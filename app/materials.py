"""Materials Studio workflow, with immutable artifact and evidence snapshots."""
import json
import re

from . import jd, layout
from .imports import clean
from .review import fact_view, requires_review
from .storage import audit, normalized

FILES = {
    'Resume.docx','Resume.pdf','Cover_Letter.docx','Cover_Letter.pdf',
    'Supplemental_Responses.docx','Material_Validation.json',
}
VARIANT_LABELS = {
    'Backend': {'backend','back end'},
    'Application Developer': {'application developer','application','app developer'},
    'Data Analyst': {'data analyst','analyst','data'},
    'IT Support': {'it support','support','help desk'},
    'AI Engineer': {'ai engineer','ai','machine learning'},
}
SECTIONS = {'experience':'Professional Experience','resume':'Professional Experience',
            'project':'Projects','education':'Education','skill':'Skills'}


class MaterialError(ValueError):
    """A safe workflow error that can be shown in the Studio."""


def json_text(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True)


def job_record(db,job_id):
    row = db.execute('SELECT id,title,employer,deadline FROM jobs WHERE id=?',(job_id,)).fetchone()
    if row is None:
        raise MaterialError('Choose a job from Pipeline')
    return dict(row)


def jd_record(db,entry_id,job_id):
    row = db.execute('SELECT * FROM material_jds WHERE id=? AND job_id=?',(entry_id,job_id)).fetchone()
    if row is None:
        raise MaterialError('Choose a saved JD for this job')
    return {**dict(row),'analysis':json.loads(row['analysis'])}


def save_jd(db,payload):
    job = job_record(db,payload['job_id'])
    source,body = jd.input_text(payload)
    analysis = jd.parse(body,job)
    db.execute('BEGIN IMMEDIATE')
    old = db.execute('SELECT id FROM material_jds WHERE job_id=? AND source=? AND body=? AND analysis=?',
                     (job['id'],source,body,json_text(analysis))).fetchone()
    if old:
        return jd_record(db,old['id'],job['id'])
    entry = db.execute('INSERT INTO material_jds(job_id,source,body,analysis) VALUES(?,?,?,?)',
                       (job['id'],source,body,json_text(analysis))).lastrowid
    audit(db,'jd_saved','job',job['id'])
    return jd_record(db,entry,job['id'])


def verified_facts(db):
    return [fact for fact in fact_view(db) if fact['verification_status']=='verified'
            and fact['last_verified_at'] and fact['sources']]


CONNECTORS = set('a an the and with via using for to in on by of from as'.split())
QUALIFIERS = re.compile(r'\b(?:not|no|never|only|unless|except|prototype|simulated|example|estimated|'
                        r'approximately|training|coursework|assisted|helped|supported|rather than)\b',re.I)


def guarded_variant(original, variant):
    def claims(text):
        return {word.strip('.') for word in re.findall(r'[\w+#.%]+',normalized(text))}-CONNECTORS-{''}
    source = QUALIFIERS.findall(normalized(original))
    target = QUALIFIERS.findall(normalized(variant))
    numbers = r'\b\d[\d,.%]*'
    return (claims(variant)<=claims(original) and sorted(source)==sorted(target)
            and sorted(re.findall(numbers,original.rstrip('.')))==sorted(re.findall(numbers,variant.rstrip('.'))))


def options(fact):
    original = {'variant_id':None,'label':'Original fact','version':None,'wording':fact['statement']}
    variants = [item for item in fact['variants'] if guarded_variant(fact['statement'],item['wording'])]
    return [original,*variants]


def role_value(value):
    if value not in jd.ROLE_LABELS:
        raise MaterialError('Choose a job type')
    return value


def recommendations(db,payload):
    job_id = job_record(db,payload['job_id'])['id']
    entry = jd_record(db,payload['jd_id'],job_id)
    role = role_value(payload.get('role') or entry['analysis']['role_suggestion'] or 'Application Developer')
    facts = verified_facts(db)
    matches = jd.evidence(entry['analysis'],facts)
    weights = {record['id']: (4 if record['level']=='required' else 1) for record in entry['analysis']['requirements']}
    scored = {}
    for match in matches:
        for fact_id in match['fact_ids']:
            scored[fact_id] = scored.get(fact_id,0)+weights[match['requirement_id']]
    result = []
    for fact in facts:
        choices = options(fact)
        labels = VARIANT_LABELS[role]
        preferred = next((item for item in choices if normalized(item['label']) in labels),choices[0])
        role_text = ' '.join((fact['subject'], fact['statement'],preferred['wording']))
        score = scored.get(fact['id'],0)+jd.role_scores(role_text)[role]+(5 if preferred['variant_id'] else 0)
        result.append({'fact_id':fact['id'],'subject':fact['subject'],'kind':fact['kind'],
                       'statement':fact['statement'],'sources':fact['sources'],'options':choices,
                       'preferred_variant':preferred['variant_id'],'score':score})
    result.sort(key=lambda row:(-row['score'],row['fact_id']))
    return {'role':role,'candidates':result,'matches':matches,
            'eligibility_checks':jd.independent_checks(entry['analysis'],facts,matches)}


def template(db):
    row = db.execute('SELECT * FROM material_templates ORDER BY id DESC LIMIT 1').fetchone()
    if row:
        return {'id':row['id'],'settings':json.loads(row['settings'])}
    return {'id':None,'settings':layout.DEFAULT_TEMPLATE.copy()}


def save_template(db,payload):
    value = layout.settings(payload['settings'])
    db.execute('BEGIN IMMEDIATE')
    current = template(db)
    if current['id'] is not None and current['settings']==value:
        return current
    entry = db.execute('INSERT INTO material_templates(settings) VALUES(?)',(json_text(value),)).lastrowid
    audit(db,'template_saved','material_template',entry)
    return {'id':entry,'settings':value}


def safe_sentence_edit(original,edited):
    collapse = lambda value: ' '.join(value.split())
    original,edited = collapse(original),collapse(edited)
    if original==edited:
        return True
    pieces = re.split(r'(?<=[.!?])\s+(?=[A-Z])',original)
    # Restrict shortening to whole leading sentences and retain later qualifications.
    # A user can also choose another independently reviewed wording.
    qualifiers = re.compile(r'\b(?:not|no|never|only|unless|except|however|limited|prototype|simulated|'
                            r'example|estimated|approximately|training|coursework|assisted|rather than)\b',re.I)
    return any(edited==' '.join(pieces[:length]) and not qualifiers.search(' '.join(pieces[length:]))
               and not re.search(r'\d', ' '.join(pieces[length:]))
               for length in range(1,len(pieces)))


def chosen_bullets(db,requested):
    if not isinstance(requested,list) or not 1<=len(requested)<=50:
        raise MaterialError('Select 1 to 50 verified facts')
    facts = {row['id']:row for row in verified_facts(db)}
    seen,bullets = set(),[]
    for choice in requested:
        fact_id = choice['fact_id']
        fact = facts.get(fact_id)
        if not fact or fact_id in seen or type(fact_id) is not int:
            raise MaterialError('Each selected fact must be verified and unique')
        seen.add(fact_id)
        variant = choice.get('variant_id')
        if variant is not None and type(variant) is not int:
            raise MaterialError('Invalid bullet version')
        item = next((option for option in options(fact) if option['variant_id']==variant),None)
        if item is None:
            raise MaterialError('Bullet version does not belong to this fact')
        wording = clean(choice.get('text',item['wording']),600)
        if not wording or not safe_sentence_edit(item['wording'],wording):
            raise MaterialError('Edits may only retain verified wording or shorten it to complete leading sentences')
        bullets.append({'fact_id':fact_id,'original_fact':fact['statement'],'kind':fact['kind'],
                        'subject':fact['subject'],'verified_at':fact['last_verified_at'],'revision':fact['revision'],
                        'source_id':fact['source_id'],'sources':fact['sources'],
                        'variant_id':variant,'variant_version':item['version'],'variant_label':item['label'],
                        'original_wording':item['wording'],'text':wording,'edited':wording!=item['wording']})
    return bullets


def resume_groups(candidate,bullets):
    paragraph = layout.Paragraph
    groups = [[paragraph(candidate,'name')]]
    by_section = {}
    for bullet in bullets:
        section = SECTIONS.get(bullet['kind'],'Professional Experience')
        by_section.setdefault(section,{}).setdefault(bullet['subject'],[]).append(bullet['text'])
    for section,subjects in by_section.items():
        pending = [paragraph(section.upper(),'section')]
        for subject,lines in subjects.items():
            # Dates are printed only if present as explicit, selected factual wording.
            date_pattern = r'Dates:\s*((?:[A-Za-z]{3,9}\s+)?\d{4}\s*[-–]\s*(?:(?:[A-Za-z]{3,9}\s+)?\d{4}|Present))'
            dated = [re.fullmatch(date_pattern,line,re.I) for line in lines]
            date = next((match.group(1) for match in dated if match), '')
            remaining = [line for line,match in zip(lines,dated) if not match] if date else lines
            distinct_dates = {match.group(1) for match in dated if match}
            if len(distinct_dates)>1 or not remaining:
                date,remaining = '',lines
            groups.append([*pending,paragraph(subject,'subject',date),paragraph(remaining[0],'bullet')])
            pending = []
            groups.extend([[paragraph(value,'bullet')] for value in remaining[1:]])
    return groups


def cover_groups(candidate,job,bullets):
    p = layout.Paragraph
    return [[p(candidate,'name'),p(job['employer'],'meta'),p('Re: '+job['title'],'subject'),p('Dear Hiring Committee,')],
            [p('I am applying for the '+job['title']+' position at '+job['employer']+'.')],
            *[[p(bullet['text'],'bullet')] for bullet in bullets[:3]],
            [p('Thank you for considering my application.'),p('Sincerely,'),p(candidate)]]


def supplemental_groups(analysis,responses,bullets):
    if not isinstance(responses,dict):
        raise MaterialError('Invalid supplemental responses')
    questions = [item for item in analysis['requirements'] if item['category']=='question']
    if set(responses)-{str(question['id']) for question in questions}:
        raise MaterialError('Supplemental question is no longer present')
    supported = {bullet['fact_id']:bullet['text'] for bullet in bullets}
    groups, pending = [],[]
    p = layout.Paragraph
    for question in questions:
        key = str(question['id'])
        chosen = responses.get(key)
        if chosen is None:
            pending.append(question['id'])
            continue
        if (type(chosen) is not int or chosen not in supported
                or requires_review(normalized(question['text'])) or jd.VISA.search(question['text']) or jd.REFERENCES.search(question['text'])
                or re.search(r'\b(?:citizen|nationality|race|ethnicity|disability|veteran|salary|criminal)\b',question['text'],re.I)):
            raise MaterialError('Question requires manual review or a selected fact')
        groups.append([p(question['text'],'section'),p(supported[chosen])])
    return groups,pending


def version_record(db,version_id):
    row = db.execute('SELECT * FROM material_versions WHERE id=?',(version_id,)).fetchone()
    if not row:
        raise MaterialError('Materials version not found')
    files = db.execute('SELECT name FROM material_files WHERE version_id=? ORDER BY name',(version_id,))
    return {**dict(row),'snapshot':json.loads(row['snapshot']),'files':[item['name'] for item in files]}


def list_job(db,job_id):
    job_record(db,job_id)
    entries = db.execute('SELECT id,source,created_at FROM material_jds WHERE job_id=? ORDER BY id DESC',(job_id,))
    versions = db.execute('SELECT id,version,approved_at,parent_id,created_at FROM material_versions WHERE job_id=? ORDER BY version DESC',(job_id,))
    final = db.execute('SELECT version_id FROM material_final WHERE job_id=?',(job_id,)).fetchone()
    return {'jds':[dict(item) for item in entries],'versions':[dict(item) for item in versions],
            'final':final['version_id'] if final else None}


def generate(db,payload):
    job = job_record(db,payload['job_id'])
    entry = jd_record(db,payload['jd_id'],job['id'])
    analysis = entry['analysis']
    labels = {key:analysis['fields'][key]['value'] for key in ('title','employer')}
    if not all(labels.values()):
        raise MaterialError('Resolve the job title or employer discrepancy before generating')
    role = role_value(payload['role'])
    candidate = clean(payload['candidate'],100)
    if not candidate or any(ord(char)<32 for char in candidate):
        raise MaterialError('Enter the candidate name')
    bullets = chosen_bullets(db,payload['bullets'])
    responses = payload.get('responses',{})
    parent = payload.get('parent_id')
    if parent is not None and version_record(db,parent)['job_id']!=job['id']:
        raise MaterialError('Original materials version belongs to a different job')
    active_template = template(db)
    rules = active_template['settings']
    try:
        resume_docx,resume_pdf,resume_report = layout.render(resume_groups(candidate,bullets),rules,'Resume')
        cover_docx,cover_pdf,cover_report = layout.render(cover_groups(candidate,labels,bullets),rules,'Cover Letter')
        groups,pending = supplemental_groups(analysis,responses,bullets)
        supplemental = layout.render([[layout.Paragraph('Supplemental Responses','name')],*groups],
                                     rules,'Supplemental Responses',make_pdf=False)[0] if groups else None
    except ValueError as exc:
        raise MaterialError(str(exc)) from exc
    matches = jd.evidence(analysis,verified_facts(db))
    report = {'resume':resume_report,'cover_letter':cover_report,'eligibility':matches,
              'pending_supplemental_questions':pending,'template':rules,
              'separate_checks':jd.independent_checks(analysis,verified_facts(db),matches),'bullets':bullets,'supplemental_sources':responses}
    snapshot = {'candidate':candidate,'role':role,'job':labels,'bullets':bullets,'responses':responses,
                'analysis':analysis,'validation':report}
    artifacts = {'Resume.docx':resume_docx,'Resume.pdf':resume_pdf,'Cover_Letter.docx':cover_docx,
                 'Cover_Letter.pdf':cover_pdf,'Material_Validation.json':json_text(report).encode()}
    if supplemental is not None:
        artifacts['Supplemental_Responses.docx'] = supplemental
    db.execute('BEGIN IMMEDIATE')
    # Do not finalize bytes rendered from evidence that changed during rendering.
    if chosen_bullets(db,payload['bullets'])!=bullets:
        raise MaterialError('Verified evidence changed. Generate again.')
    if active_template['id'] is None:
        current = template(db)
        if current['id'] is None:
            tid = db.execute('INSERT INTO material_templates(settings) VALUES(?)',(json_text(rules),)).lastrowid
        elif current['settings']==rules:
            tid = current['id']
        else:
            raise MaterialError('Template changed. Generate again.')
    else:
        tid = active_template['id']
    number = db.execute('SELECT COALESCE(MAX(version),0)+1 FROM material_versions WHERE job_id=?',(job['id'],)).fetchone()[0]
    entry_id = db.execute('INSERT INTO material_versions(job_id,jd_id,version,parent_id,template_id,snapshot) VALUES(?,?,?,?,?,?)',
                          (job['id'],entry['id'],number,parent,tid,json_text(snapshot))).lastrowid
    db.executemany('INSERT INTO material_files VALUES(?,?,?)',((entry_id,key,value) for key,value in artifacts.items()))
    audit(db,'materials_generated','job',job['id'],str(number))
    return version_record(db,entry_id)


def approve(db,payload):
    db.execute('BEGIN IMMEDIATE')
    record = version_record(db,payload['version_id'])
    if record['approved_at']:
        return record
    snapshot = record['snapshot']
    report = snapshot['validation']
    if not report['resume']['passed'] or not report['cover_letter']['passed'] or report['pending_supplemental_questions']:
        raise MaterialError('Resolve PDF validation or unanswered supplemental questions before approving')
    current = chosen_bullets(db,snapshot['bullets'])
    for old,new in zip(snapshot['bullets'],current):
        if old['original_wording']!=new['original_wording'] or old['original_fact']!=new['original_fact']:
            raise MaterialError('Fact wording changed. Generate again before approving.')
    db.execute('UPDATE material_versions SET approved_at=CURRENT_TIMESTAMP WHERE id=?',(record['id'],))
    db.execute('INSERT INTO material_final VALUES(?,?) ON CONFLICT(job_id) DO UPDATE SET version_id=excluded.version_id',
               (record['job_id'],record['id']))
    audit(db,'materials_approved','job',record['job_id'],str(record['version']))
    return version_record(db,record['id'])


def artifact(db,version_id,name):
    if name not in FILES:
        return None
    row = db.execute('SELECT content FROM material_files WHERE version_id=? AND name=?',(version_id,name)).fetchone()
    return row['content'] if row else None

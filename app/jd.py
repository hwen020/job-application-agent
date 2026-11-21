"""Conservative extraction of quoted job requirements and evidence checks."""
import base64
import re
from pathlib import PurePath

from .imports import clean, extract
from .storage import normalized

ROLE_LABELS = {
    'Backend': ('backend','back end','api','service','database','server'),
    'Application Developer': ('application developer','application','workflow','integration','software'),
    'Data Analyst': ('data analyst','analytics','dashboard','report','data','sql'),
    'IT Support': ('it support','help desk','desktop','troubleshoot','ticket','support'),
    'AI Engineer': ('ai engineer','machine learning','inference','model','evaluation','ai'),
}
LABELS = {
    'title': ('job title','position title','position name','title','position'),
    'employer': ('company','company name','employer','organization','institution','agency'),
    'location': ('location','work location','job location'),
    'deadline': ('deadline','closing date','application deadline','apply by'),
}
HEADINGS = {
    'required': ('minimum qualifications','required qualifications','requirements','required skills','basic qualifications',
                 'minimum requirements','must have','essential qualifications'),
    'preferred': ('preferred qualifications','preferred skills','preferred experience','desirable qualifications','nice to have'),
    'responsibility': ('responsibilities','duties','key responsibilities','what you will do'),
    'question': ('supplemental questions','application questions','supplemental responses'),
    'neutral': ('about us','benefits','salary','how to apply','equal opportunity','about the role','job description','overview'),
    'education': ('education','minimum education','minimum degree'),
    'experience': ('experience','work experience','minimum experience','years of experience'),
    'visa': ('visa','visa sponsorship','sponsorship','work authorization'),
    'reference': ('references','reference requirements','professional references'),
}
YEARS = re.compile(r'\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s*(?:\+\s*|[-–]\s*\d+\s*)?years?\b',re.I)
NUMBER_WORDS = 'zero one two three four five six seven eight nine ten'.split()
EDUCATION = re.compile(r"\b(?:degree|bachelor|master|doctorate|ph\.?d\.?|high school|diploma|associate degree)",re.I)
VISA = re.compile(r'\b(?:visa|sponsor\w*|work authori[sz]ation|work permit)\b',re.I)
REFERENCES = re.compile(r'\b(?:professional references?|reference letters?|references? (?:are required|upon request|contact|must))\b',re.I)
RELATED = re.compile(r'\b(?:related|relevant|equivalent|comparable)\b|\b(?:experience in|years? of .+? experience)\b',re.I)


def input_text(payload):
    upload = payload.get('file')
    pasted = clean(payload.get('text',''),70_000)
    if upload and pasted:
        raise ValueError('Choose pasted text or one file')
    if upload:
        name = clean(upload['name'],240)
        if not name or re.search(r'[/\\:]',name):
            raise ValueError('Invalid JD file name')
        raw = base64.b64decode(upload['content'],validate=True)
        if not 0 < len(raw) <= 4_000_000:
            raise ValueError('JD file must be at most 4 MB')
        pasted = clean('\n'.join(extract(raw,PurePath(name).suffix.lower())),70_000)
        return name,pasted
    return 'Pasted JD',pasted


def explicit_level(text):
    if re.search(r'\b(?:not (?:required|mandatory|necessary)|no (?:minimum|requirement)|need not)\b',text,re.I):
        return 'not_required'
    if re.search(r'\b(?:preferred|desirable|nice to have|a plus|optional)\b',text,re.I):
        return 'preferred'
    if re.search(r'\b(?:must|required|minimum|at least|mandatory)\b',text,re.I):
        return 'required'
    return 'review'


def role_scores(text):
    words = re.sub(r'[^a-z0-9]+',' ',normalized(text)).strip()
    return {role:sum(bool(re.search(r'\b'+re.escape(word)+r'\b',words)) for word in keywords)
            for role,keywords in ROLE_LABELS.items()}


def parse(body,job=None):
    fields, items = {},[]
    heading = 'neutral'
    heading_name = ''
    category_scope = 'review'
    for n,raw in enumerate(body.splitlines(),1):
        line = re.sub(r'^\s*(?:[-*•]+|\d+[.)])\s+','',raw.strip()).strip()
        if not line:
            continue
        key, sep, value = line.partition(':')
        if not sep and ' | ' in line:
            key, sep, value = line.partition(' | ')
        key = normalized(key).strip('# *')
        for field, aliases in LABELS.items():
            if sep and key in aliases and value.strip():
                fields.setdefault(field,[]).append({'text':value.strip(),'line':n,'source':'JD'})
        if sep and any(key in aliases for aliases in LABELS.values()):
            continue
        found = next((k for k,aliases in HEADINGS.items() if key in aliases),None)
        if found:
            if found in ('education','experience','visa','reference'):
                if heading in ('required','preferred'):
                    category_scope = heading
                if key.startswith('minimum '):
                    category_scope = 'required'
            else:
                category_scope = 'review'
            heading = found
            heading_name = key
            line = value.strip()
            if not line:
                continue
        if heading=='question' or (line.endswith('?') and heading not in ('preferred','required')):
            category = 'question'
        elif VISA.search(line) or heading=='visa':
            category = 'visa'
        elif REFERENCES.search(line) or heading=='reference':
            category = 'reference'
        elif heading=='responsibility':
            category = 'responsibility'
        elif EDUCATION.search(line) or heading=='education':
            category = 'education'
        elif heading_name in ('required skills','preferred skills'):
            category = 'skill'
        elif YEARS.search(line) or heading=='experience' or re.search(r'\bexperience (?:with|in)\b',line,re.I):
            category = 'experience'
        elif heading in ('required','preferred') or explicit_level(line)!='review':
            category = 'skill' if heading in ('required','preferred') and len(line.split())<=4 else 'qualification'
        else:
            continue
        level = explicit_level(line)
        if level=='review':
            if heading in ('required','preferred'):
                level = heading
            elif heading in ('education','experience','visa','reference'):
                level = category_scope
        elif level=='not_required':
            level = 'review'
        item = {'id':len(items)+1,'category':category,'level':level,'line':n,'text':line}
        if category=='experience':
            years = YEARS.search(line)
            if years:
                item['years_mentioned'] = year_value(years)
            item['relatedness_check'] = bool(RELATED.search(line))
        if category=='visa':
            item['future_sponsorship'] = 'explicitly_mentioned' if re.search(r'\b(?:future|now or)\b',line,re.I) else 'review'
        skills = lists_from_skills(line) if category=='skill' and heading_name in ('required skills','preferred skills') else [line]
        for skill in skills:
            items.append({**item,'id':len(items)+1,'text':skill,'excerpt':line})
    for field in LABELS:
        candidates = fields.get(field,[])
        pipeline_value = (job[field] or '') if job is not None and field in ('title','employer','deadline') else ''
        if pipeline_value:
            candidates.append({'text':pipeline_value,'line':None,'source':'Pipeline'})
        unique = {normalized(c['text']) for c in candidates}
        fields[field] = {'state':'extracted' if len(unique)==1 else 'review',
                         'value':candidates[0]['text'] if len(unique)==1 else '', 'evidence':candidates}
    title = fields['title']['value']
    scores = role_scores(title)
    role = max(scores,key=scores.get) if title and max(scores.values()) else None
    return {'fields':fields,'requirements':items,'role_suggestion':role,
            'separate_checks': {
                'experience_years': 'review',
                'field_relatedness': 'review',
                'future_sponsorship': 'review',
            },
            'missing_categories':[name for name in ('education','experience','visa','reference')
                                  if not any(item['category']==name for item in items)]}


def evidence(analysis,facts):
    matches = []
    for item in analysis['requirements']:
        phrase = normalized(item['text']).strip(' .;')
        candidates = []
        if item['category'] in ('skill','qualification') and phrase and len(phrase)<=80:
            pattern = r'(?<!\w)'+re.escape(phrase)+r'(?!\w)'
            for fact in facts:
                phrases = re.split(r'[.!?]\s+',normalized(fact['statement']))
                if any(re.search(pattern,part) and not re.search(
                        r'\b(?:not|no|never|without|unfamiliar)\b',part) for part in phrases):
                    candidates.append(fact['id'])
        elif item['category']=='education':
            words = ('bachelor','master','doctorate','phd','associate','diploma','high school')
            degree = next((word for word in words if word in re.sub(r'ph\.?d\.?','phd',phrase)),None)
            if degree:
                candidates = [f['id'] for f in facts if f['kind']=='education'
                              and degree in re.sub(r'ph\.?d\.?','phd',normalized(f['statement']))]
        elif item['category']=='experience':
            terms = re.search(r'\bexperience (?:in|with) ([a-z0-9][a-z0-9 +#.-]{1,55})',phrase)
            if terms:
                candidates = [f['id'] for f in facts if f['kind'] in ('experience','project','resume') and re.search(
                    r'(?<!\w)'+re.escape(terms.group(1).strip(' .'))+r'(?!\w)',normalized(f['statement']))]
        # A phrase match locates evidence. Eligibility remains a human determination.
        manual = item['category'] in ('visa','reference','question','responsibility') or item['level']=='review'
        state = 'candidate_evidence' if candidates else 'review' if manual else 'missing_evidence'
        matches.append({'requirement_id':item['id'],'state':state,'fact_ids':candidates})
    return matches


def year_value(match):
    amount = match.group(1).lower()
    return int(amount) if amount.isdigit() else NUMBER_WORDS.index(amount)


def lists_from_skills(line):
    values = [part.strip() for part in re.split(r'[,;]',line)]
    if len(values)>1 and all(re.fullmatch(r'[A-Za-z0-9][\w .+#/-]{0,36}',part) for part in values):
        return values
    return [line]


def independent_checks(analysis,facts,matches):
    indexed = {match['requirement_id']:match for match in matches}
    years, field, visa = [],[],[]
    for item in analysis['requirements']:
        if item['category']=='experience':
            match = indexed[item['id']]
            if 'years_mentioned' in item:
                supported = []
                for fact in facts:
                    explicit = re.search(r'\b(?:experience|duration)\s*:',fact['statement'],re.I)
                    amount = YEARS.search(fact['statement'])
                    if explicit and amount:
                        supported.append({'fact_id':fact['id'],'years_mentioned':year_value(amount),
                                          'quote':fact['statement']})
                years.append({'requirement_id':item['id'],'years_mentioned':item['years_mentioned'],
                              'candidate_durations':supported})
            if item.get('relatedness_check'):
                field.append({'requirement_id':item['id'],'candidate_fact_ids':match['fact_ids']})
        if item['category']=='education' and RELATED.search(item['text']):
            field.append({'requirement_id':item['id'],'candidate_fact_ids':indexed[item['id']]['fact_ids']})
        if item['category']=='visa' or item['category']=='question' and VISA.search(item['text']):
            visa.append({'requirement_id':item['id'],'future_language':bool(re.search(r'\b(?:future|now or)\b',item['text'],re.I)),
                         'quote':item['text']})
    return {'experience_years':{'state':'review','requirements':years},
            'field_relatedness':{'state':'review','requirements':field},
            'future_sponsorship':{'state':'review','requirements':visa}}

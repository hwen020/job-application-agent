"""Phase 1 review operations."""
from datetime import date, datetime, timezone

from .imports import clean
from .storage import audit, normalized


def fact_view(db):
    facts = [dict(row) for row in db.execute('SELECT * FROM facts ORDER BY id DESC')]
    for fact in facts:
        fact['sources'] = [dict(row) for row in db.execute(
            'SELECT s.id,s.name,fs.locator FROM fact_sources fs JOIN source_documents s ON s.id=fs.source_id WHERE fact_id=?',
            (fact['id'],))]
        fact['variants'] = [dict(row) for row in db.execute(
            'SELECT id AS variant_id,label,wording,version FROM fact_variants WHERE fact_id=? ORDER BY version DESC',(fact['id'],))]
    return facts


def review_fact(db,p):
    db.execute('BEGIN IMMEDIATE')
    fact = db.execute('SELECT * FROM facts WHERE id=?',(p['id'],)).fetchone()
    if not fact or fact['revision']!=p['revision']:
        raise ValueError('Fact changed. Reload before reviewing.')
    status = p.get('status',fact['verification_status'])
    if status not in ('unverified','verified','disputed'):
        raise ValueError('Invalid verification status')
    if 'wording' in p:
        if p.get('same_fact') is not True or fact['verification_status']!='verified' or status!='verified':
            raise ValueError('Confirm that the wording preserves a verified fact')
        label, wording = normalized(clean(p['label'],100)),clean(p['wording'])
        if not label or not wording:
            raise ValueError('Label and wording are required')
        last = db.execute('SELECT version,wording FROM fact_variants WHERE fact_id=? AND label=? ORDER BY version DESC LIMIT 1',
                          (fact['id'],label)).fetchone()
        if not last or last['wording']!=wording:
            db.execute('INSERT INTO fact_variants(fact_id,label,wording,version,source_id) VALUES(?,?,?,?,?)',
                       (fact['id'],label,wording,last['version']+1 if last else 1,fact['source_id']))
    db.execute('UPDATE facts SET revision=revision+1,verification_status=?,last_verified_at=CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE last_verified_at END WHERE id=?',
               (status,'status' in p and status=='verified',fact['id']))
    audit(db,'fact_reviewed','fact',fact['id'],status)
    return {'ok':True}


def rule_fields(payload):
    question = normalized(clean(payload['question'],500))
    platform = normalized(clean(payload.get('platform',''),80))
    employer = normalized(clean(payload.get('employer',''),200))
    if not question:
        raise ValueError('Question is required')
    return question,platform,employer


def requires_review(question):
    return any(word in question for word in (
        'authoriz','sponsor','visa','citizen','nationality','race','ethnicity','disabilit','veteran',
        'worked for','relative','工作授权','担保','签证','国籍','种族','族裔','身份','亲属'))


def create_rule(db,p):
    question,platform,employer = rule_fields(p)
    answer = clean(p['answer'])
    scope = p['scope']
    if p.get('confirmed') is not True or not answer or scope not in ('employer','platform','global','dynamic'):
        raise ValueError('An explicitly confirmed answer and scope are required')
    if scope=='employer' and not employer or scope=='platform' and not platform or scope=='global' and (platform or employer):
        raise ValueError('Scope does not match the supplied context')
    sensitive = requires_review(question)
    if sensitive and scope!='dynamic':
        raise ValueError('Sensitive questions require a dynamic review rule')
    if sensitive:
        raise ValueError('Sensitive answers require manual review')
    expiry = clean(p.get('valid_until',''),10)
    if expiry and date.fromisoformat(expiry).isoformat()!=expiry:
        raise ValueError('Invalid expiry date')
    if scope=='dynamic' and not expiry:
        raise ValueError('Dynamic rules require an expiry date')
    db.execute('BEGIN IMMEDIATE')
    latest = db.execute('SELECT * FROM answer_rules WHERE question=? AND scope=? AND platform=? AND employer=? ORDER BY version DESC LIMIT 1',
                        (question,scope,platform,employer)).fetchone()
    if latest and latest['answer']==answer and (latest['valid_until'] or '')==expiry:
        return {'id':latest['id']}
    rule = db.execute('INSERT INTO answer_rules(question,answer,scope,platform,employer,valid_until,version) VALUES(?,?,?,?,?,?,?)',
                      (question,answer,scope,platform,employer,expiry or None,latest['version']+1 if latest else 1)).lastrowid
    db.execute('INSERT INTO rule_confirmations(rule_id) VALUES(?)',(rule,))
    audit(db,'rule_confirmed','rule',rule)
    return {'id':rule}


def match_rule(db,p):
    fields = rule_fields(p)
    question, platform, employer = fields
    if requires_review(question):
        return {'state':'review'}
    rows = db.execute("""
        SELECT r.* FROM answer_rules r JOIN rule_confirmations c ON c.rule_id=r.id
        WHERE r.question=? AND r.sensitive=0 AND r.scope!='dynamic'
          AND (r.platform='' OR r.platform=?) AND (r.employer='' OR r.employer=?)
          AND (r.valid_until IS NULL OR r.valid_until>=?)
          AND NOT EXISTS (SELECT 1 FROM answer_rules n WHERE n.question=r.question AND n.scope=r.scope
            AND n.platform=r.platform AND n.employer=r.employer AND n.version>r.version)
        ORDER BY (r.employer!='')*2+(r.platform!='') DESC
        """,fields+(datetime.now(timezone.utc).date().isoformat(),)).fetchall()
    return {'state':'matched','rule':dict(rows[0])} if rows else {'state':'review'}


def link_document(db,p):
    db.execute('BEGIN IMMEDIATE')
    job = db.execute('SELECT id,status FROM jobs WHERE id=?',(p['job_id'],)).fetchone()
    if not job:
        raise ValueError('Job not found')
    db.execute('INSERT OR IGNORE INTO applications(job_id,status) VALUES(?,?)',(job['id'],job['status']))
    application = db.execute('SELECT id FROM applications WHERE job_id=?',(job['id'],)).fetchone()[0]
    cur = db.execute('INSERT OR IGNORE INTO application_documents VALUES(?,?)',(application,p['resume_version_id']))
    if cur.rowcount:
        audit(db,'material_linked','application',application)
    return {'id':application}

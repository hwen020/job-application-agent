"""Local snapshot comparison and three-way merge.

The Google transport can read the original sheet. Writes target a distinct
staging sheet and should use a revision precondition.
"""
import json
from datetime import date
from urllib.parse import parse_qsl, urlsplit

from .imports import clean, token
from .storage import audit, employer_id, job_identity

STATUSES = ('To review','Materials needed','Ready for review','Submitted','Excluded','Paused')
FIELDS = ('employer','title','platform','priority','status','deadline','exclusion_reason','url')
LIMITS = (200,200,80,2,40,10,1000,1000)


def job_values(data):
    if not isinstance(data,dict) or set(data)-set(FIELDS):
        raise ValueError('Unknown job fields')
    defaults = {'priority':'P1','status':'To review'}
    row = {key:clean(data.get(key,defaults.get(key,'')),limit) for key,limit in zip(FIELDS,LIMITS)}
    if not row['employer'] or not row['title'] or row['priority'] not in ('P0','P1','P2','P3') or row['status'] not in STATUSES:
        raise ValueError('Invalid employer, title, priority or status')
    if row['deadline'] and date.fromisoformat(row['deadline']).isoformat()!=row['deadline']:
        raise ValueError('Invalid deadline')
    if row['url']:
        url = urlsplit(row['url'])
        if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or any(
                any(word in key.casefold() for word in ('token','password','secret','apikey','api_key','session','credential','signature'))
                for key,_ in parse_qsl(url.query,keep_blank_values=True)):
            raise ValueError('Enter a public HTTP or HTTPS job URL')
    return row


def snapshot(db):
    return {row['identity']: {key:row[key] or '' for key in FIELDS}
            for row in db.execute('SELECT * FROM jobs WHERE identity IS NOT NULL')}


def upsert(db,row):
    key = job_identity(row)
    values = [row[field] for field in FIELDS] + [employer_id(db,row['employer']),key]
    existing = db.execute('SELECT id FROM jobs WHERE identity=?',(key,)).fetchone()
    if existing:
        db.execute('UPDATE jobs SET '+','.join(field+'=?' for field in FIELDS)+',employer_id=? WHERE identity=?',values)
        job_id = existing['id']
    else:
        job_id = db.execute('INSERT INTO jobs('+','.join(FIELDS)+',employer_id,identity) VALUES(?,?,?,?,?,?,?,?,?,?)',values).lastrowid
    return job_id


def sync_plan(db, rows):
    if not isinstance(rows,list) or len(rows)>2000:
        raise ValueError('Supply up to 2000 job rows')
    remote, duplicate = {}, 0
    for item in rows:
        row = job_values(item)
        key = job_identity(row)
        if key in remote:
            if remote[key]!=row:
                raise ValueError('Incoming rows share an identity with differing values')
            duplicate += 1
        remote[key] = row
    local = snapshot(db)
    baseline = {r['identity']:json.loads(r['snapshot']) for r in db.execute('SELECT * FROM sync_baseline')}
    merged, conflicts = {}, []
    for key in sorted(local.keys() | remote.keys()):
        left, right, old = local.get(key),remote.get(key),baseline.get(key)
        if left is None or right is None:
            merged[key] = left or right
            continue
        row = dict(left)
        for field in FIELDS:
            if left[field]==right[field] or old is not None and right[field]==old[field]:
                continue
            if old is not None and left[field]==old[field]:
                row[field] = right[field]
            else:
                conflicts.append({'job':left['title'],'field':field,'local':left[field],'incoming':right[field]})
        merged[key] = row
    for key,row in merged.items():
        if row['status']=='Submitted' and local.get(key,{}).get('status')!='Submitted':
            conflicts.append({'job':row['title'],'field':'status','local':local.get(key,{}).get('status',''),
                              'incoming':'Submitted (record manually in Pipeline)'})
    changes = [{'before':local.get(key),'after':row} for key,row in merged.items() if local.get(key)!=row]
    return {'local':local,'baseline':baseline,'staging':merged,'conflicts':conflicts,
            'duplicates':duplicate,'changes':changes}


def save_baseline(db, rows):
    db.execute('DELETE FROM sync_baseline')
    db.executemany('INSERT INTO sync_baseline VALUES(?,?)',((k,json.dumps(v)) for k,v in rows.items()))


def apply(db, payload):
    db.execute('BEGIN IMMEDIATE')
    plan = sync_plan(db,payload.get('rows'))
    if plan['conflicts'] or payload.get('preview')!=token(plan):
        raise ValueError('Resolve conflicts or refresh the sync preview')
    before = {'jobs':plan['local'],'baseline':plan['baseline']}
    after = plan['staging']
    for key,row in after.items():
        if before['jobs'].get(key)!=row:
            job_id = upsert(db,row)
            db.execute('UPDATE applications SET status=? WHERE job_id=?',(row['status'],job_id))
    save_baseline(db,after)
    run = db.execute('INSERT INTO sync_runs(before_state,after_state) VALUES(?,?)',(json.dumps(before),json.dumps(after))).lastrowid
    audit(db,'sync_confirmed','integration',run)
    return {'run':run,'staging':list(after.values())}


def rollback(db, run):
    db.execute('BEGIN IMMEDIATE')
    row = db.execute('SELECT * FROM sync_runs WHERE rolled_back=0 ORDER BY id DESC LIMIT 1').fetchone()
    if row is None or type(run) is not int or row['id']!=run:
        raise ValueError('Only the latest active sync can be rolled back')
    before, after = json.loads(row['before_state']),json.loads(row['after_state'])
    local = snapshot(db)
    if local!=after:
        raise ValueError('Local jobs changed after this sync')
    for key in after.keys()-before['jobs'].keys():
        db.execute('DELETE FROM jobs WHERE identity=?',(key,))
    for key,job in before['jobs'].items():
        if after.get(key)!=job:
            job_id = upsert(db,job)
            db.execute('UPDATE applications SET status=? WHERE job_id=?',(job['status'],job_id))
    save_baseline(db,before['baseline'])
    db.execute('UPDATE sync_runs SET rolled_back=1 WHERE id=?',(run,))
    audit(db,'sync_rolled_back','integration',run)
    return {'staging':list(before['baseline'].values())}

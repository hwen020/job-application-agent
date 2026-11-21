"""Versioned, transactional upgrades for the existing demo database."""
import hashlib
import json
import sqlite3
import unicodedata


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


SCHEMA = """
CREATE TABLE source_documents(id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
 sha256 TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE resume_versions(id INTEGER PRIMARY KEY, series TEXT NOT NULL, version INTEGER NOT NULL,
 source_id INTEGER NOT NULL UNIQUE REFERENCES source_documents, UNIQUE(series,version));
CREATE TABLE facts(id INTEGER PRIMARY KEY, identity TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
 subject TEXT NOT NULL, statement TEXT NOT NULL, source_id INTEGER NOT NULL REFERENCES source_documents,
 verification_status TEXT NOT NULL DEFAULT 'unverified' CHECK(verification_status IN ('unverified','verified','disputed')),
 last_verified_at TEXT, revision INTEGER NOT NULL DEFAULT 1);
CREATE TABLE fact_sources(fact_id INTEGER REFERENCES facts, source_id INTEGER REFERENCES source_documents,
 locator TEXT NOT NULL, PRIMARY KEY(fact_id,source_id,locator));
CREATE TABLE fact_variants(id INTEGER PRIMARY KEY, fact_id INTEGER NOT NULL REFERENCES facts,
 label TEXT NOT NULL, wording TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
 source_id INTEGER NOT NULL REFERENCES source_documents, UNIQUE(fact_id,label,version));
CREATE TABLE projects(fact_id INTEGER PRIMARY KEY REFERENCES facts);
CREATE TABLE education(fact_id INTEGER PRIMARY KEY REFERENCES facts);
CREATE TABLE skills(fact_id INTEGER PRIMARY KEY REFERENCES facts);
CREATE TABLE references_metadata(id INTEGER PRIMARY KEY, source_id INTEGER REFERENCES source_documents,
 label TEXT NOT NULL);
CREATE TABLE answer_rules(id INTEGER PRIMARY KEY, question TEXT NOT NULL, answer TEXT NOT NULL,
 scope TEXT NOT NULL, sensitive INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1,
 platform TEXT NOT NULL DEFAULT '', employer TEXT NOT NULL DEFAULT '', valid_until TEXT,
 UNIQUE(question,scope,platform,employer,version));
CREATE TABLE rule_confirmations(id INTEGER PRIMARY KEY, rule_id INTEGER NOT NULL REFERENCES answer_rules,
 confirmed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE employers(id INTEGER PRIMARY KEY, name TEXT NOT NULL, identity TEXT NOT NULL UNIQUE);
ALTER TABLE jobs ADD COLUMN employer_id INTEGER REFERENCES employers;
ALTER TABLE jobs ADD COLUMN identity TEXT;
CREATE UNIQUE INDEX jobs_identity ON jobs(identity);
CREATE TABLE applications(id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL UNIQUE REFERENCES jobs,
 status TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE application_documents(application_id INTEGER REFERENCES applications,
 resume_version_id INTEGER REFERENCES resume_versions, PRIMARY KEY(application_id,resume_version_id));
CREATE TABLE questions(id INTEGER PRIMARY KEY, application_id INTEGER REFERENCES applications,
 wording TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'review');
CREATE TABLE browser_tasks(id INTEGER PRIMARY KEY, application_id INTEGER REFERENCES applications,
 state TEXT NOT NULL DEFAULT 'planned');
CREATE TABLE task_steps(id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES browser_tasks,
 position INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'planned', UNIQUE(task_id,position));
CREATE TABLE audit_events(id INTEGER PRIMARY KEY, action TEXT NOT NULL, entity TEXT NOT NULL,
 entity_id INTEGER, detail TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE integrations(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, mode TEXT NOT NULL,
 target TEXT NOT NULL);
CREATE TABLE sync_runs(id INTEGER PRIMARY KEY, before_state TEXT NOT NULL, after_state TEXT NOT NULL,
 rolled_back INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE sync_baseline(identity TEXT PRIMARY KEY, snapshot TEXT NOT NULL);
"""


def employer_id(db, name):
    key = normalized(name)
    db.execute('INSERT OR IGNORE INTO employers(name,identity) VALUES(?,?)', (name,key))
    return db.execute('SELECT id FROM employers WHERE identity=?', (key,)).fetchone()[0]


def job_identity(row):
    from urllib.parse import urlsplit, urlunsplit
    url = urlsplit(row['url'] or '')
    if url.scheme.lower() in ('https','http') and url.netloc:
        return 'url:' + urlunsplit(('https',url.netloc.lower(),url.path.rstrip('/'),url.query,''))
    return 'name:' + json.dumps([normalized(row[k] or '') for k in ('employer','title','platform')])


def audit(db, action, entity, entity_id=None, detail=None):
    db.execute('INSERT INTO audit_events(action,entity,entity_id,detail) VALUES(?,?,?,?)',
               (action,entity,entity_id,detail))


def migrate(db):
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version > 1:
        raise RuntimeError('Database schema is newer than this application')
    if version == 1:
        return
    for statement in SCHEMA.strip().split(';'):
        if statement.strip():
            db.execute(statement)
    for row in db.execute('SELECT * FROM experiences').fetchall():
        name = row['source'] or 'Legacy demo'
        key = digest('legacy:'+str(row['id']))
        source = db.execute('INSERT INTO source_documents(name,kind,sha256) VALUES(?,?,?)',
                            (name,'legacy',key)).lastrowid
        subject = row['organization'] + ' · ' + row['role']
        fact_ids = []
        for i, statement in enumerate(json.loads(row['facts']), 1):
            fact = db.execute('INSERT INTO facts(identity,kind,subject,statement,source_id) VALUES(?,?,?,?,?)',
                              (key+':'+str(i),'experience',subject,statement,source)).lastrowid
            db.execute('INSERT INTO fact_sources VALUES(?,?,?)', (fact,source,str(i)))
            fact_ids.append(fact)
        if fact_ids:
            # Legacy variants reference an experience as a whole. Keep their original mapping in experiences.
            audit(db,'legacy_experience_migrated','source',source)
    for row in db.execute('SELECT * FROM rules ORDER BY id'):
        version = db.execute('SELECT COALESCE(MAX(version),0)+1 FROM answer_rules WHERE question=? AND scope=?',
                             (row['question'],row['scope'])).fetchone()[0]
        db.execute('INSERT INTO answer_rules(question,answer,scope,sensitive,version) VALUES(?,?,?,?,?)',
                   (row['question'],row['answer'] if not row['sensitive'] else 'Review required',row['scope'],row['sensitive'],version))
    for row in db.execute('SELECT * FROM jobs ORDER BY id').fetchall():
        key = job_identity(row)
        try:
            db.execute('UPDATE jobs SET employer_id=?,identity=? WHERE id=?',
                       (employer_id(db,row['employer']),key,row['id']))
        except sqlite3.IntegrityError:
            db.execute('UPDATE jobs SET employer_id=? WHERE id=?',(employer_id(db,row['employer']),row['id']))
            audit(db,'legacy_duplicate','job',row['id'])
        if row['submitted_at'] or row['status']=='Submitted':
            db.execute('INSERT INTO applications(job_id,status) VALUES(?,?)',(row['id'],row['status']))
    db.execute("INSERT INTO audit_events(action,entity,entity_id,detail,created_at) "
               "SELECT action,'job',job_id,detail,COALESCE(created_at,CURRENT_TIMESTAMP) FROM events ORDER BY id")
    db.execute('PRAGMA user_version=1')

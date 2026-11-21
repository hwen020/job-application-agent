"""Deterministic fictional jobs and evidence for the portfolio walkthrough."""
from . import material_schema, runner_schema, storage, sync

CANDIDATE = 'Synthetic Demo Candidate'
ORGANIZATION = 'Synthetic Demo Aurora Institute'
OPENINGS = (
    ('Backend Developer', 'PageUp', 'Python, SQLite', 'Docker',
     'Build Python services and document test results.'),
    ('Data Analyst', 'NEOGOV', 'SQL, Python', 'Tableau',
     'Validate queries and explain synthetic reporting results.'),
)
BASE = """
CREATE TABLE IF NOT EXISTS experiences(id INTEGER PRIMARY KEY, organization TEXT, role TEXT,
    facts TEXT, variants TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS rules(id INTEGER PRIMARY KEY, question TEXT, answer TEXT, scope TEXT, sensitive INTEGER);
CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY, employer TEXT, title TEXT, platform TEXT,
    priority TEXT, status TEXT, deadline TEXT, exclusion_reason TEXT, url TEXT, submitted_at TEXT);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, job_id INTEGER, action TEXT, detail TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS demo_jds(job_id INTEGER PRIMARY KEY REFERENCES jobs, body TEXT NOT NULL);
"""


def initialize(db):
    db.executescript(BASE)
    storage.migrate(db)
    material_schema.migrate_materials(db)
    runner_schema.migrate_runner(db)
    if db.execute('SELECT 1 FROM demo_jds LIMIT 1').fetchone():
        return
    db.execute('DELETE FROM integrations')
    for title, platform, skills, preferred, responsibilities in OPENINGS:
        title = 'Synthetic Demo '+title
        job_id = sync.upsert(db, sync.job_values({'title': title, 'employer': ORGANIZATION,
                                                'platform': platform, 'priority': 'P1', 'status': 'To review'}))
        text = (f'Job Title: {title}\nEmployer: {ORGANIZATION}\nRequired Skills: {skills}\n'
                f'Preferred Skills: {preferred}\nMinimum Experience: 2 years of related experience.\n'
                f'Responsibilities: {responsibilities}')
        db.execute('INSERT INTO demo_jds VALUES(?,?)', (job_id, text))
    folder = 'Synthetic Demo evidence packet'
    source = db.execute('INSERT INTO source_documents(name,kind,sha256) VALUES(?,?,?)',
                        (folder, 'fixture', storage.digest(folder))).lastrowid
    evidence = (
        ('project', 'Synthetic Demo Service Lab', 'Built a Python service backed by SQLite and tested its query workflow.', 'verified'),
        ('project', 'Synthetic Demo Reporting Lab', 'Validated SQL queries and processed example records with Python.', 'verified'),
        ('education', 'Synthetic Demo Learning Lab', 'Completed a fictional certificate in applied computing.', 'verified'),
        ('project', 'Synthetic Demo Unverified Claim', 'Explored a Docker deployment for a sample service.', 'unverified'),
    )
    for index, (kind, subject, text, status) in enumerate(evidence, 1):
        fact = db.execute("""INSERT INTO facts(identity,kind,subject,statement,source_id,verification_status,last_verified_at)
                          VALUES(?,?,?,?,?,?,CASE WHEN ?='verified' THEN CURRENT_TIMESTAMP END)""",
                          (storage.digest(text), kind, subject, text, source, status, status)).lastrowid
        db.execute('INSERT INTO fact_sources VALUES(?,?,?)', (fact, source, f'Item {index}'))
    storage.audit(db, 'demo_initialized', 'demo', detail='Synthetic Demo dataset loaded')

"""Additive browser runner tables. Phase 1 task and step identities are retained."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS runner_runs(
 task_id INTEGER PRIMARY KEY REFERENCES browser_tasks, job_id INTEGER NOT NULL REFERENCES jobs,
 platform TEXT NOT NULL, mode TEXT NOT NULL, fixture_key TEXT NOT NULL UNIQUE,
 checkpoint TEXT NOT NULL DEFAULT '{}', review_token TEXT, submitted_receipt TEXT, submit_intent INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE UNIQUE INDEX IF NOT EXISTS runner_one_submission ON runner_runs(job_id,mode) WHERE submitted_receipt IS NOT NULL;
CREATE TABLE IF NOT EXISTS runner_events(
 id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES browser_tasks,
 action TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS runner_private(
 id INTEGER PRIMARY KEY, kind TEXT NOT NULL, job_id INTEGER REFERENCES jobs, ciphertext BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS runner_rules(
 id INTEGER PRIMARY KEY, question TEXT NOT NULL, scope TEXT NOT NULL, owner TEXT NOT NULL,
 rule_id INTEGER REFERENCES answer_rules, private_id INTEGER REFERENCES runner_private,
 expires_at TEXT, confirmed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(question,scope,owner));
CREATE TABLE IF NOT EXISTS runner_simulation(
 task_id INTEGER PRIMARY KEY REFERENCES browser_tasks, signed_in INTEGER NOT NULL DEFAULT 0,
 obstacle TEXT NOT NULL DEFAULT '', receipt TEXT);
"""


def migrate_runner(db):
    for statement in SCHEMA.strip().split(';'):
        if statement.strip():
            db.execute(statement)
    db.execute("INSERT OR IGNORE INTO material_migrations VALUES('browser-runner-v1')")

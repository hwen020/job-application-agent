"""Additive Materials Studio migration, independent of the Phase 1 schema."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS material_migrations(name TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS material_jds(
 id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs,
 source TEXT NOT NULL, body TEXT NOT NULL, analysis TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS material_templates(
 id INTEGER PRIMARY KEY, settings TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS material_versions(
 id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs,
 jd_id INTEGER NOT NULL REFERENCES material_jds, version INTEGER NOT NULL,
 parent_id INTEGER REFERENCES material_versions, template_id INTEGER NOT NULL REFERENCES material_templates,
 snapshot TEXT NOT NULL, approved_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(job_id,version));
CREATE TABLE IF NOT EXISTS material_files(
 version_id INTEGER NOT NULL REFERENCES material_versions, name TEXT NOT NULL,
 content BLOB NOT NULL, PRIMARY KEY(version_id,name));
CREATE TABLE IF NOT EXISTS material_final(
 job_id INTEGER PRIMARY KEY REFERENCES jobs, version_id INTEGER NOT NULL REFERENCES material_versions);
"""


def migrate_materials(db):
    for statement in SCHEMA.strip().split(';'):
        if statement.strip():
            db.execute(statement)
    db.execute("INSERT OR IGNORE INTO material_migrations VALUES('materials-studio-v1')")

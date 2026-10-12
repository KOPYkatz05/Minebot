"""Separate SQLite collection state. No reviewer tables or source files are touched."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid

from .config import Config


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.database = self.directory / "discovery.sqlite3"
        with self.db() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 3:
                raise ValueError("Discovery database belongs to a newer version")
            if version < 1:
                if db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                    backups = self.directory / "backups"
                    backups.mkdir(exist_ok=True)
                    with sqlite3.connect(backups / f"before-v1-{uuid.uuid4().hex}.sqlite3") as backup:
                        db.backup(backup)
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, document TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS repositories(id INTEGER PRIMARY KEY, document TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS candidates(
                        id TEXT PRIMARY KEY, repository_id INTEGER NOT NULL,
                        revision TEXT NOT NULL, path TEXT NOT NULL, blob_sha TEXT NOT NULL,
                        status TEXT NOT NULL, document TEXT NOT NULL, created TEXT NOT NULL,
                        UNIQUE(repository_id,revision,path));
                    CREATE TABLE IF NOT EXISTS work(key TEXT PRIMARY KEY, document TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, document TEXT NOT NULL);
                    PRAGMA user_version=1;
                """)
            if version < 2:
                if version == 1:
                    backups = self.directory / 'backups'
                    backups.mkdir(exist_ok=True)
                    with sqlite3.connect(backups / f'before-v2-{uuid.uuid4().hex}.sqlite3') as backup:
                        db.backup(backup)
                db.executescript("""
                    CREATE TABLE artifacts(sha256 TEXT PRIMARY KEY, blob_sha TEXT NOT NULL,
                                           size INTEGER NOT NULL, document TEXT NOT NULL);
                    CREATE INDEX artifacts_blob ON artifacts(blob_sha);
                    CREATE TABLE collection_attempts(
                        id INTEGER PRIMARY KEY, candidate_id TEXT NOT NULL, run_id TEXT NOT NULL,
                        document TEXT NOT NULL);
                    PRAGMA user_version=2;
                """)
            if version < 3:
                if version == 2:
                    backups = self.directory / 'backups'
                    backups.mkdir(exist_ok=True)
                    with sqlite3.connect(backups / f'before-v3-{uuid.uuid4().hex}.sqlite3') as backup:
                        db.backup(backup)
                db.executescript("""
                    CREATE TABLE validations(id TEXT PRIMARY KEY, source_sha256 TEXT NOT NULL,
                        fingerprint TEXT NOT NULL, status TEXT NOT NULL, document TEXT NOT NULL);
                    CREATE INDEX validations_source ON validations(source_sha256,fingerprint,status);
                    PRAGMA user_version=3;
                """)
            db.execute("INSERT OR IGNORE INTO settings VALUES ('config',?)", (json.dumps(Config().document()),))

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.database, timeout=30)
        try:
            db.row_factory = sqlite3.Row
            with db:
                yield db
        finally:
            db.close()

    def config(self):
        with self.db() as db:
            return Config.parse(json.loads(db.execute("SELECT document FROM settings WHERE key='config'").fetchone()[0]))

    def configure(self, changes):
        with self.db() as db:
            current = json.loads(db.execute("SELECT document FROM settings WHERE key='config'").fetchone()[0])
            config = Config.parse(current | changes)
            db.execute("UPDATE settings SET document=? WHERE key='config'", (json.dumps(config.document()),))
        return config.document()

    def retry_at(self):
        with self.db() as db:
            row = db.execute("SELECT document FROM settings WHERE key='github_retry_at'").fetchone()
            return json.loads(row[0]) if row else 0

    def defer_until(self, timestamp):
        with self.db() as db:
            db.execute("INSERT INTO settings VALUES ('github_retry_at',?) ON CONFLICT(key) DO UPDATE SET document=excluded.document",
                       (json.dumps(timestamp),))

    def put_work(self, key, document):
        with self.db() as db:
            db.execute("INSERT INTO work VALUES (?,?) ON CONFLICT(key) DO UPDATE SET document=excluded.document", (key, json.dumps(document)))

    def work(self):
        with self.db() as db:
            return [(r['key'], json.loads(r['document'])) for r in db.execute("SELECT * FROM work ORDER BY key")]

    def remove_work(self, key):
        with self.db() as db:
            db.execute("DELETE FROM work WHERE key=?", (key,))

    def repository(self, repository):
        if repository.get("private") is not False or repository.get("visibility", "public") != "public":
            raise ValueError("Only public repositories are collected")
        with self.db() as db:
            db.execute("INSERT INTO repositories VALUES (?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document", (repository['id'], json.dumps(repository)))

    def candidate(self, document):
        identity = json.dumps([document['repository_id'], document['revision'], document['path']])
        identifier = hashlib.sha256(identity.encode()).hexdigest()
        with self.db() as db:
            created = db.execute("INSERT OR IGNORE INTO candidates VALUES (?,?,?,?,?,?,?,?)", (
                identifier, document['repository_id'], document['revision'], document['path'],
                document['blob_sha'], document.get('status', 'discovered'), json.dumps(document), now())).rowcount
        return identifier, bool(created)

    def candidates(self, limit=100, offset=0):
        if not 1 <= limit <= 1000 or offset < 0:
            raise ValueError("limit must be 1–1000 and offset nonnegative")
        with self.db() as db:
            rows = db.execute("SELECT * FROM candidates ORDER BY CASE WHEN lower(path) LIKE '%house%' THEN 0 ELSE 1 END, created,id LIMIT ? OFFSET ?", (limit, offset))
            return [json.loads(r['document']) | {'id': r['id'], 'status': r['status'], 'created': r['created']} for r in rows]

    def action(self, identifier, action):
        if action not in ('dismiss', 'restore', 'retry', 'retry-validation'):
            raise ValueError("Choose dismiss, restore, retry or retry-validation")
        with self.db() as db:
            row = db.execute("SELECT status,document FROM candidates WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise ValueError("Candidate not found")
            doc = json.loads(row['document'])
            if action == 'retry-validation':
                if row['status'] != 'downloaded':
                    raise ValueError('Only downloaded candidates can be queued for validation')
                doc['validation_status'] = 'not_validated'
                status = row['status']
            elif action == 'retry':
                if row['status'] not in ('failed', 'deferred'):
                    raise ValueError('Only failed or deferred collection can be retried')
                doc['collection'] = {'retry_at': 0}
                status = 'discovered'
            elif action == 'dismiss' and row['status'] != 'dismissed':
                doc['previous_status'] = row['status']
                status = 'dismissed'
            elif action == 'restore' and row['status'] == 'dismissed':
                status = doc.pop('previous_status', 'discovered')
            else:
                status = row['status']
            db.execute("UPDATE candidates SET status=?,document=? WHERE id=?", (status, json.dumps(doc), identifier))

    def update_candidate(self, identifier, changes, status):
        with self.db() as db:
            row = db.execute('SELECT status,document FROM candidates WHERE id=?', (identifier,)).fetchone()
            doc = json.loads(row['document']) | changes
            if row['status'] == 'dismissed':
                doc['previous_status'] = status
                status = 'dismissed'
            db.execute('UPDATE candidates SET status=?,document=? WHERE id=?', (status, json.dumps(doc), identifier))

    def next_collection(self):
        with self.db() as db:
            row = db.execute("""SELECT * FROM candidates
                WHERE status IN ('discovered','collecting','deferred')
                AND COALESCE(json_extract(document,'$.collection.retry_at'),0)<=?
                ORDER BY CASE WHEN lower(path) LIKE '%house%' THEN 0 ELSE 1 END,created,id LIMIT 1""",
                (time.time(),)).fetchone()
            return (json.loads(row['document']) | {'id': row['id'], 'status': row['status']}) if row else None

    def artifact(self, document):
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO artifacts VALUES (?,?,?,?)',
                       (document['sha256'], document['blob_sha'], document['size'], json.dumps(document)))

    def artifact_for_blob(self, sha):
        with self.db() as db:
            row = db.execute('SELECT document FROM artifacts WHERE blob_sha=?', (sha,)).fetchone()
            return json.loads(row[0]) if row else None

    def next_validation(self, fingerprint):
        with self.db() as db:
            row = db.execute("""SELECT * FROM candidates WHERE status='downloaded'
                AND (COALESCE(json_extract(document,'$.validation.validator_fingerprint'),'') != ?
                     OR COALESCE(json_extract(document,'$.validation_status'),'not_validated')
                         IN ('not_validated','validating','interrupted'))
                ORDER BY CASE WHEN lower(path) LIKE '%house%' THEN 0 ELSE 1 END,created,id LIMIT 1""",
                (fingerprint,)).fetchone()
            return json.loads(row['document']) | {'id': row['id'], 'status': row['status']} if row else None

    def save_validation(self, identifier, document):
        with self.db() as db:
            db.execute('INSERT INTO validations VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,document=excluded.document',
                       (identifier, document['source_sha256'], document['validator_fingerprint'],
                        document['status'], json.dumps(document)))

    def cached_validation(self, source_sha256, fingerprint):
        with self.db() as db:
            row = db.execute("SELECT document FROM validations WHERE source_sha256=? AND fingerprint=? AND status='passed' ORDER BY rowid DESC LIMIT 1",
                             (source_sha256, fingerprint)).fetchone()
            return json.loads(row[0]) if row else None

    def recover_validations(self):
        with self.db() as db:
            for row in list(db.execute("SELECT * FROM validations WHERE status='running'")):
                document = json.loads(row['document']) | {'status': 'interrupted', 'finished': now()}
                db.execute("UPDATE validations SET status='interrupted',document=? WHERE id=?", (json.dumps(document), row['id']))

    def validation_history(self, source_sha256, limit=100):
        with self.db() as db:
            return [json.loads(row[0]) | {'id': row[1]} for row in db.execute(
                'SELECT document,id FROM validations WHERE source_sha256=? ORDER BY rowid DESC LIMIT ?', (source_sha256, limit))]

    def start_attempt(self, candidate_id, run_id):
        with self.db() as db:
            return db.execute('INSERT INTO collection_attempts(candidate_id,run_id,document) VALUES (?,?,?)',
                              (candidate_id, run_id, json.dumps({'started': now(), 'status': 'running'}))).lastrowid

    def finish_attempt(self, identifier, document):
        with self.db() as db:
            row = db.execute('SELECT document FROM collection_attempts WHERE id=?', (identifier,)).fetchone()
            db.execute('UPDATE collection_attempts SET document=? WHERE id=?',
                       (json.dumps(json.loads(row[0]) | document | {'finished': now()}), identifier))

    def recover_attempts(self):
        with self.db() as db:
            rows = list(db.execute("SELECT id,document FROM collection_attempts WHERE json_extract(document,'$.status')='running'"))
            for row in rows:
                doc = json.loads(row['document'])
                if doc['status'] == 'running':
                    db.execute('UPDATE collection_attempts SET document=? WHERE id=?',
                               (json.dumps(doc | {'status': 'interrupted', 'finished': now()}), row['id']))

    def attempts(self, identifier, limit=100):
        with self.db() as db:
            return [json.loads(row['document']) | {'id': row['id'], 'run_id': row['run_id']}
                    for row in db.execute('SELECT * FROM collection_attempts WHERE candidate_id=? ORDER BY id DESC LIMIT ?',
                                          (identifier, limit))]

    def save_run(self, identifier, document):
        with self.db() as db:
            db.execute("INSERT INTO runs VALUES (?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document", (identifier, json.dumps(document)))

    def status(self):
        with self.db() as db:
            counts = {r[0]: r[1] for r in db.execute("SELECT status,count(*) FROM candidates GROUP BY status")}
            runs = [json.loads(r[0]) | {'id': r[1]} for r in db.execute("SELECT document,id FROM runs ORDER BY rowid DESC LIMIT 20")]
            repository_count = db.execute("SELECT count(*) FROM repositories").fetchone()[0]
            work_count = db.execute("SELECT count(*) FROM work").fetchone()[0]
        return {'config': self.config().document(), 'candidate_counts': counts, 'repository_count': repository_count,
                'pending_work': work_count, 'runs': runs, 'implementation_stage': 'isolated_validation',
                'training_admission': 'human_review_required'}

"""Separate SQLite collection state. No reviewer tables or source files are touched."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
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
            if version > 1:
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
        if action not in ('dismiss', 'restore'):
            raise ValueError("Choose dismiss or restore")
        with self.db() as db:
            row = db.execute("SELECT status,document FROM candidates WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise ValueError("Candidate not found")
            doc = json.loads(row['document'])
            if action == 'dismiss' and row['status'] != 'dismissed':
                doc['previous_status'] = row['status']
                status = 'dismissed'
            elif action == 'restore' and row['status'] == 'dismissed':
                status = doc.pop('previous_status', 'discovered')
            else:
                status = row['status']
            db.execute("UPDATE candidates SET status=?,document=? WHERE id=?", (status, json.dumps(doc), identifier))

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
                'pending_work': work_count, 'runs': runs, 'implementation_stage': 'metadata_discovery',
                'training_admission': 'human_review_required'}

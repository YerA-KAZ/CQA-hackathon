import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from app.schemas import Conversation


class Storage:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    assessment TEXT
                )
            """)
            yield db

    def import_records(self, records: list[Conversation]) -> None:
        with self.connect() as db:
            db.executemany("""
                INSERT INTO conversations (id, payload) VALUES (?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    assessment = CASE WHEN payload = excluded.payload THEN assessment END,
                    payload = excluded.payload
            """, [(record.conversation_id, record.model_dump_json()) for record in records])

    def list_records(self):
        with self.connect() as db:
            return db.execute("SELECT payload, assessment FROM conversations ORDER BY id").fetchall()

    def get_record(self, conversation_id: str):
        with self.connect() as db:
            return db.execute(
                "SELECT payload, assessment FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()

    @staticmethod
    def _update_assessment(db, conversation_id, source, assessment, previous):
        cursor = db.execute(
            "UPDATE conversations SET assessment = ? WHERE id = ? AND payload = ? AND assessment IS ?",
            (assessment, conversation_id, source, previous),
        )
        return cursor.rowcount == 1

    def save_assessment(self, conversation_id, source, assessment, previous=None):
        with self.connect() as db:
            return self._update_assessment(db, conversation_id, source, assessment, previous)

    def checklist(self):
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS checklists (version INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
            row = db.execute("SELECT version, payload FROM checklists ORDER BY version DESC LIMIT 1").fetchone()
            return row or (0, None)

    def cached(self, key):
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS criterion_cache (key TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            row = db.execute("SELECT payload FROM criterion_cache WHERE key = ?", (key,)).fetchone()
            return row[0] if row else None

    def cache(self, key, payload):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO criterion_cache VALUES (?, ?)", (key, payload))

    def apply_checklist(self, version, checklist, updates):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT MAX(version) FROM checklists").fetchone()[0] or 0
            record_count = db.execute('SELECT COUNT(*) FROM conversations').fetchone()[0]
            if current != version or record_count != len(updates):
                return False
            for conversation_id, source, previous, assessment in updates:
                if not self._update_assessment(db, conversation_id, source, assessment, previous):
                    db.rollback()
                    return False
            db.execute("INSERT INTO checklists VALUES (?, ?)", (version + 1, checklist))
            return True

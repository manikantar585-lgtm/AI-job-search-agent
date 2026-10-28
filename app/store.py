"""SQLite: profile, cached jobs, application tracker."""
import json
import sqlite3
import threading
from dataclasses import fields

from . import config
from .models import Job, Profile

SCHEMA = """
CREATE TABLE IF NOT EXISTS profile (id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL, fetched_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS applications (
    job_id TEXT PRIMARY KEY REFERENCES jobs(id),
    status TEXT NOT NULL DEFAULT 'saved',     -- saved | applied | interview | offer | rejected
    score INTEGER,
    notes TEXT DEFAULT '',
    follow_up TEXT,                           -- YYYY-MM-DD
    cover_letter TEXT DEFAULT '',
    updated_at TEXT DEFAULT (datetime('now'))
);
"""
STATUSES = ("saved", "applied", "interview", "offer", "rejected")
_JOB_FIELDS = {f.name for f in fields(Job)}
_PROFILE_FIELDS = {f.name for f in fields(Profile)}


class Store:
    def __init__(self, path: str | None = None):
        self.conn = sqlite3.connect(path or config.DB_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.lock = threading.RLock()

    # profile
    def get_profile(self) -> Profile:
        row = self.conn.execute("SELECT data FROM profile WHERE id=1").fetchone()
        if not row:
            return Profile()
        d = json.loads(row["data"])
        return Profile(**{k: v for k, v in d.items() if k in _PROFILE_FIELDS})

    def save_profile(self, p: Profile) -> Profile:
        from dataclasses import asdict
        with self.lock, self.conn:
            self.conn.execute("INSERT INTO profile (id, data) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                              (json.dumps(asdict(p)),))
        return p

    # jobs
    def cache_jobs(self, jobs: list[Job]) -> None:
        with self.lock, self.conn:
            self.conn.executemany(
                "INSERT INTO jobs (id, data) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data, fetched_at=datetime('now')",
                [(j.id, json.dumps(j.to_dict())) for j in jobs])

    def get_job(self, job_id: str) -> Job | None:
        row = self.conn.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return None
        return Job(**{k: v for k, v in json.loads(row["data"]).items() if k in _JOB_FIELDS})

    # tracker
    def upsert_application(self, job_id: str, status: str = "saved", score: int | None = None,
                           notes: str | None = None, follow_up: str | None = None,
                           cover_letter: str | None = None) -> dict:
        if status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        if not self.get_job(job_id):
            raise KeyError(job_id)
        with self.lock, self.conn:
            self.conn.execute(
                """INSERT INTO applications (job_id, status, score, notes, follow_up, cover_letter)
                   VALUES (?, ?, ?, COALESCE(?, ''), ?, COALESCE(?, ''))
                   ON CONFLICT(job_id) DO UPDATE SET status=excluded.status,
                     score=COALESCE(excluded.score, applications.score),
                     notes=CASE WHEN ? IS NULL THEN applications.notes ELSE excluded.notes END,
                     follow_up=COALESCE(excluded.follow_up, applications.follow_up),
                     cover_letter=CASE WHEN ? IS NULL THEN applications.cover_letter ELSE excluded.cover_letter END,
                     updated_at=datetime('now')""",
                (job_id, status, score, notes, follow_up, cover_letter, notes, cover_letter))
        return self.get_application(job_id)

    def get_application(self, job_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM applications WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["job"] = self.get_job(job_id).to_dict()
        return d

    def list_applications(self) -> list[dict]:
        ids = [r["job_id"] for r in self.conn.execute(
            "SELECT job_id FROM applications ORDER BY CASE status WHEN 'offer' THEN 0 WHEN 'interview' THEN 1 "
            "WHEN 'applied' THEN 2 WHEN 'saved' THEN 3 ELSE 4 END, score DESC")]
        return [self.get_application(i) for i in ids]

    def delete_application(self, job_id: str) -> bool:
        with self.lock, self.conn:
            return self.conn.execute("DELETE FROM applications WHERE job_id=?", (job_id,)).rowcount > 0

    def due_follow_ups(self, today: str) -> list[dict]:
        ids = [r["job_id"] for r in self.conn.execute(
            "SELECT job_id FROM applications WHERE follow_up IS NOT NULL AND follow_up <= ? AND status IN ('applied','interview')",
            (today,))]
        return [self.get_application(i) for i in ids]

"""Lightweight SQLite persistence for jobs and per-image results.

A single shared connection is used with a lock around writes, which is
plenty for a batch-scanning tool processing hundreds (not millions) of
images. Data survives server restarts so a user can close the browser
tab and come back later to the same job URL.
"""

import json
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path(__file__).resolve().parent.parent / "storage" / "db.sqlite3"

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
    return _conn


def init_db() -> None:
    conn = get_conn()
    with _lock:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                total INTEGER NOT NULL,
                processed INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL DEFAULT 'queued'
            );

            CREATE TABLE IF NOT EXISTS images (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                original_filename TEXT NOT NULL,
                stored_path TEXT,
                state TEXT NOT NULL DEFAULT 'queued',
                status_code INTEGER,
                status_key TEXT,
                tracking_number TEXT,
                weight_kg REAL,
                weight_raw TEXT,
                length_cm REAL,
                width_cm REAL,
                height_cm REAL,
                dims_raw TEXT,
                location TEXT,
                timestamp_raw TEXT,
                notes TEXT,
                error TEXT,
                processed_at TEXT,
                FOREIGN KEY (job_id) REFERENCES jobs(id)
            );

            CREATE INDEX IF NOT EXISTS idx_images_job ON images(job_id);
            """
        )
        conn.commit()


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def create_job(job_id: str, total: int, created_at: str) -> None:
    conn = get_conn()
    with _lock:
        conn.execute(
            "INSERT INTO jobs (id, created_at, total, processed, state) "
            "VALUES (?, ?, ?, 0, 'queued')",
            (job_id, created_at, total),
        )
        conn.commit()


def create_image_row(
    image_id: str, job_id: str, seq: int, original_filename: str
) -> None:
    conn = get_conn()
    with _lock:
        conn.execute(
            "INSERT INTO images (id, job_id, seq, original_filename, state) "
            "VALUES (?, ?, ?, ?, 'queued')",
            (image_id, job_id, seq, original_filename),
        )
        conn.commit()


def set_job_state(job_id: str, state: str) -> None:
    conn = get_conn()
    with _lock:
        conn.execute("UPDATE jobs SET state = ? WHERE id = ?", (state, job_id))
        conn.commit()


def mark_image_processing(image_id: str, stored_path: str) -> None:
    conn = get_conn()
    with _lock:
        conn.execute(
            "UPDATE images SET state = 'processing', stored_path = ? WHERE id = ?",
            (stored_path, image_id),
        )
        conn.commit()


def save_image_result(image_id: str, job_id: str, result: dict[str, Any]) -> None:
    conn = get_conn()
    with _lock:
        conn.execute(
            """
            UPDATE images SET
                state = 'done',
                status_code = ?,
                status_key = ?,
                tracking_number = ?,
                weight_kg = ?,
                weight_raw = ?,
                length_cm = ?,
                width_cm = ?,
                height_cm = ?,
                dims_raw = ?,
                location = ?,
                timestamp_raw = ?,
                notes = ?,
                error = ?,
                processed_at = datetime('now')
            WHERE id = ?
            """,
            (
                result.get("status_code"),
                result.get("status_key"),
                result.get("tracking_number"),
                result.get("weight_kg"),
                result.get("weight_raw"),
                result.get("length_cm"),
                result.get("width_cm"),
                result.get("height_cm"),
                result.get("dims_raw"),
                result.get("location"),
                result.get("timestamp_raw"),
                result.get("notes"),
                result.get("error"),
                image_id,
            ),
        )
        conn.execute(
            "UPDATE jobs SET processed = processed + 1 WHERE id = ?", (job_id,)
        )
        conn.commit()


def get_job(job_id: str) -> Optional[sqlite3.Row]:
    conn = get_conn()
    cur = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    return cur.fetchone()


def list_jobs(limit: int = 50) -> list[sqlite3.Row]:
    conn = get_conn()
    cur = conn.execute(
        "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    return cur.fetchall()


def get_images_for_job(job_id: str) -> list[sqlite3.Row]:
    conn = get_conn()
    cur = conn.execute(
        "SELECT * FROM images WHERE job_id = ? ORDER BY seq ASC", (job_id,)
    )
    return cur.fetchall()


def get_incomplete_images() -> list[sqlite3.Row]:
    """Images left in 'queued'/'processing' state, e.g. after a server
    restart interrupted an in-flight batch (the thread pool state is lost
    on restart, but the DB record survives)."""
    conn = get_conn()
    cur = conn.execute(
        "SELECT * FROM images WHERE state IN ('queued', 'processing') ORDER BY job_id, seq"
    )
    return cur.fetchall()


def get_status_counts(job_id: str) -> dict[str, int]:
    conn = get_conn()
    cur = conn.execute(
        "SELECT status_key, COUNT(*) as n FROM images "
        "WHERE job_id = ? AND status_key IS NOT NULL GROUP BY status_key",
        (job_id,),
    )
    return {row["status_key"]: row["n"] for row in cur.fetchall()}

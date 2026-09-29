import os
import sqlite3
import time
from pathlib import Path
from threading import Lock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STORAGE_ROOT = Path(os.getenv("STORAGE_ROOT", str(PROJECT_ROOT)))
JOB_DB = STORAGE_ROOT / "upload_jobs.sqlite3"

STORAGE_ROOT.mkdir(parents=True, exist_ok=True)

_db_lock = Lock()


def _connect():
    connection = sqlite3.connect(
        str(JOB_DB),
        timeout=30,
        check_same_thread=False
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL;")
    connection.execute("PRAGMA synchronous=NORMAL;")
    return connection


def init_job_database():
    with _db_lock:
        with _connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS upload_jobs (
                    job_id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    current_page INTEGER NOT NULL DEFAULT 0,
                    total_pages INTEGER NOT NULL DEFAULT 0,
                    chunks INTEGER NOT NULL DEFAULT 0,
                    searchable INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    started_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    finished_at REAL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_upload_jobs_status
                ON upload_jobs(status, updated_at)
                """
            )
            connection.commit()


def create_job(job_id, filename, file_path):
    now = time.time()

    with _db_lock:
        with _connect() as connection:
            connection.execute(
                """
                INSERT INTO upload_jobs (
                    job_id,
                    filename,
                    file_path,
                    status,
                    stage,
                    current_page,
                    total_pages,
                    chunks,
                    searchable,
                    message,
                    error,
                    started_at,
                    updated_at,
                    finished_at
                )
                VALUES (?, ?, ?, 'queued', 'queued', 0, 0, 0, 0, ?, '', ?, ?, NULL)
                """,
                (
                    job_id,
                    filename,
                    str(file_path),
                    "Document is queued for indexing.",
                    now,
                    now
                )
            )
            connection.commit()


def get_job(job_id):
    with _db_lock:
        with _connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM upload_jobs
                WHERE job_id = ?
                """,
                (job_id,)
            ).fetchone()

    return dict(row) if row else None


def update_job(job_id, **fields):
    if not fields:
        return

    fields["updated_at"] = time.time()

    allowed = {
        "status",
        "stage",
        "current_page",
        "total_pages",
        "chunks",
        "searchable",
        "message",
        "error",
        "finished_at",
        "updated_at"
    }

    clean = {
        key: value
        for key, value in fields.items()
        if key in allowed
    }

    if not clean:
        return

    assignments = ", ".join(
        f"{key} = ?"
        for key in clean
    )

    values = list(clean.values())
    values.append(job_id)

    with _db_lock:
        with _connect() as connection:
            connection.execute(
                f"""
                UPDATE upload_jobs
                SET {assignments}
                WHERE job_id = ?
                """,
                values
            )
            connection.commit()


def requeue_incomplete_jobs():
    """
    If Railway restarts, jobs that were processing are placed back in the queue.
    The ingestion code resumes from current_page.
    """
    now = time.time()

    with _db_lock:
        with _connect() as connection:
            connection.execute(
                """
                UPDATE upload_jobs
                SET
                    status = 'queued',
                    stage = 'queued',
                    message = 'Job resumed after service restart.',
                    updated_at = ?
                WHERE status IN ('starting', 'processing', 'ocr', 'indexing')
                """,
                (now,)
            )
            connection.commit()


def claim_next_job():
    """
    Atomically claim one queued job.
    Only one worker loop is used, so large OCR jobs run serially.
    """
    with _db_lock:
        connection = _connect()

        try:
            connection.execute("BEGIN IMMEDIATE")

            row = connection.execute(
                """
                SELECT *
                FROM upload_jobs
                WHERE status = 'queued'
                ORDER BY started_at ASC
                LIMIT 1
                """
            ).fetchone()

            if not row:
                connection.commit()
                return None

            now = time.time()

            connection.execute(
                """
                UPDATE upload_jobs
                SET
                    status = 'processing',
                    stage = 'starting',
                    message = 'Document processing is starting.',
                    updated_at = ?
                WHERE job_id = ?
                """,
                (
                    now,
                    row["job_id"]
                )
            )

            connection.commit()

            job = dict(row)
            job["status"] = "processing"
            job["stage"] = "starting"
            return job

        except Exception:
            connection.rollback()
            raise

        finally:
            connection.close()

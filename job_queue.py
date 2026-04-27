"""SQLite-backed producer-consumer job queue."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

import platform
_machine_suffix = f"-{platform.node()}" if platform.node() else ""
DB_PATH = f"loto_jobs{_machine_suffix}.db"
JOB_PENDING = "PENDING"
JOB_RUNNING = "RUNNING"
JOB_COMPLETED = "COMPLETED"
JOB_FAILED = "FAILED"
JOB_CANCELLED = "CANCELLED"


def _connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    p = Path(db_path)
    if not p.is_absolute():
        p = Path.cwd() / p
    p.parent.mkdir(parents=True, exist_ok=True)
    
    last_exc = None
    for attempt in range(5):
        try:
            conn = sqlite3.connect(str(p), timeout=30, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=30000;")
            # WAL mode is sometimes problematic on networked/cloud drives during sync
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
            except sqlite3.OperationalError as e:
                if "disk I/O error" in str(e):
                    # If WAL fails, try to continue with default if possible, or just log it
                    import logging
                    logging.warning(f"Failed to set WAL mode (attempt {attempt+1}): {e}. Retrying...")
                    conn.close()
                    time.sleep(0.5 * (attempt + 1))
                    last_exc = e
                    continue
                raise
            return conn
        except sqlite3.OperationalError as e:
            last_exc = e
            if "disk I/O error" in str(e) or "database is locked" in str(e):
                time.sleep(0.5 * (attempt + 1))
                continue
            raise
    
    if last_exc:
        raise last_exc
    raise sqlite3.OperationalError("Could not connect to database after multiple retries")


def init_job_queue(db_path: str = DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY,
                task_type TEXT NOT NULL,
                status TEXT NOT NULL,
                config_json TEXT NOT NULL,
                result_json TEXT,
                progress_pct INTEGER NOT NULL DEFAULT 0,
                log_tail TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pipeline_cache (
                input_hash TEXT PRIMARY KEY,
                result_json TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_used_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


def submit_job(task_type: str, config_json: str, db_path: str = DB_PATH) -> int:
    init_job_queue(db_path)
    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO jobs (task_type, status, config_json, result_json, progress_pct, log_tail)
            VALUES (?, ?, ?, NULL, 0, '')
            """,
            (task_type, JOB_PENDING, config_json),
        )
        conn.commit()
        return int(cur.lastrowid)


def get_job_status(job_id: int, db_path: str = DB_PATH) -> dict[str, Any] | None:
    init_job_queue(db_path)
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (int(job_id),)).fetchone()
    return dict(row) if row else None


def update_job_progress(job_id: int, pct: int, log_msg: str, db_path: str = DB_PATH) -> None:
    init_job_queue(db_path)
    pct_i = max(0, min(100, int(pct)))
    line = str(log_msg or "").strip()
    if not line:
        return
    from datetime import datetime
    ts = datetime.now().strftime("%H:%M:%S")
    stamped = f"[{ts}] {line}"
    with _connect(db_path) as conn:
        current = conn.execute(
            "SELECT log_tail FROM jobs WHERE id = ?",
            (int(job_id),),
        ).fetchone()
        prev_tail = (current["log_tail"] if current else "") if current else ""
        merged = (prev_tail + ("\n" if prev_tail else "") + stamped).strip()
        # keep tail compact to avoid unbounded growth
        if len(merged) > 6000:
            merged = merged[-6000:]
        conn.execute(
            "UPDATE jobs SET progress_pct = ?, log_tail = ? WHERE id = ?",
            (pct_i, merged, int(job_id)),
        )
        conn.commit()
        
    # Verificăm statusul după update pentru a semnaliza oprirea dacă e cazul
    return is_job_cancelled(job_id, db_path=db_path)


def complete_job(job_id: int, result_json: str, db_path: str = DB_PATH) -> None:
    init_job_queue(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE jobs
            SET status = ?, progress_pct = 100, result_json = ?
            WHERE id = ?
            """,
            (JOB_COMPLETED, result_json, int(job_id)),
        )
        conn.commit()


def fail_job(job_id: int, error_msg: str, db_path: str = DB_PATH) -> None:
    init_job_queue(db_path)
    msg = str(error_msg or "Unknown worker error")
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE jobs
            SET status = ?, result_json = ?, log_tail = ?
            WHERE id = ?
            """,
            (JOB_FAILED, msg, msg[-6000:], int(job_id)),
        )
        conn.commit()


def fetch_pending_job(db_path: str = DB_PATH) -> dict[str, Any] | None:
    init_job_queue(db_path)
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT * FROM jobs
            WHERE status = ?
            ORDER BY id ASC
            LIMIT 1
            """,
            (JOB_PENDING,),
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        job_id = int(row["id"])
        conn.execute(
            "UPDATE jobs SET status = ?, progress_pct = 1 WHERE id = ?",
            (JOB_RUNNING, job_id),
        )
        conn.commit()
    return get_job_status(job_id, db_path=db_path)


def fetch_running_job(db_path: str = DB_PATH) -> dict[str, Any] | None:
    """Preluăm job-uri RUNNING care nu au fost procesate încă."""
    init_job_queue(db_path)
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT * FROM jobs
            WHERE status = ? AND progress_pct <= 5
            ORDER BY id ASC
            LIMIT 1
            """,
            (JOB_RUNNING,),
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        job_id = int(row["id"])
        # Actualizăm progresul pentru a marca că job-ul este preluat
        conn.execute(
            "UPDATE jobs SET progress_pct = 2 WHERE id = ?",
            (job_id,),
        )
        conn.commit()
    return get_job_status(job_id, db_path=db_path)


def cancel_pending_running_jobs(reason: str = "Oprit de utilizator", db_path: str = DB_PATH) -> int:
    """Soft cancel: mark PENDING/RUNNING jobs as CANCELLED instead of DELETE."""
    init_job_queue(db_path)
    msg = str(reason or "Oprit de utilizator")
    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE jobs
            SET status = ?, result_json = ?, log_tail = CASE
                WHEN log_tail IS NULL OR log_tail = '' THEN ?
                ELSE substr(log_tail || char(10) || ?, -6000)
            END
            WHERE status IN (?, ?)
            """,
            (JOB_CANCELLED, msg, msg, msg, JOB_PENDING, JOB_RUNNING),
        )
        conn.commit()
        return cur.rowcount


def is_job_cancelled(job_id: int, db_path: str = DB_PATH) -> bool:
    """Check if a specific job has been cancelled."""
    init_job_queue(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT status FROM jobs WHERE id = ?",
            (int(job_id),),
        ).fetchone()
    # Dacă job-ul nu mai există (a fost șters prin reset) sau are status CANCELLED, returnăm True (STOP)
    if row is None:
        return True
    return row["status"] == JOB_CANCELLED


def reset_job_queue(db_path: str = DB_PATH) -> None:
    """Clear all queued jobs and reset SQLite AUTOINCREMENT sequence."""
    init_job_queue(db_path)
    last_exc: Exception | None = None
    wait_s = 0.15
    for _ in range(8):
        try:
            with _connect(db_path) as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("DELETE FROM jobs")
                try:
                    conn.execute("DELETE FROM sqlite_sequence WHERE name = 'jobs'")
                except Exception:
                    # Table sqlite_sequence may not exist if AUTOINCREMENT is unused.
                    pass
                conn.commit()
                return
        except Exception as exc:
            last_exc = exc
            time.sleep(wait_s)
            wait_s = min(wait_s * 1.7, 2.0)
    if last_exc is not None:
        raise last_exc


def clear_pipeline_cache(db_path: str = DB_PATH) -> None:
    """Clear cached pipeline results."""
    init_job_queue(db_path)
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM pipeline_cache")
        conn.commit()


def requeue_running_jobs(db_path: str = DB_PATH) -> int:
    """Move orphan RUNNING jobs back to PENDING (useful after worker restarts/crashes)."""
    init_job_queue(db_path)
    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE jobs
            SET status = ?, log_tail = CASE
                WHEN log_tail IS NULL OR log_tail = '' THEN ?
                ELSE log_tail || char(10) || ?
            END
            WHERE status = ?
            """,
            (
                JOB_PENDING,
                "Worker restart detectat: job reprogramat automat.",
                "Worker restart detectat: job reprogramat automat.",
                JOB_RUNNING,
            ),
        )
        conn.commit()
        return int(getattr(cur, "rowcount", 0) or 0)


def fail_running_jobs(reason: str = "Job oprit automat la startup.", db_path: str = DB_PATH) -> int:
    """Mark all RUNNING jobs as FAILED (startup safety cleanup)."""
    try:
        init_job_queue(db_path)
        msg = str(reason or "Job oprit automat la startup.")
        # Wrap connection in a way that handles initial connection failures
        try:
            conn_context = _connect(db_path)
        except Exception as e:
            import logging
            logging.warning(f"fail_running_jobs: nu se poate conecta la baza de date {db_path}: {e}")
            return 0
            
        with conn_context as conn:
            cur = conn.execute(
                """
                UPDATE jobs
                SET status = ?, result_json = ?, log_tail = CASE
                    WHEN log_tail IS NULL OR log_tail = '' THEN ?
                    ELSE substr(log_tail || char(10) || ?, -6000)
                END
                WHERE status = ?
                """,
                (
                    JOB_FAILED,
                    msg,
                    msg,
                    msg,
                    JOB_RUNNING,
                ),
            )
            conn.commit()
            return cur.rowcount
    except Exception as e:
        # Protecție pentru disk I/O error și alte erori de startup
        import logging
        logging.warning(f"fail_running_jobs: eroare în timpul procesării {db_path}: {e}")
        return 0


def get_pipeline_cache(input_hash: str, db_path: str = DB_PATH) -> str | None:
    """Return cached pipeline result for a specific input hash."""
    key = str(input_hash or "").strip()
    if not key:
        return None
    init_job_queue(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT result_json FROM pipeline_cache WHERE input_hash = ?",
            (key,),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE pipeline_cache SET last_used_at = CURRENT_TIMESTAMP WHERE input_hash = ?",
            (key,),
        )
        conn.commit()
        return str(row["result_json"])


def put_pipeline_cache(input_hash: str, result_json: str, db_path: str = DB_PATH) -> None:
    """Insert/update cached pipeline result for current input hash."""
    key = str(input_hash or "").strip()
    if not key:
        return
    init_job_queue(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO pipeline_cache (input_hash, result_json, created_at, last_used_at)
            VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(input_hash) DO UPDATE SET
                result_json = excluded.result_json,
                last_used_at = CURRENT_TIMESTAMP
            """,
            (key, str(result_json)),
        )
        # Keep cache bounded: oldest entries by last usage are pruned.
        conn.execute(
            """
            DELETE FROM pipeline_cache
            WHERE input_hash IN (
                SELECT input_hash
                FROM pipeline_cache
                ORDER BY last_used_at DESC
                LIMIT -1 OFFSET 40
            )
            """
        )
        conn.commit()


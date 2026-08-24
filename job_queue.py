"""SQLite-backed producer-consumer job queue."""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Folder dedicat, în AFARA OneDrive, ales de utilizator pentru fișierele de stare
# mutate (Windows). Schimbabil aici sau prin env LOTO_JOBS_DB.
_PREFERRED_WIN_DIR = r"D:\_BUILD\_LOTO"


def _default_db_path() -> str:
    """Coada SQLite NU trebuie să stea în OneDrive: sync-ul poate corupe WAL-ul
    bazei ACTIVE (scriere parțială) ȘI sincronizează joburi între mașini (laptop
    ↔ ALF). O punem în AFARA OneDrive.
      • Override explicit: env LOTO_JOBS_DB.
      • Windows: D:\\_BUILD\\_LOTO (preferat), apoi %LOCALAPPDATA%\\LOTO.
      • Linux: $XDG_CACHE_HOME/LOTO (sau ~/.cache/LOTO).
      • Fallback final: cwd (comportamentul vechi)."""
    env = os.environ.get("LOTO_JOBS_DB")
    if env:
        return env
    candidates: list[str] = []
    if os.name == "nt":
        candidates.append(_PREFERRED_WIN_DIR)
        la = os.environ.get("LOCALAPPDATA")
        if la:
            candidates.append(os.path.join(la, "LOTO"))
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
        candidates.append(os.path.join(base, "LOTO"))
    for d in candidates:
        try:
            os.makedirs(d, exist_ok=True)
            return os.path.join(d, "loto_jobs.db")
        except Exception:  # noqa: BLE001
            continue
    return "loto_jobs.db"


DB_PATH = _default_db_path()
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


# Cache pentru DB-urile deja inițializate în acest proces — evităm CREATE TABLE
# redundant la fiecare apel get_job_status/update_job_progress/etc.
_INITIALIZED_DBS: set[str] = set()


def init_job_queue(db_path: str = DB_PATH) -> None:
    if db_path in _INITIALIZED_DBS:
        return
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
        # Migrare additivă: completed_at (joburile mai vechi NU o au). Folosită de UI
        # ca să știe DACĂ un job COMPLETED e recent (recuperare după repornire UI →
        # mail/shutdown DOAR pentru finalizări proaspete, fără surprize la joburi vechi).
        migrated = True
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
            if "completed_at" not in cols:
                conn.execute("ALTER TABLE jobs ADD COLUMN completed_at TIMESTAMP")
        except sqlite3.OperationalError as exc:
            # "duplicate column" = altă conexiune a adăugat-o deja (race UI↔worker) → benign.
            # Altceva (lock/I/O OneDrive cât fișierul se sincronizează) → NU marcăm DB-ul
            # ca inițializat, ca un apel ulterior să RE-încerce migrarea (altfel
            # complete_job ar eșua cu "no such column" tot restul vieții procesului).
            if "duplicate column" not in str(exc).lower():
                logger.warning("[job_queue] migrare completed_at amânată: %s", exc)
                migrated = False
        conn.commit()
    if migrated:
        _INITIALIZED_DBS.add(db_path)


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


def get_active_job(db_path: str = DB_PATH) -> dict[str, Any] | None:
    """Read-only: cel mai recent job PENDING/RUNNING (FĂRĂ a-l revendica).

    Folosit de UI ca să se re-ataşeze la un job în curs după un reload/restart —
    altfel active_job_id se pierde și rezultatul rămâne orfan în DB."""
    init_job_queue(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE status IN (?, ?) ORDER BY id DESC LIMIT 1",
            (JOB_PENDING, JOB_RUNNING),
        ).fetchone()
    return dict(row) if row else None


def is_stale_unstarted_job(job: dict | None, worker_alive: bool) -> bool:
    """PENDING 0% fără log, worker mort = cadavru, nu job viu.

    START_8000 omoară worker-ul; dacă un astfel de rând rămâne în DB și UI-ul
    îl reatașează, afișează «⏳ Job în rulare (#1) — 0% / se inițializează...»
    la o pornire goală. RUNNING cu progres, sau PENDING cât worker-ul e viu
    (tocmai trimis, încă nepreluat), NU sunt stale.
    """
    if not job or worker_alive:
        return False
    status = str(job.get("status") or "")
    try:
        pct = int(job.get("progress_pct") or 0)
    except (TypeError, ValueError):
        pct = 0
    tail = str(job.get("log_tail") or "").strip()
    return status == JOB_PENDING and pct <= 1 and not tail


def is_fresh_ui_start() -> bool:
    """START_8000.bat setează LOTO_FRESH_START=1: sesiune nouă, fără job automat.

    Fără acest flag (repornire doar a UI-ului, worker încă viu) reatașarea
    rămâne permisă. Valorile acceptate: 1 / true / yes.
    """
    return os.environ.get("LOTO_FRESH_START", "").strip().lower() in {"1", "true", "yes"}


def update_job_progress(job_id: int, pct: int, log_msg: str, db_path: str = DB_PATH) -> bool:
    """Actualizează progresul unui job și întoarce True dacă jobul a fost anulat între timp."""
    init_job_queue(db_path)
    pct_i = max(0, min(100, int(pct)))
    line = str(log_msg or "").strip()
    if not line:
        return False
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
    return bool(is_job_cancelled(job_id, db_path=db_path))


def complete_job(job_id: int, result_json: str, db_path: str = DB_PATH) -> None:
    init_job_queue(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            """
            -- completed_at TREBUIE să rămână text UTC din CURRENT_TIMESTAMP
            -- ('YYYY-MM-DD HH:MM:SS'): UI-ul (_completed_age_seconds) îl parsează ca
            -- naiv-UTC. Nu-l scrie din Python (ar fi local → vechime greșită).
            UPDATE jobs
            SET status = ?, progress_pct = 100, result_json = ?, completed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (JOB_COMPLETED, result_json, int(job_id)),
        )
        conn.commit()


def get_latest_completed_job(db_path: str = DB_PATH) -> dict[str, Any] | None:
    """Read-only: cel mai recent job COMPLETED (cu completed_at, dacă există).

    Folosit de UI la pornire ca să recupereze un job care s-a terminat cât UI-ul
    era jos — get_active_job() întoarce doar PENDING/RUNNING, deci altfel rezultatul
    (și mail-ul/shutdown-ul de la final) ar rămâne orfan. UI-ul decide pe baza
    vechimii (completed_at) dacă mai declanșează mail/shutdown sau doar afișează."""
    init_job_queue(db_path)
    with _connect(db_path) as conn:
        # După completed_at (cea mai recentă finalizare reală), cu id ca tie-break.
        # NULLS LAST: joburile vechi fără completed_at nu „bat" unul proaspăt.
        row = conn.execute(
            "SELECT * FROM jobs WHERE status = ? "
            "ORDER BY (completed_at IS NULL), completed_at DESC, id DESC LIMIT 1",
            (JOB_COMPLETED,),
        ).fetchone()
    return dict(row) if row else None


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


def _claim_job(
    db_path: str,
    where_sql: str,
    where_params: tuple,
    update_sql: str,
    update_extra_params: tuple = (),
) -> dict[str, Any] | None:
    """Pattern unificat de claim: BEGIN IMMEDIATE → SELECT cel mai vechi → UPDATE → COMMIT.

    BEGIN IMMEDIATE acordă RESERVED lock; combinat cu PRAGMA busy_timeout=30000
    setat în _connect, asigură serializare corectă între workeri concurenți.
    """
    init_job_queue(db_path)
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            f"SELECT * FROM jobs WHERE {where_sql} ORDER BY id ASC LIMIT 1",
            where_params,
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        job_id = int(row["id"])
        conn.execute(update_sql, update_extra_params + (job_id,))
        conn.commit()
    return get_job_status(job_id, db_path=db_path)


def fetch_pending_job(db_path: str = DB_PATH) -> dict[str, Any] | None:
    return _claim_job(
        db_path,
        where_sql="status = ?",
        where_params=(JOB_PENDING,),
        update_sql="UPDATE jobs SET status = ?, progress_pct = 1 WHERE id = ?",
        update_extra_params=(JOB_RUNNING,),
    )


def fetch_running_job(db_path: str = DB_PATH) -> dict[str, Any] | None:
    """Preluăm job-uri RUNNING care nu au fost procesate încă (fallback la restart worker)."""
    return _claim_job(
        db_path,
        where_sql="status = ? AND progress_pct <= 5",
        where_params=(JOB_RUNNING,),
        update_sql="UPDATE jobs SET progress_pct = 2 WHERE id = ?",
    )


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


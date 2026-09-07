"""SQLite-backed producer-consumer job queue."""

from __future__ import annotations

import contextlib
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
        base = os.environ.get("XDG_CACHE_HOME") or os.path.join(
            os.path.expanduser("~"), ".cache"
        )
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
        conn = None
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

                    logging.warning(
                        f"Failed to set WAL mode (attempt {attempt + 1}): {e}. Retrying..."
                    )
                    conn.close()
                    time.sleep(0.5 * (attempt + 1))
                    last_exc = e
                    continue
                raise
            return conn
        except sqlite3.OperationalError as e:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
            last_exc = e
            if "disk I/O error" in str(e) or "database is locked" in str(e):
                time.sleep(0.5 * (attempt + 1))
                continue
            raise

    if last_exc:
        raise last_exc
    raise sqlite3.OperationalError(
        "Could not connect to database after multiple retries"
    )


@contextlib.contextmanager
def _conn(db_path: str = DB_PATH):
    """`_connect` + ÎNCHIDERE garantată.

    `with sqlite3.connect(...) as conn:` gestionează doar TRANZACȚIA (commit /
    rollback) — NU închide conexiunea. Cu UI-ul care face poll la fiecare tick și
    worker-ul care scrie progres continuu, conexiunile se adunau până le prindea
    GC-ul generațional (măsurat: 5 → 406 descriptori după 200 de `get_job_status`,
    reveniți la 5 doar după `gc.collect()`). Pe Windows fiecare conexiune deschisă
    ține handle-uri pe `-wal`/`-shm` și blochează checkpoint-ul WAL.
    """
    conn = _connect(db_path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


# Cache pentru DB-urile deja inițializate în acest proces — evităm CREATE TABLE
# redundant la fiecare apel get_job_status/update_job_progress/etc.
_INITIALIZED_DBS: set[str] = set()


def init_job_queue(db_path: str = DB_PATH) -> None:
    if db_path in _INITIALIZED_DBS:
        return
    with _conn(db_path) as conn:
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
        # worker_token: identitatea (uuid4 per proces) a workerului care a REVENDICAT
        # jobul. Fără ea, `complete_job`/`fail_job` garantau doar `status = RUNNING`,
        # nu și că apelantul e ÎNCĂ proprietarul acelei rulări — un worker A căruia
        # i s-a reprogramat jobul de `requeue_running_jobs()` (ex. la pornirea unui
        # worker B) putea, la finalul propriei rulări STALE, să suprascrie tăcut
        # rezultatul lui B (sau chiar să-l marcheze FAILED), fiindcă statusul redevenise
        # RUNNING sub B. Coloana e opțională la citire/scriere (None = fără gardă de
        # proprietate, comportament vechi) — doar worker.py o populează în producție.
        migrated = True
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
            if "completed_at" not in cols:
                conn.execute("ALTER TABLE jobs ADD COLUMN completed_at TIMESTAMP")
            if "worker_token" not in cols:
                conn.execute("ALTER TABLE jobs ADD COLUMN worker_token TEXT")
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
    with _conn(db_path) as conn:
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
    with _conn(db_path) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (int(job_id),)).fetchone()
    return dict(row) if row else None


def get_active_job(db_path: str = DB_PATH) -> dict[str, Any] | None:
    """Read-only: cel mai recent job PENDING/RUNNING (FĂRĂ a-l revendica).

    Folosit de UI ca să se re-ataşeze la un job în curs după un reload/restart —
    altfel active_job_id se pierde și rezultatul rămâne orfan în DB."""
    init_job_queue(db_path)
    with _conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE status IN (?, ?) ORDER BY id DESC LIMIT 1",
            (JOB_PENDING, JOB_RUNNING),
        ).fetchone()
    return dict(row) if row else None


def is_unstarted_job(job: dict | None) -> bool:
    """PENDING/RUNNING cu ≤1% și fără log = worker-ul NU l-a preluat.

    Ecranul «⏳ Job în rulare (#1) — 0% / se inițializează...» e exact starea
    asta. La pornirea UI-ului e leftover, nu click pe Generează. Un job viu
    are log_tail («Job preluat de worker.») și progress ≥ 2.
    """
    if not job:
        return False
    status = str(job.get("status") or "")
    if status not in {JOB_PENDING, JOB_RUNNING}:
        return False
    try:
        pct = int(job.get("progress_pct") or 0)
    except TypeError, ValueError:
        pct = 0
    tail = str(job.get("log_tail") or "").strip()
    return pct <= 1 and not tail


def is_stale_unstarted_job(job: dict | None, worker_alive: bool) -> bool:
    """Compat: unstarted, ignorând worker_alive (la boot user-ul n-a apăsat încă)."""
    return is_unstarted_job(job)


def is_fresh_ui_start() -> bool:
    """START_8000.bat setează LOTO_FRESH_START=1: sesiune nouă, fără job automat.

    Fără acest flag (repornire doar a UI-ului, worker încă viu) reatașarea
    rămâne permisă. Valorile acceptate: 1 / true / yes.
    """
    return os.environ.get("LOTO_FRESH_START", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def update_job_progress(
    job_id: int,
    pct: int,
    log_msg: str,
    db_path: str = DB_PATH,
    worker_token: str | None = None,
) -> bool:
    """Actualizează atomic progresul; True cere workerului să se oprească.

    Scriem numai cât timp jobul este ``RUNNING``. Vechiul SELECT + UPDATE lăsa
    o fereastră în care anularea putea fi comisă între ele, iar UPDATE-ul
    workerului suprascria apoi logul de anulare și procentul jobului CANCELLED.

    `worker_token`, dacă e dat, adaugă și gardă de PROPRIETATE (vezi comentariul
    coloanei din `init_job_queue`): un worker căruia i s-a reprogramat jobul sub
    picioare (`requeue_running_jobs`) nu mai poate scrie progres peste rularea
    NOUĂ care l-a revendicat între timp, chiar dacă statusul e din nou RUNNING.
    """
    init_job_queue(db_path)
    pct_i = max(0, min(100, int(pct)))
    line = str(log_msg or "").strip()
    if not line:
        state = get_job_status(job_id, db_path=db_path)
        if state is None or state.get("status") != JOB_RUNNING:
            return True
        if worker_token is not None and state.get("worker_token") != worker_token:
            return True
        return False
    ts = datetime.now().strftime("%H:%M:%S")
    stamped = f"[{ts}] {line}"
    where = "id = ? AND status = ?"
    params: tuple = (int(job_id), JOB_RUNNING)
    if worker_token is not None:
        where += " AND worker_token = ?"
        params = params + (worker_token,)
    with _conn(db_path) as conn:
        cur = conn.execute(
            f"""
            UPDATE jobs
            SET progress_pct = ?, log_tail = CASE
                WHEN log_tail IS NULL OR log_tail = '' THEN ?
                ELSE substr(log_tail || char(10) || ?, -6000)
            END
            WHERE {where}
            """,
            (pct_i, stamped, stamped) + params,
        )
        conn.commit()
        updated = cur.rowcount > 0

    if not updated:
        return True
    # Prinde și o anulare/reprogramare comisă imediat după tranzacția noastră.
    state = get_job_status(job_id, db_path=db_path)
    if state is None or state.get("status") != JOB_RUNNING:
        return True
    if worker_token is not None and state.get("worker_token") != worker_token:
        return True
    return False


def complete_job(
    job_id: int,
    result_json: str,
    db_path: str = DB_PATH,
    worker_token: str | None = None,
) -> bool:
    """Scrie rezultatul jobului. Întoarce True dacă rândul a fost ACTUALIZAT.

    UPDATE-ul e condiționat de `status = RUNNING` și, dacă `worker_token` e dat,
    și de PROPRIETATE (`worker_token = ?`). Dacă între timp altcineva a schimbat
    starea (tipic: `requeue_running_jobs()` al unui al doilea worker pornit peste
    primul → RUNNING revine la PENDING, apoi e revendicat de noul worker), UPDATE-ul
    prinde 0 rânduri și rezultatul se PIERDE din coadă (worker.py îl salvează
    separat pe disc — vezi `_dump_orphan_result`). Fără garda de token, un worker
    STALE care termina DUPĂ ce jobul redevenise RUNNING sub alt worker ar fi
    suprascris tăcut rezultatul acelei rulări noi cu propriul rezultat vechi.
    """
    init_job_queue(db_path)
    where = "id = ? AND status = ?"
    params: tuple = (int(job_id), JOB_RUNNING)
    if worker_token is not None:
        where += " AND worker_token = ?"
        params = params + (worker_token,)
    with _conn(db_path) as conn:
        cur = conn.execute(
            f"""
            -- completed_at TREBUIE să rămână text UTC din CURRENT_TIMESTAMP
            -- ('YYYY-MM-DD HH:MM:SS'): UI-ul (_completed_age_seconds) îl parsează ca
            -- naiv-UTC. Nu-l scrie din Python (ar fi local → vechime greșită).
            UPDATE jobs
            SET status = ?, progress_pct = 100, result_json = ?, completed_at = CURRENT_TIMESTAMP
            WHERE {where}
            """,
            (JOB_COMPLETED, result_json) + params,
        )
        conn.commit()
        ok = cur.rowcount > 0
    if not ok:
        logger.error(
            "[job_queue] complete_job(%s): 0 rânduri actualizate — jobul nu mai era "
            "RUNNING (sau nu mai era al acestui worker_token). REZULTATUL S-A PIERDUT "
            "DIN COADĂ.",
            job_id,
        )
    return ok


def get_latest_completed_job(db_path: str = DB_PATH) -> dict[str, Any] | None:
    """Read-only: cel mai recent job COMPLETED (cu completed_at, dacă există).

    Folosit de UI la pornire ca să recupereze un job care s-a terminat cât UI-ul
    era jos — get_active_job() întoarce doar PENDING/RUNNING, deci altfel rezultatul
    (și mail-ul/shutdown-ul de la final) ar rămâne orfan. UI-ul decide pe baza
    vechimii (completed_at) dacă mai declanșează mail/shutdown sau doar afișează."""
    init_job_queue(db_path)
    with _conn(db_path) as conn:
        # După completed_at (cea mai recentă finalizare reală), cu id ca tie-break.
        # NULLS LAST: joburile vechi fără completed_at nu „bat" unul proaspăt.
        row = conn.execute(
            "SELECT * FROM jobs WHERE status = ? "
            "ORDER BY (completed_at IS NULL), completed_at DESC, id DESC LIMIT 1",
            (JOB_COMPLETED,),
        ).fetchone()
    return dict(row) if row else None


def fail_job(
    job_id: int, error_msg: str, db_path: str = DB_PATH, worker_token: str | None = None
) -> bool:
    """Marchează jobul ca FAILED. Întoarce True dacă rândul a fost actualizat
    (vezi nota din `complete_job` — aceeași cursă de stare).

    `worker_token`, dacă e dat, adaugă gardă de PROPRIETATE: excluderea veche
    (`status NOT IN (COMPLETED, CANCELLED)`) las̆a un worker STALE (căruia jobul
    i-a fost reprogramat de `requeue_running_jobs()`) să eșueze un job aflat din
    nou PENDING (așteptând reluare) sau RUNNING sub un alt worker — crash-ul
    workerului vechi, fără nicio legătură cu rularea nouă, marca FAILED munca
    legitimă în curs. Cu token dat, garda cere ȘI potrivirea proprietarului.
    """
    init_job_queue(db_path)
    msg = str(error_msg or "Unknown worker error")
    where = "id = ? AND status NOT IN (?, ?)"
    params: tuple = (int(job_id), JOB_COMPLETED, JOB_CANCELLED)
    if worker_token is not None:
        where += " AND worker_token = ?"
        params = params + (worker_token,)
    with _conn(db_path) as conn:
        cur = conn.execute(
            f"""
            UPDATE jobs
            SET status = ?, result_json = ?, log_tail = ?
            WHERE {where}
            """,
            (JOB_FAILED, msg, msg[-6000:]) + params,
        )
        conn.commit()
        ok = cur.rowcount > 0
    if not ok:
        logger.warning(
            "[job_queue] fail_job(%s): 0 rânduri actualizate (jobul era deja "
            "COMPLETED/CANCELLED, sau nu mai era al acestui worker_token).",
            job_id,
        )
    return ok


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
    with _conn(db_path) as conn:
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


def fetch_pending_job(
    db_path: str = DB_PATH, worker_token: str | None = None
) -> dict[str, Any] | None:
    """Revendică cel mai vechi job PENDING. `worker_token`, dacă e dat, se scrie pe
    rând ca dovadă de proprietate pentru `complete_job`/`fail_job`/`update_job_progress`."""
    return _claim_job(
        db_path,
        where_sql="status = ?",
        where_params=(JOB_PENDING,),
        update_sql="UPDATE jobs SET status = ?, progress_pct = 1, worker_token = ? WHERE id = ?",
        update_extra_params=(JOB_RUNNING, worker_token),
    )


def fetch_running_job(
    db_path: str = DB_PATH, worker_token: str | None = None
) -> dict[str, Any] | None:
    """Preluăm job-uri RUNNING care nu au fost procesate încă (fallback la restart worker).

    Pragul e <= 1 (doar claim-uit, niciodată atins de worker): orice job cu pct >= 2
    a fost deja preluat („Job preluat de worker.") și e PROPRIETATEA acelui worker —
    un al doilea worker nu are voie să-l fure cât primul încarcă pandas/engine
    (pct 2-5). Orfanii cu pct >= 2 se recuperează la următorul start de worker
    (requeue_running_jobs), nu aici. `worker_token`, dacă e dat, marchează noul
    proprietar (vezi `fetch_pending_job`)."""
    return _claim_job(
        db_path,
        where_sql="status = ? AND progress_pct <= 1",
        where_params=(JOB_RUNNING,),
        update_sql="UPDATE jobs SET progress_pct = 2, worker_token = ? WHERE id = ?",
        update_extra_params=(worker_token,),
    )


def cancel_pending_running_jobs(
    reason: str = "Oprit de utilizator",
    db_path: str = DB_PATH,
    job_ids: "list[int] | tuple[int, ...] | None" = None,
) -> int:
    """Soft cancel: marchează joburile PENDING/RUNNING drept CANCELLED (fără DELETE).

    `job_ids` restrânge anularea la ID-urile date. FĂRĂ el se anulează TOT ce e
    în zbor — corect pentru „sesiune nouă" (LOTO_FRESH_START), dar GREȘIT pentru
    calea de la pornirea UI-ului care voia să anuleze UN SINGUR job orfan:
    mesajul numea un id anume, dar UPDATE-ul nu avea filtru, deci un job aflat
    la 60% era anulat împreună cu orfanul.
    """
    init_job_queue(db_path)
    msg = str(reason or "Oprit de utilizator")
    ids = [int(j) for j in (job_ids or [])]
    where = "status IN (?, ?)"
    params: tuple = (JOB_CANCELLED, msg, msg, msg, JOB_PENDING, JOB_RUNNING)
    if ids:
        where += f" AND id IN ({','.join('?' * len(ids))})"
        params = params + tuple(ids)
    with _conn(db_path) as conn:
        cur = conn.execute(
            f"""
            UPDATE jobs
            SET status = ?, result_json = ?, log_tail = CASE
                WHEN log_tail IS NULL OR log_tail = '' THEN ?
                ELSE substr(log_tail || char(10) || ?, -6000)
            END
            WHERE {where}
            """,
            params,
        )
        conn.commit()
        return cur.rowcount


def is_job_cancelled(job_id: int, db_path: str = DB_PATH) -> bool:
    """Check if a specific job has been cancelled."""
    init_job_queue(db_path)
    with _conn(db_path) as conn:
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
            with _conn(db_path) as conn:
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
    with _conn(db_path) as conn:
        conn.execute("DELETE FROM pipeline_cache")
        conn.commit()


def requeue_running_jobs(
    db_path: str = DB_PATH, worker_token: str | None = None
) -> int:
    """Move orphan RUNNING jobs back to PENDING (useful after worker restarts/crashes).

    Fără `worker_token` (implicit, folosit la PORNIREA workerului): reprogramează
    TOATE joburile RUNNING — la start nu putem ști ale cui erau, iar scopul e
    recuperarea după un crash al unui worker anterior oarecare.

    Cu `worker_token` (folosit la OPRIRE gracioasă — SIGTERM/SIGINT/atexit):
    reprogramează DOAR jobul revendicat de ACEST worker. Înainte, oprirea
    oricărui worker reprograma și joburile RUNNING ale altor workeri încă vii
    (dublu-pornire accidentală, sau DB-ul partajat laptop↔ALF din istoricul
    proiectului) — jobul lor legitim era smuls la mijloc și putea fi revendicat
    și dublu-procesat de un al treilea worker.
    """
    init_job_queue(db_path)
    where = "status = ?"
    params: tuple = (JOB_RUNNING,)
    if worker_token is not None:
        where += " AND worker_token = ?"
        params = params + (worker_token,)
    with _conn(db_path) as conn:
        cur = conn.execute(
            f"""
            UPDATE jobs
            SET status = ?, log_tail = CASE
                WHEN log_tail IS NULL OR log_tail = '' THEN ?
                ELSE substr(log_tail || char(10) || ?, -6000)
            END
            WHERE {where}
            """,
            (
                JOB_PENDING,
                "Worker restart detectat: job reprogramat automat.",
                "Worker restart detectat: job reprogramat automat.",
            )
            + params,
        )
        conn.commit()
        return int(getattr(cur, "rowcount", 0) or 0)


def fail_running_jobs(
    reason: str = "Job oprit automat la startup.", db_path: str = DB_PATH
) -> int:
    """Mark all RUNNING jobs as FAILED (startup safety cleanup)."""
    try:
        init_job_queue(db_path)
        msg = str(reason or "Job oprit automat la startup.")
        with _conn(db_path) as conn:
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
            return int(getattr(cur, "rowcount", 0) or 0)
    except Exception as e:
        import logging

        logging.warning(
            f"fail_running_jobs: eroare în timpul procesării {db_path}: {e}"
        )
        return 0


def get_pipeline_cache(input_hash: str, db_path: str = DB_PATH) -> str | None:
    """Return cached pipeline result for a specific input hash."""
    key = str(input_hash or "").strip()
    if not key:
        return None
    init_job_queue(db_path)
    with _conn(db_path) as conn:
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


def put_pipeline_cache(
    input_hash: str, result_json: str, db_path: str = DB_PATH
) -> None:
    """Insert/update cached pipeline result for current input hash."""
    key = str(input_hash or "").strip()
    if not key:
        return
    init_job_queue(db_path)
    with _conn(db_path) as conn:
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

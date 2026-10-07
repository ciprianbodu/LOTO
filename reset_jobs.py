"""Resetează coada de joburi (golește tabela `jobs` din loto_jobs.db) la pornire.

Numerotarea joburilor (#1, #2, …) e `id INTEGER PRIMARY KEY` în SQLite. Golind
tabela, următorul job inserat primește din nou id = 1.

START_8000.bat omoară UI + worker + bench + copiii ProcessPool
(`cleanup_old_processes.py`), APOI rulează ăsta cu `--force`.
PENDING/RUNNING rămase sunt cadavre (procesele au fost deja omorâte) — dacă le
păstrăm, noul worker le reia singur, iar UI-ul arată la o pornire goală:
  «⏳ Job în rulare (#1) — 0% / se inițializează...».
Păstrăm DOAR cel mai recent job COMPLETED dacă NU a fost încă preluat de UI
(`ui_finalized_at` gol pe rândul lui), ca rezultatul să nu se piardă.
Pe START_8000 recovery-ul e display-only (fără mail/WF/shutdown); finalizarea
automată rămâne permisă doar la restart direct al UI-ului, fără fresh-start.
Dacă nimic nu califică (cazul normal la început de sesiune) → golire COMPLETĂ +
VACUUM → următorul job e #1.

Rulare:
    .venv\\Scripts\\python reset_jobs.py            # refuză dacă există RUNNING
    .venv\\Scripts\\python reset_jobs.py --force     # șterge și RUNNING (după kill)
"""

from __future__ import annotations

import os
import sqlite3
import sys

# Consola CMD poate rămâne cp1252; mesajele românești și simbolurile de status
# nu trebuie să transforme un reset reușit într-un exit 1.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

# Sursa unică pentru calea DB (în afara OneDrive — vezi job_queue._default_db_path).
try:
    from job_queue import DB_PATH as DB, init_job_queue as _init_job_queue
except Exception:  # noqa: BLE001
    DB = "loto_jobs.db"
    _init_job_queue = None


def main() -> int:
    if not os.path.exists(DB):
        print(f"Nu există {DB} — coada e deja goală. Următorul job va fi #1.")
        return 0

    # `reset_jobs.py` citește direct `completed_at`, deci trebuie să aplice
    # aceeași migrare aditivă ca UI-ul/worker-ul înainte de primul SELECT.
    # Altfel o bază creată de o versiune veche oprește fresh-start-ul exact când
    # launcherul încearcă s-o repare.
    if _init_job_queue is not None:
        _init_job_queue(DB)

    force = "--force" in sys.argv
    # `busy_timeout` + WAL, ca în `job_queue._conn`: scriptul rulează din
    # START_8000.bat exact când UI-ul/worker-ul pot avea încă DB-ul deschis, iar un
    # `sqlite3.connect` gol iese instant cu „database is locked" în loc să aștepte.
    con = sqlite3.connect(DB, timeout=30.0)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=30000")
    except sqlite3.Error:
        pass  # DB nou/read-only: mergem mai departe ca înainte
    try:
        # BEGIN IMMEDIATE ia lock-ul de scriere ÎNAINTE de verificarea RUNNING,
        # nu doar înaintea DELETE-ului final. Fără asta, verificarea RUNNING și
        # DELETE-ul erau instrucțiuni separate, necuprinse în nicio tranzacție
        # explicită (SELECT-urile din sqlite3 rulează în autocommit) — un worker
        # putea trece un job PENDING în RUNNING exact în fereastra dintre ele, iar
        # DELETE FROM jobs WHERE id NOT IN (...) îl ștergea tăcut, deși garda de
        # mai sus tocmai raportase 0 job-uri RUNNING. Cu lock-ul luat aici, orice
        # alt scriitor așteaptă (busy_timeout) sau eșuează — nu mai poate strecura
        # o tranziție de stare între verificare și ștergere.
        con.execute("BEGIN IMMEDIATE")
        running = con.execute(
            "SELECT COUNT(*) FROM jobs WHERE status = 'RUNNING'"
        ).fetchone()[0]
        if running and not force:
            con.execute("ROLLBACK")
            print(
                f"⚠️  {running} job(uri) RUNNING. Oprește-le întâi (butonul "
                f"'🔴 Anulează TOT Procesul') sau rulează cu --force."
            )
            return 1

        total = con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

        # Păstrăm DOAR un rezultat COMPLETED pe care UI-ul nu l-a preluat încă
        # (mail/shutdown). PENDING/RUNNING nu: START_8000 a omorât worker-ul, deci
        # nu e muncă în curs — e un job-fantomă care ar reapărea la fiecare pornire.
        # Marcajul stă pe rând: golirea tabelei îl șterge odată cu jobul, deci un
        # job nou cu același id pornește nemarcat.
        keep: set[int] = set()
        row = con.execute(
            "SELECT id, ui_finalized_at FROM jobs WHERE status = 'COMPLETED' "
            "ORDER BY (completed_at IS NULL), completed_at DESC, id DESC LIMIT 1"
        ).fetchone()
        if row and row[1] is None:
            keep.add(int(row[0]))  # terminat, nepreluat de UI → recuperarea are nevoie de el

        if keep:
            placeholders = ",".join("?" * len(keep))
            con.execute(
                f"DELETE FROM jobs WHERE id NOT IN ({placeholders})",
                tuple(sorted(keep)),
            )
            con.commit()
            print(
                f"✅ Șterse {total - len(keep)} joburi; PĂSTRAT {len(keep)} "
                f"rezultat nefinalizat: {sorted(keep)}. "
                f"(Nu resetez numerotarea — recuperarea UI are nevoie de id-ul ăsta.)"
            )
        else:
            con.execute("DELETE FROM jobs")
            con.commit()
            # DELETE+commit de mai sus a golit deja tabela cu succes — VACUUM
            # e doar recuperare de spațiu pe disc + resetarea numerotării, NU
            # o condiție de corectitudine. VACUUM cere un lock mai exclusiv
            # decât un simplu write (poate eșua pe un lock rezidual imediat
            # după ce cleanup_old_processes.py tocmai a omorât workerul/UI-ul
            # vechi, sau pe disc plin) — o excepție nepri­nsă aici ar opri
            # PORNIREA întregii aplicații pentru un pas pur cosmetic, deși
            # coada e deja corect goală.
            next_is_one = True
            try:
                con.execute("VACUUM")
            except sqlite3.Error as exc:
                next_is_one = False
                print(
                    f"⚠️  VACUUM eșuat ({exc}) — coada e golită oricum; "
                    "numerotarea job-urilor s-ar putea sa nu reînceapă de la 1."
                )
            msg = f"✅ Șterse {total} joburi din coadă."
            if next_is_one:
                msg += " Următorul job va fi #1."
            print(msg)
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())

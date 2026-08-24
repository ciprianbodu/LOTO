"""Resetează coada de joburi (golește tabela `jobs` din loto_jobs.db) la pornire.

Numerotarea joburilor (#1, #2, …) e `id INTEGER PRIMARY KEY` în SQLite. Golind
tabela, următorul job inserat primește din nou id = 1.

START_8000.bat omoară UI + worker + bench, APOI rulează ăsta cu `--force`.
PENDING/RUNNING rămase sunt cadavre (procesele au fost deja omorâte) — dacă le
păstrăm, noul worker le reia singur, iar UI-ul arată la o pornire goală:
  «⏳ Job în rulare (#1) — 0% / se inițializează...».
Păstrăm DOAR cel mai recent job COMPLETED dacă NU a fost încă finalizat de UI
(id ≠ `last_finalized_job_id` din .ui_state.json) — altfel mail/shutdown de la
`_recover_completed_job` s-ar pierde. Dacă nimic nu califică (cazul normal la
început de sesiune) → golire COMPLETĂ + VACUUM → următorul job e #1.

Rulare:
    .venv\\Scripts\\python reset_jobs.py            # refuză dacă există RUNNING
    .venv\\Scripts\\python reset_jobs.py --force     # șterge și RUNNING (după kill)
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

# Sursa unică pentru calea DB (în afara OneDrive — vezi job_queue._default_db_path).
try:
    from job_queue import DB_PATH as DB
except Exception:  # noqa: BLE001
    DB = "loto_jobs.db"


def _last_finalized_job_id() -> int:
    """Ultimul job dus prin finalize (mail/shutdown) de UI, din .ui_state.json.
    Un job COMPLETED cu id ≠ ăsta = încă neprocesat → îl păstrăm pentru recuperare."""
    try:
        f = Path(__file__).resolve().parent / ".ui_state.json"
        if f.exists():
            data = json.loads(f.read_text(encoding="utf-8"))
            return int(data.get("last_finalized_job_id", 0) or 0)
    except Exception:  # noqa: BLE001
        pass
    return 0


def main() -> int:
    if not os.path.exists(DB):
        print(f"Nu există {DB} — coada e deja goală. Următorul job va fi #1.")
        return 0

    force = "--force" in sys.argv
    con = sqlite3.connect(DB)
    try:
        running = con.execute(
            "SELECT COUNT(*) FROM jobs WHERE status = 'RUNNING'"
        ).fetchone()[0]
        if running and not force:
            print(f"⚠️  {running} job(uri) RUNNING. Oprește-le întâi (butonul "
                  f"'🔴 Anulează TOT Procesul') sau rulează cu --force.")
            return 1

        total = con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

        # Păstrăm DOAR un rezultat COMPLETED pe care UI-ul nu l-a finalizat încă
        # (mail/shutdown). PENDING/RUNNING nu: START_8000 a omorât worker-ul, deci
        # nu e muncă în curs — e un job-fantomă care ar reapărea la fiecare pornire.
        keep: set[int] = set()
        last_fin = _last_finalized_job_id()
        row = con.execute(
            "SELECT id FROM jobs WHERE status = 'COMPLETED' "
            "ORDER BY (completed_at IS NULL), completed_at DESC, id DESC LIMIT 1"
        ).fetchone()
        if row and int(row[0]) != last_fin:
            keep.add(int(row[0]))  # terminat, neprocesat de UI → recuperarea are nevoie de el

        if keep:
            placeholders = ",".join("?" * len(keep))
            con.execute(f"DELETE FROM jobs WHERE id NOT IN ({placeholders})", tuple(sorted(keep)))
            con.commit()
            print(f"✅ Șterse {total - len(keep)} joburi; PĂSTRAT {len(keep)} "
                  f"rezultat nefinalizat: {sorted(keep)}. "
                  f"(Nu resetez numerotarea — recuperarea UI are nevoie de id-ul ăsta.)")
        else:
            con.execute("DELETE FROM jobs")
            con.commit()
            con.execute("VACUUM")  # recuperează spațiu + următorul job devine #1
            print(f"✅ Șterse {total} joburi din coadă. Următorul job va fi #1.")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())

"""Resetează coada de joburi la #1 (golește tabela `jobs` din loto_jobs.db).

Numerotarea joburilor (#1, #2, …) e `id INTEGER PRIMARY KEY` în SQLite. Golind
tabela, următorul job inserat primește din nou id = 1.

⚠️ Rulează DOAR când NU rulează niciun job (altfel pierzi jobul curent). Scriptul
refuză dacă există un job RUNNING (folosește --force ca să forțezi).

Rulare:
    .venv\\Scripts\\python reset_jobs.py
"""
from __future__ import annotations

import os
import sqlite3
import sys

# Sursa unică pentru calea DB (în afara OneDrive — vezi job_queue._default_db_path).
try:
    from job_queue import DB_PATH as DB
except Exception:  # noqa: BLE001
    DB = "loto_jobs.db"


def main() -> int:
    if not os.path.exists(DB):
        print(f"Nu există {DB} — coada e deja goală. Următorul job va fi #1.")
        return 0

    con = sqlite3.connect(DB)
    try:
        running = con.execute(
            "SELECT COUNT(*) FROM jobs WHERE status = 'RUNNING'"
        ).fetchone()[0]
        if running and "--force" not in sys.argv:
            print(f"⚠️  {running} job(uri) RUNNING. Oprește-le întâi (butonul "
                  f"'🔴 Anulează TOT Procesul') sau rulează cu --force.")
            return 1

        total = con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        con.execute("DELETE FROM jobs")
        con.commit()
        # VACUUM ca să recupereze spațiul și să reseteze starea internă a tabelei.
        con.execute("VACUUM")
        print(f"✅ Șterse {total} joburi din coadă. Următorul job va fi #1.")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())

"""Resetează coada de joburi (golește tabela `jobs` din loto_jobs.db) la pornire.

Numerotarea joburilor (#1, #2, …) e `id INTEGER PRIMARY KEY` în SQLite. Golind
tabela, următorul job inserat primește din nou id = 1.

START_8000.bat omoară UI + worker + bench, APOI rulează ăsta cu `--force`.
PENDING/RUNNING rămase sunt cadavre (procesele au fost deja omorâte) — dacă le
păstrăm, noul worker le reia singur, iar UI-ul arată la o pornire goală:
  «⏳ Job în rulare (#1) — 0% / se inițializează...».
Păstrăm DOAR cel mai recent job COMPLETED dacă NU a fost încă finalizat de UI
(id ≠ `last_finalized_job_id` din .ui_state.json), ca rezultatul să nu se piardă.
Pe START_8000 recovery-ul e display-only (fără mail/WF/shutdown); finalizarea
automată rămâne permisă doar la restart direct al UI-ului, fără fresh-start.
Dacă nimic nu califică (cazul normal la început de sesiune) → golire COMPLETĂ +
VACUUM → următorul job e #1.

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


def _clear_last_finalized_job_id() -> bool:
    """Zerează `last_finalized_job_id` din .ui_state.json. Întoarce True dacă a schimbat ceva.

    OBLIGATORIU pe ramura de golire COMPLETĂ: `id INTEGER PRIMARY KEY` fără
    AUTOINCREMENT înseamnă că după `DELETE FROM jobs` numerotarea reîncepe de la
    1 (chiar asta promite mesajul „Următorul job va fi #1"), în timp ce markerul
    din .ui_state.json supraviețuiește între sesiuni. Cele două lifetime-uri sunt
    independente, deci un job NOU putea primi exact id-ul deja marcat ca
    finalizat → `_recover_completed_job` ieșea pe `jid == already` și un rezultat
    REAL nu mai era nici afișat, nici trecut prin mail/shutdown, fără nicio linie
    de log care să explice de ce. Golirea tabelei face markerul lipsit de sens,
    deci îl ștergem odată cu ea.
    """
    f = Path(__file__).resolve().parent / ".ui_state.json"
    try:
        if not f.exists():
            return False
        data = json.loads(f.read_text(encoding="utf-8"))
        if not int(data.get("last_finalized_job_id", 0) or 0):
            return False
        data["last_finalized_job_id"] = 0
        try:
            from ui_shared import atomic_write_json  # scriere atomică (regula de aur 3)
            atomic_write_json(f, data)
        except Exception:  # noqa: BLE001 — ui_shared indisponibil: tmp+replace local
            tmp = f.with_name(f"{f.name}.{os.getpid()}.reset.tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, f)
        return True
    except Exception as exc:  # noqa: BLE001 — pornirea NU trebuie blocată de asta
        print(f"⚠️  Nu am putut reseta last_finalized_job_id: {exc}")
        return False


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
            _cleared = _clear_last_finalized_job_id()
            print(f"✅ Șterse {total} joburi din coadă. Următorul job va fi #1."
                  + ("  (am resetat și last_finalized_job_id — id-urile reîncep de la 1)"
                     if _cleared else ""))
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())

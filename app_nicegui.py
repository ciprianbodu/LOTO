"""Front-end NiceGUI pentru Loto Enterprise Wheeling.

Înlocuiește app.py (Streamlit). Motivul migrării: modelul Streamlit re-rula tot
scriptul și — ca să detecteze terminarea bench-ului din background — injecta un
reload COMPLET de pagină (window.location.reload), care ștergea session_state și
golea uploader-ul → bug-uri recurente ("Încărcați un CSV!", bife pierdute etc.).

NiceGUI ține starea pe server și actualizează componentele prin websocket cu
`ui.timer` — fără reload, deci starea nu se mai pierde NICIODATĂ.

Backend-ul (job_queue.py SQLite, worker.py subprocess, loto_engine, tot
loto_enterprise/) e reutilizat NEATINS. Contractul config_json/result e identic
cu cel din app.py, deci worker-ul nu știe ce UI l-a chemat.

Rulare:  python app_nicegui.py   (sau: python -m app_nicegui)
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime as _dt, timezone as _tz
from pathlib import Path

import pandas as pd
from nicegui import app, ui

from job_queue import (
    cancel_pending_running_jobs,
    get_active_job,
    get_job_status,
    get_latest_completed_job,
    init_job_queue,
    is_fresh_ui_start,
    is_unstarted_job,
    submit_job,
)
from runtime_paths import BENCH_LOG_FILE
from ui_shared import (
    PROJECT_ROOT,
    atomic_write_json,
    atomic_write_text,
    clear_logs,
    decode_queue_result,
    ensure_worker_running,
    is_worker_running,
    load_mail_config,
    read_logs_filtered,
    read_tail_lines,
    render_html_safe,
    send_email,
)

logging.basicConfig(
    level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s"
)
logger = logging.getLogger("app_nicegui")

from ui_runtime import *  # noqa: F403
from ui_results import *  # noqa: F403
from ui_bench import *  # noqa: F403
from ui_hits import *  # noqa: F403

def _build_config_json(sim_depth_per_game: dict | None = None) -> str:
    sim_depth_per_game = sim_depth_per_game or {}
    h = hashlib.sha256()
    for k in (
        "pool_size_val",
        "guarantee_val",
        "max_variants_val",
        "lookback_val",
        "sim_depth_val",
        "bench_hit_target",
        "wheel_condition_val",
        "recent_penalty_draws_val",
        "recent_penalty_factor_val",
    ):
        h.update(str(SETTINGS.get(k, DEFAULTS.get(k))).encode("utf-8"))
    # Praguri active PER JOC — bifa oprită anulează toate, indiferent de ce mai
    # e tastat în câmpuri. Calculat o singură dată, pentru toate cele trei jocuri
    # canonice (nu doar pentru cele efectiv încărcate): hash-ul reprezintă
    # întreaga submisie (`PIPELINE_CACHE_VERSION:input_hash`, un singur cache_key
    # pentru tot job-ul), deci trebuie să reflecte TOATE restrângerile alese, nu
    # doar jocul ultimului fișier procesat în bucla de mai jos.
    _rb_by_game = {
        label: _active_restrict_base(label) for label, _s, _m in _RESTRICT_BASE_GAMES
    }
    h.update(str(sorted(_rb_by_game.items())).encode("utf-8"))
    # Semantica restrângerii intră în hash DOAR când e activă undeva: un rezultat
    # cache-uit sub regula veche (interval mai îngust decât un bilet aplicat, nu
    # ignorat) nu are voie să fie servit sub cea nouă. Fără nicio restricție,
    # hash-ul rămâne cel dinainte, deci cache-urile existente continuă să fie folosite.
    if any(lo or hi for lo, hi in _rb_by_game.values()):
        h.update(f"restrict_semantics={_RESTRICT_SEMANTICS}".encode("utf-8"))
    h.update(
        str(sorted(sim_depth_per_game.items())).encode("utf-8")
    )  # adâncime per joc → cache key
    # `pure_bench` NU intră în hash: taskul emite `"pure_bench_mode": True`
    # NECONDIȚIONAT (mai jos), iar `loto_engine` scrie `audit["pure_bench_mode"] = True`
    # indiferent de argument — deci „pure" vs „normal" produce EXACT aceleași bilete.
    # Cât era în hash, cele două variante primeau chei de cache diferite pentru un
    # rezultat identic: cache ratat degeaba dacă `use_cache` ar fi True.
    datasets_cfg = []
    for fname, df in STATE["datasets"]:
        g_label = _game_label_for(fname)
        df_json = df.to_json(orient="split")
        # adâncime backtesting: per joc (din Auto-Pilot) dacă există, altfel globală
        _sd_pg = sim_depth_per_game.get(g_label)
        sd = int(_sd_pg) if _sd_pg is not None else _int_setting("sim_depth_val")
        _rb_min, _rb_max = _rb_by_game.get(g_label, (0, 0))
        task = {
            "game_label": g_label,
            "pool_size": _int_setting("pool_size_val"),
            "guarantee": _int_setting("guarantee_val"),
            "max_variants": _int_setting("max_variants_val"),
            "wheel_condition": _int_setting("wheel_condition_val"),
            "recent_penalty_draws": _int_setting("recent_penalty_draws_val"),
            "recent_penalty_factor": _float_setting("recent_penalty_factor_val"),
            "restrict_base_max": _rb_max,
            "restrict_base_min": _rb_min,
            "lookback": _int_setting("lookback_val"),
            "sim_depth_pct": sd,  # TELEMETRIE de bench, nu taie istoricul (vezi AGENTS.md)
            # Mereu True: singurul mod de generare care există azi (scoring → top-N →
            # wheel, fără filtre). Rămâne în contractul worker↔UI (regula de aur 2).
            "pure_bench_mode": True,
            "bench_hit_target": _clamped_bench_target(),
        }
        datasets_cfg.append(
            {
                "fname": fname,
                "df_json": df_json,
                "tasks": [task],
            }
        )
        h.update(fname.encode("utf-8"))
        h.update(hashlib.sha256(df_json.encode("utf-8")).hexdigest().encode("ascii"))
    return json.dumps(
        {"input_hash": h.hexdigest(), "use_cache": False, "datasets": datasets_cfg}
    )


def submit_generation(
    pure: bool = False, sim_depth_per_game: dict | None = None
) -> None:
    if not STATE["datasets"]:
        ui.notify("Încărcați cel puțin un fișier CSV!", type="negative")
        return
    if STATE["active_job_id"]:
        ui.notify("Există deja un job în rulare.", type="warning")
        return
    STATE["pure_bench"] = pure
    STATE["results"] = None
    STATE["retro"] = {}
    STATE["wf_status"] = ""
    ensure_worker_running()
    cfg = _build_config_json(sim_depth_per_game)
    job_id = submit_job("pipeline", cfg)
    STATE["active_job_id"] = int(job_id)
    STATE["job_start_time"] = time.time()
    STATE["job_elapsed"] = None  # reset; se fixează la COMPLETED
    STATE["wf_elapsed"] = None  # reset; se fixează la finalul walk-forward
    ui.notify(f"Job #{job_id} trimis.", type="positive")
    _refresh_status()


def apply_autopilot_and_generate() -> None:
    """Aplică scorer-ul (și sim_depth de telemetrie) din best_methods.json, apoi generează.

    sim_depth e fereastra de bench unde avg_hits a picat — se stochează în audit,
    NU taie pool-ul și NU schimbă biletele. Mesajul de notify nu mai pretinde
    că «@ 50%» modifică tichetele.
    """
    # best_methods.json folosește CHEIA jocului (loto_6_49 ...), nu eticheta scurtă
    # (6/49) întoarsă de _game_label_for → altfel lookup-ul eșua mereu → fallback.
    _LABEL_TO_KEY = _LABEL_TO_FOLDS_GAME
    per_game: dict = {}  # {game_label: sim_depth_pct} — telemetrie per joc, nu filtru
    try:
        from loto_enterprise.core.method_selector import recommend_optimal_config

        recs = []
        for fname, _ in STATE["datasets"]:
            label = _game_label_for(fname)
            gk = _LABEL_TO_KEY.get(label, "loto_6_49")
            cfg = recommend_optimal_config(gk, _int_setting("pool_size_val"))
            if cfg and not cfg.get("fallback"):
                sd = int(cfg.get("sim_depth_pct", SETTINGS["sim_depth_val"]))
                per_game[label] = sd
                low = " · low_confidence" if cfg.get("low_confidence") else ""
                # Substituirea nearest-k: bench-ul n-a evaluat pool-ul cerut, deci
                # scorer-ul (și cifrele lui) vin de la ALT pool. Până acum se vedea
                # doar ca linie INFO în loto.log, iar notificarea îl prezenta ca și
                # cum ar fi fost măsurat la pool-ul de pe slider.
                _sub = cfg.get("pool_substituted") or {}
                sub = (
                    f" · ⚠ măsurat la pool {_sub['used']}, nu {_sub['requested']}"
                    if _sub
                    else ""
                )
                recs.append(f"{gk}: {cfg.get('scorer')}{low}{sub}")
        if recs:
            ui.notify(
                "Auto-Pilot (scorer per joc, din Re-Bench): " + " | ".join(recs),
                type="info",
            )
        else:
            ui.notify(
                "Fără decizie bench încă — rulează un Re-Bench întâi. Folosesc setările curente.",
                type="warning",
            )
    except Exception as exc:  # noqa: BLE001
        ui.notify(
            f"Auto-Pilot indisponibil ({exc}); folosesc setările curente.",
            type="warning",
        )
    submit_generation(pure=False, sim_depth_per_game=per_game)


# --------------------------------------------------------------------------- #
# Bench (subprocess) + status
# --------------------------------------------------------------------------- #
def _verified_bench_pid() -> int | None:
    """PID-ul din `.bench_pid` DOAR dacă e CHIAR bench-ul pe care l-am pornit.

    `psutil.pid_exists()` singur nu ajunge: nimic nu șterge `.bench_pid` la
    terminarea normală a bench-ului, deci fișierul supraviețuiește cu un PID mort,
    iar Windows reciclează PID-urile. Un proces străin care nimerea acel PID
    bloca la nesfârșit „Un bench rulează deja.", ascundea panoul de rezultate, iar
    la ieșirea lui `_tick` declanșa `_on_bench_finished()` → o generare Auto-Pilot
    NECERUTĂ (și, cu shutdown-ul bifat, o oprire a PC-ului). Aceeași verificare
    apără și `cancel_all()`: fără ea, un `kill_pid_tree(pid)` pe un PID reciclat
    ar omorî tot arborele procesului străin, nu doar l-ar raporta greșit ca „bench".

    Verificăm identitatea, nu doar existența: `create_time` față de timestamp-ul
    scris la lansare (`pid|ts`) și `bench_all_methods.py` în linia de comandă.
    Fișierul stale e șters ca să nu mai fie reevaluat.
    """
    if not BENCH_PID_FILE.exists():
        return None
    try:
        import psutil

        parts = BENCH_PID_FILE.read_text(encoding="utf-8").strip().split("|")
        pid = int(parts[0])
        started = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        proc = psutil.Process(pid)  # NoSuchProcess → stale
        if proc.status() == getattr(psutil, "STATUS_ZOMBIE", "zombie"):
            raise psutil.NoSuchProcess(pid)
        # Identitate: fereastră generoasă (ceasul de pornire poate diferi cu
        # câteva secunde de `int(time.time())` scris de noi).
        if started is not None and abs(proc.create_time() - started) > 60:
            raise psutil.NoSuchProcess(pid)
        try:
            if "bench_all_methods.py" not in " ".join(proc.cmdline() or []):
                raise psutil.NoSuchProcess(pid)
        except (psutil.AccessDenied, psutil.ZombieProcess):
            pass  # nu putem citi cmdline (elevat) → ne bazăm pe create_time
        return pid
    except Exception:  # noqa: BLE001
        # PID mort / reciclat / fișier corupt → curățăm ca să nu blocheze UI-ul.
        try:
            BENCH_PID_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def _bench_running() -> bool:
    return _verified_bench_pid() is not None


def _launch_bench(args: list[str], label: str) -> None:
    if _bench_running():
        ui.notify("Un bench rulează deja.", type="warning")
        return
    py = sys.executable
    cmd = [py, "bench_all_methods.py"] + args
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if os.name == "nt" else 0
    # Bench exclusiv CPU (GPU eliminat complet din aplicație).
    env = dict(os.environ)
    env["LOTO_BENCH_TARGET"] = str(_clamped_bench_target())
    try:
        # bench_all_methods.py își scrie SINGUR bench_full.log (FileHandler) → nu
        # mai redirectăm stdout aici (altfel doi writeri pe același fișier). Logul
        # există acum și pe Windows, vizibil în consola DEBUG.
        proc = subprocess.Popen(
            cmd, cwd=str(PROJECT_ROOT), creationflags=flags, env=env
        )
        atomic_write_text(BENCH_PID_FILE, f"{proc.pid}|{int(time.time())}")
        ui.notify(f"{label} pornit (PID {proc.pid}).", type="positive")
    except Exception as exc:  # noqa: BLE001
        ui.notify(f"Nu pot porni bench-ul: {exc}", type="negative")
    _refresh_status()


_PCTS = (
    "10,30,60,100"  # 4 ferestre: 10% (zona unde 4+ a ieșit cel mai sus în măsurători)
)
# + 30/60/100 (scurt-mediu-lung). NOTĂ: 10% e cea mai SCUMPĂ (antrenare pe ~90%% din istoric
# → rețelele grele fac 25-30 min/fold); 100% e cea mai ieftină. Tunabil aici.


def _on_bench_finished() -> None:
    """Re-Bench (unic) terminat → pornește Auto-Pilot automat (dacă e bifat)."""
    if (
        SETTINGS.get("autopilot_after_bench")
        and not STATE.get("active_job_id")
        and STATE["datasets"]
    ):
        ui.notify("✅ Re-Bench terminat → pornesc Auto-Pilot automat.", type="positive")
        apply_autopilot_and_generate()


def _istoric_has_data() -> bool:
    """True dacă există măcar un CSV în _ISTORIC/ (sursa pe care o citește bench-ul)."""
    try:
        from loto_enterprise.benchmark.runner import _list_istoric_dirs

        for d in _list_istoric_dirs():
            if any(d.glob("*.csv")):
                return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("istoric check: %s", exc)
    return False


def run_rebench() -> None:
    """Re-Bench UNIC, aliniat cu walk-forward-ul de producție.

    ``block-size=1`` recalculează scorul înaintea FIECĂREI extrageri testate,
    exact ca validarea afișată după generare. Varianta rapidă istorică
    (score-once-per-fold, block=99999) putea alege un câștigător care trecea
    poarta din bench, dar pierdea față de random în walk-forward-ul real.

    Controlul pe istoricul amestecat este sărit: dubla timpul și nu participă
    la decizia de producție (``decision.py`` folosește doar ``is_random=False``).
    Baseline-ul scorerului ``random`` rămâne prezent și obligatoriu.
    """
    if _bench_running():
        ui.notify("Un bench rulează deja.", type="warning")
        return
    if not _istoric_has_data():
        ui.notify(
            "Nu există date în _ISTORIC/ — adaugă CSV-urile cu extragerile "
            "(loto_6_49.csv, loto_5_40.csv, joker.csv) înainte de Re-Bench.",
            type="negative",
            timeout=8000,
        )
        return
    if not STATE["datasets"]:
        ui.notify(
            "⚠️ Niciun CSV încărcat în UI — bench-ul va rula, dar Auto-Pilot-ul "
            "de după NU va putea genera pool-uri. Încarcă fișierele la pasul 1.",
            type="warning",
            timeout=8000,
        )
    # Un singur bench, fără --methods (= curarea per-game din producție), scrie
    # best_methods.json. Dacă curarea este inactivă, CLI-ul revine la TOATE.
    # Re-score per extragere: selecția și avertismentul de onestitate măsoară
    # aceeași procedură, nu un pool înghețat la începutul întregului fold.
    _launch_bench(
        [
            "--no-rich",
            "--percentiles",
            _PCTS,
            "--block-size",
            "1",
            "--no-shuffled-control",
        ],
        "Re-Bench walk-forward real (metodele fiecărui joc)",
    )


def _estimate_bench_eta(target_folds: int, overhead: float = 1.25) -> str:
    """ETA bench pe baza ULTIMEI rulări (bench_results/folds.csv): avg runtime_sec
    al folds-urilor reale × nr. folds × overhead. Fallback la estimarea implicită
    dacă nu există bench anterior."""
    default = "~50 min" if target_folds >= 1000 else "~5 min"
    fp = PROJECT_ROOT / "bench_results" / "folds.csv"
    if not fp.exists():
        return default
    try:
        df = pd.read_csv(fp)
        if df.empty or "runtime_sec" not in df.columns:
            return default
        mask = (df.get("failed", False) == False) & (df["runtime_sec"] > 0.05)  # noqa: E712
        real = df[mask] if mask.any() else df
        avg = float(real["runtime_sec"].mean())
        total = avg * target_folds * overhead
        if total < 60:
            return f"~{int(total)} sec"
        if total < 3600:
            return f"~{int(total / 60)} min"
        return f"~{total / 3600:.1f} h"
    except Exception:  # noqa: BLE001
        return default


def _target_bench_folds() -> int:
    """Numărul de folduri pe care Re-Bench-ul UI (`run_rebench`, fără
    `--methods`/`--quick`) urmează să le ruleze — ținta pentru `_estimate_bench_eta`.

    Aceeași formulă din AGENTS.md §5: suma metodelor per joc după
    `resolve_methods_per_game` (curarea + baseline-urile structurale
    random/frequency adăugate acolo, NU numărătoarea brută din
    `apply_curation`, care nu include baseline-urile) ori numărul de ferestre
    din `_PCTS`. Cu curarea inactivă, `resolve_methods_per_game` întoarce {},
    iar `runner._methods_for_game` rulează întreaga listă pe FIECARE joc — de
    aceea fallback-ul de mai jos e `len(kept) * len(games)`, nu 0.

    Întoarce 0 (→ ETA implicit „~5 min"/„~50 min") dacă istoricul sau
    registry-ul de metode nu sunt disponibile la randare — nu trebuie să
    blocheze sidebar-ul.
    """
    try:
        from loto_enterprise.benchmark.curated import (
            apply_curation,
            resolve_methods_per_game,
        )
        from loto_enterprise.benchmark.methods import list_methods, method_meta
        from loto_enterprise.benchmark.runner import discover_games

        avail = [
            m
            for m in list_methods()
            if method_meta(m).get("available", True)
        ]
        kept, info = apply_curation(avail)
        games = discover_games()
        # Aceeași poartă ca bench_all_methods.py: matricea restrânsă per joc se
        # aplică DOAR cu curarea activă (fără `--methods` explicit, cum rulează
        # mereu UI-ul). Cu curarea oprită, `resolve_methods_per_game` ar întoarce
        # totuși o restrângere — CLI-ul nu o folosește în cazul ăsta, deci nici
        # estimarea de-aici nu are voie, altfel supraestimează eronat un bench
        # care de fapt rulează toate metodele pe fiecare joc.
        per_game = (
            resolve_methods_per_game(kept, (g.key for g in games))
            if info.get("active")
            else {}
        )
        total_methods = (
            sum(len(v) for v in per_game.values())
            if per_game
            else len(kept) * len(games)
        )
        n_windows = len([p for p in _PCTS.split(",") if p.strip()])
        return total_methods * max(1, n_windows)
    except Exception as exc:  # noqa: BLE001
        logger.warning("target folds Re-Bench: %s", exc)
        return 0


def _fmt_dur(sec) -> str:
    """Durată granulară în h/m/s: '1h 23m 4s' / '3m 12s' / '45s'."""
    try:
        s = int(round(float(sec)))
    except (TypeError, ValueError):
        return "?"
    if s < 0:
        s = 0
    h, rem = divmod(s, 3600)
    m, sec_ = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {sec_}s"
    if m:
        return f"{m}m {sec_}s"
    return f"{sec_}s"


def _bench_progress_from(log_path, start_ts=None) -> tuple[float, str] | None:
    """(fracție, text live) dintr-un log de bench specific. None dacă logul lipsește.

    Log: `[N/M] [game/method/pct%/REAL|RND/CPU] ...`
    Text (plain, fără HTML): `% (N/M teste) · rămas ~X · acum: joc · metodă · fereastră pct% · REAL/RND`.
    `pct%` = fereastra istorică a fold-ului (ex. ultimele 60% din CSV), NU progresul bench.
    """
    if not log_path.exists():
        return None
    cur = tot = 0
    last_now = ""
    try:
        import re

        # Bench-ul poate produce zeci de MB, pe OneDrive. Citirea integrală la
        # fiecare tick de 1s bloca inutil I/O + event-loop-ul. Ultimul progres
        # este suficient și se află în coada logului.
        txt = "".join(read_tail_lines(log_path, 3000, block=512 * 1024))
        # Ultima linie de progres: [1048/2568] [loto_5_40/ml_svm_rbf/60%/REAL/CPU]
        matches = re.findall(r"\[(\d+)/(\d+)\]\s*\[([^\]]+)\]", txt)
        if matches:
            cur, tot = int(matches[-1][0]), int(matches[-1][1])
            seg = [s.strip() for s in matches[-1][2].split("/") if s.strip()]
            # game / method / window% / REAL|RND [/CPU]
            if len(seg) >= 3:
                game, method, window = seg[0], seg[1], seg[2]
                kind = seg[3] if len(seg) >= 4 else ""
                # „60% backtest" e greșit — e fereastra pe istoric, nu % din bench.
                parts = [game, method, f"fereastră {window}"]
                if kind in ("REAL", "RND"):
                    parts.append(kind)
                last_now = " · ".join(parts)
    except Exception:  # noqa: BLE001
        pass
    if tot <= 0:
        return 0.03, "pornește... (estimez după primele teste)"
    frac = max(0.0, min(1.0, cur / tot))

    elapsed = max(0.0, time.time() - start_ts) if start_ts else 0.0
    eta = (
        (tot - cur) * (elapsed / cur)
        if (elapsed > 0 and cur > 0 and tot > cur)
        else None
    )

    text = f"{int(frac * 100)}% ({cur}/{tot} teste)"
    if eta is not None:
        text += f"  ·  rămas ~{_fmt_dur(eta)}"
    elif cur >= tot:
        text += "  ·  ✅ gata"
    if last_now:
        text += f"  ·  acum: {last_now}"
    return frac, text


_HW_CACHE = {"html": "", "ts": 0.0, "running": False}


def _hw_telemetry_refresh() -> None:
    """Citește CPU/RAM ÎN FUNDAL (thread) și cache-uiește HTML-ul. Apelat de un
    thread separat — NU pe event-loop-ul UI (psutil e blocant → ar pica
    WebSocket-ul 'connection lost'). GPU eliminat complet."""
    cpu = ram = ""
    try:
        import psutil

        ncores = psutil.cpu_count(logical=True) or 1
        # interval=0.3 → citire instantanee REALĂ (blochează 0.3s, dar suntem în thread
        # de fundal, nu pe event-loop). interval=None dădea mereu 0% la prima citire.
        pct = psutil.cpu_percent(interval=0.3)
        active = round(pct / 100.0 * ncores)
        cpu = f"{pct:.0f}% (~{active}/{ncores} nuclee)"
        vm = psutil.virtual_memory()
        ram = f"{vm.used / (1024**3):.1f}/{vm.total / (1024**3):.0f} GB ({vm.percent:.0f}%)"
    except Exception:  # noqa: BLE001
        pass
    parts = []
    if cpu:
        parts.append(render_html_safe(t"<span style='color:#38bdf8'>CPU {cpu}</span>"))
    if ram:
        parts.append(render_html_safe(t"<span style='color:#60a5fa'>RAM {ram}</span>"))
    _HW_CACHE["html"] = (
        (
            render_html_safe(
                t"<div style='margin-top:6px;font-size:.82em;font-family:monospace;opacity:.9'>📊 "
            )
            + " &nbsp;·&nbsp; ".join(parts)
            + render_html_safe(t"</div>")
        )
        if parts
        else ""
    )


def _hw_telemetry_html() -> str:
    """Întoarce INSTANT HTML-ul cache-uit (ne-blocant). Pornește un thread de refresh
    la fundal dacă datele-s vechi (>2.5s) — astfel event-loop-ul UI nu se blochează."""
    import threading, time as _t

    if not _HW_CACHE["running"] and (_t.time() - _HW_CACHE["ts"]) > 2.5:
        _HW_CACHE["running"] = True
        _HW_CACHE["ts"] = _t.time()

        def _bg():
            try:
                _hw_telemetry_refresh()
            finally:
                _HW_CACHE["running"] = False

        threading.Thread(target=_bg, daemon=True).start()
    return _HW_CACHE["html"]


def cancel_all() -> None:
    try:
        cancel_pending_running_jobs("Oprit de utilizator")
    except Exception as exc:  # noqa: BLE001
        logger.warning("cancel jobs: %s", exc)
    # Kill bench (din .bench_pid) + fallback orice bench_all_methods.py din proiect
    from cleanup_old_processes import kill_pid_tree

    try:
        # PID VERIFICAT (aceeași identitate ca _bench_running: create_time +
        # cmdline), nu doar `pid_exists`: un PID stale reciclat de Windows către
        # un proces străin ar face ca tree-kill-ul să omoare tot arborele lui,
        # nu doar procesul greșit izolat cum era înainte de tree-kill.
        pid = _verified_bench_pid()
        if pid is not None:
            # Tree-kill, nu doar terminate() pe părinte: runner.py paralelizează
            # foldurile pe un ProcessPoolExecutor — uciderea DOAR a procesului
            # principal lasă lucrătorii orfani, arzând CPU după „Anulează TOT".
            kill_pid_tree(pid)
    except Exception as exc:  # noqa: BLE001
        logger.warning("kill bench pid: %s", exc)
    try:
        # Plasa de siguranță cerea AMBELE substring-uri în linia de comandă:
        # „bench_all_methods.py" ȘI PROJECT_ROOT. Nu se potrivea niciodată:
        # `_launch_bench` pornea scriptul cu cale RELATIVĂ (cwd=PROJECT_ROOT), iar
        # `sys.executable` e venv-ul din D:\_BUILD\_LOTO, deliberat în AFARA
        # repo-ului — deci PROJECT_ROOT nu apărea nicăieri în cmdline. Cu
        # `.bench_pid` lipsă sau reciclat, „Anulează TOT" raporta „Proces anulat."
        # în timp ce bench-ul mergea mai departe și rescria folds.csv.
        # Acum: potrivim pe numele scriptului și confirmăm prin CWD-ul procesului.
        root = Path(PROJECT_ROOT).resolve()
        for p in psutil.process_iter(["cmdline"]):
            cl = " ".join(p.info.get("cmdline") or [])
            if "bench_all_methods.py" not in cl:
                continue
            try:
                same_cwd = Path(p.cwd()).resolve() == root
            except Exception:  # noqa: BLE001 — AccessDenied / procesul a murit
                same_cwd = root.as_posix() in cl.replace("\\", "/")
            if same_cwd:
                kill_pid_tree(p.pid)
    except Exception as exc:  # noqa: BLE001
        logger.warning("kill bench fallback: %s", exc)
    try:
        BENCH_PID_FILE.unlink()
    except OSError:
        pass
    STATE["active_job_id"] = None
    # IMPORTANT: marcăm că bench-ul NU mai e "în rulare" ca _tick să NU interpreteze
    # disparitia procesului ca "bench terminat → Auto-Pilot". Altfel Anuleaza pornea
    # generarea automat.
    STATE["bench_was_running"] = False
    STATE["bench_cancelled"] = True
    # Oprește și WALK-FORWARD-ul (thread separat) și blochează _finalize_pipeline:
    # altfel „Anulează TOT" lăsa WF să ruleze până la buget (până la 90 min), iar
    # finally-ul lui declanșa mail/shutdown — PC-ul se putea ÎNCHIDE după un cancel.
    STATE["wf_user_cancel"] = True
    ui.notify("Proces anulat.", type="warning")
    _refresh_status()


# --------------------------------------------------------------------------- #
# Walk-forward backtest (în thread de fundal, ca să nu blocheze UI-ul)
# --------------------------------------------------------------------------- #


def _start_walk_forward() -> None:
    results = STATE.get("results")
    if not (isinstance(results, tuple) and len(results) == 2):
        _finalize_pipeline()  # fără rezultate → nu rulează WF; mail deja trimis mai sus, doar shutdown
        return
    results_bundle, _ = results
    # Walk-forward pentru singurul pool de producție.
    _pfx = ""  # bench unic → fără prefix de secțiune

    # Secvență de rulare: dacă între timp pornește ALT walk-forward (generare nouă
    # cât rula validarea veche), thread-ul vechi devine stale — se oprește și NU mai
    # scrie retro/status/finalize peste rularea nouă.
    STATE["wf_user_cancel"] = False
    my_seq = int(STATE.get("wf_seq") or 0) + 1
    STATE["wf_seq"] = my_seq

    def _worker_wf() -> None:
        _wf_t0 = float(STATE.get("wf_start") or time.time())

        def _budget_s_live() -> float:
            # Citit LIVE din SETTINGS (nu o dată la pornire) → schimbarea bugetului din
            # UI ÎN TIMPUL validării are efect imediat (mărești bugetul → rularea
            # curentă continuă; îl micșorezi → se oprește mai devreme).
            # Fallback-ul e DEFAULTS, nu o constantă separată: `or` se declanșează pe
            # orice valoare falsy (câmpul UI golit dă None), iar o valoare hardcodată
            # aici ar reduce tăcut bugetul — cu oprire parțială a validării, fără ca
            # UI-ul să arate altceva decât numărul pe care l-a introdus utilizatorul.
            try:
                _b = float(SETTINGS.get("wf_budget_min") or DEFAULTS["wf_budget_min"])
                return max(60.0, _b * 60.0)
            except (TypeError, ValueError):
                return float(WF_TOTAL_BUDGET_S)

        def _global_deadline() -> float:
            dl = _wf_t0 + _budget_s_live()
            STATE["wf_deadline"] = dl  # ETA-ul afișat urmărește și el schimbarea live
            return dl

        _global_deadline()  # inițializează STATE["wf_deadline"] pt panou

        def _wf_cancel_all():
            # Anulare explicită din UI sau rulare înlocuită (alt WF pornit) →
            # oprire imediată; altfel buget global depășit → oprire parțială.
            if STATE.get("wf_user_cancel") or STATE.get("wf_seq") != my_seq:
                return True
            return time.time() > _global_deadline()

        try:
            from loto_enterprise.core.walk_forward_adapter import (
                run_honest_walk_forward,
            )

            with STATE_LOCK:
                ds_by_name = {fn: df for fn, df in STATE["datasets"]}
            if not ds_by_name:
                # Tipic la RECUPERARE după restart: CSV-urile nu-s reîncărcate (se încarcă
                # manual). Walk-forward se va sări (df_source None) → fără stats de validare,
                # dar mail-ul (= doar numerele) și shutdown-ul rulează normal.
                logger.warning(
                    "[WF] datasets goale (probabil recuperare după restart) → "
                    "walk-forward sărit; mail/shutdown continuă fără stats de validare."
                )
            total = _count_wf_jobs(results_bundle)
            done = 0
            for fname, g_label, data in _iter_wf_jobs(results_bundle):
                df_source = ds_by_name.get(fname)
                if df_source is None:
                    continue
                done += 1
                base = (done - 1) / max(1, total)
                STATE["wf_status"] = f"📊 Walk-forward {done}/{total}: {g_label}..."
                STATE["wf_progress"] = base

                def _wf_cb(
                    frac,
                    n_done=0,
                    n_total=0,
                    _b=base,
                    _t=total,
                    _d=done,
                    _tot=total,
                    _g=g_label,
                ):
                    frac = max(0.0, min(1.0, float(frac)))
                    STATE["wf_progress"] = min(1.0, _b + frac / _t)
                    if n_total > 0:
                        STATE["wf_status"] = (
                            f"📊 Walk-forward {_d}/{_tot}: {_g} — "
                            f"pas {n_done}/{n_total} ({int(frac * 100)}%)"
                        )
                    else:
                        STATE["wf_status"] = (
                            f"📊 Walk-forward {_d}/{_tot}: {_g} — {int(frac * 100)}%"
                        )

                # Buget PER JOC (felie adaptivă din timpul global rămas): un joc
                # lent nu mai înfometează jocurile următoare (bug văzut: joker a
                # consumat tot bugetul → 5/40 parțial, 6/49 aproape zero). Minim
                # 60s/joc. Felia se recalculează LIVE din deadline-ul global →
                # mărirea bugetului din UI extinde și felia jocului CURENT.
                _games_left = max(1, total - done + 1)
                _game_t0 = time.time()

                def _wf_should_cancel(_gt0=_game_t0, _gl=_games_left):
                    _gd = _gt0 + max(60.0, (_global_deadline() - _gt0) / _gl)
                    return _wf_cancel_all() or time.time() > _gd

                try:
                    _wf_pool = int(
                        data.get("pool_size_requested") or data.get("pool_size") or 10
                    )
                    flat, meta = run_honest_walk_forward(
                        df_source=df_source,
                        game_type=g_label,
                        pool_size=_wf_pool,
                        backtest_depth_percent=WF_DEPTH_PERCENT,
                        lookback_percent=_effective_lookback_pct(data),
                        use_cache=True,
                        progress_cb=_wf_cb,
                        should_cancel=_wf_should_cancel,
                        # Semnal SEPARAT de should_cancel: doar înlocuire (alt WF pornit
                        # pe aceeași cheie), NU buget/anulare — acelea vor cache-ul scris
                        # (asta e scopul acoperirii parțiale). Fără el, o rulare veche
                        # care termină DUPĂ una nouă i-ar suprascrie cache-ul mai complet.
                        should_skip_cache_write=lambda: STATE.get("wf_seq") != my_seq,
                        **_wf_generation_options(data),
                    )
                    if STATE.get("wf_seq") != my_seq:
                        # A pornit alt walk-forward: nu-i suprascriem retro/status.
                        logger.info(
                            "[WF] rulare înlocuită de una nouă — mă opresc fără scriere."
                        )
                        break
                    if meta.get("partial"):
                        logger.warning(
                            "[WF] %s validat PARȚIAL: %s/%s extrageri "
                            "(buget de timp / anulare) — acoperă extragerile RECENTE.",
                            g_label,
                            meta.get("n_test_draws"),
                            meta.get("n_expected"),
                        )
                    _rk = f"{_pfx}{fname}_{g_label}"
                    with STATE_LOCK:
                        STATE["retro"][_rk] = flat
                        STATE.setdefault("retro_meta", {})[_rk] = {
                            "partial": bool(meta.get("partial")),
                            "n_test_draws": meta.get("n_test_draws"),
                            "n_expected": meta.get("n_expected"),
                            "from_cache": bool(meta.get("from_cache")),
                            # pool-ul REAL cu care a rulat WF (pt afișare corectă în istoric)
                            "pool_size": meta.get("pool_size"),
                            "wheel_guarantee": meta.get("wheel_guarantee"),
                            "wheel_condition": meta.get("wheel_condition"),
                            "max_variants": meta.get("max_variants"),
                        }
                except Exception as exc:  # noqa: BLE001
                    logger.error("walk-forward %s: %s", g_label, exc)
                STATE["wf_progress"] = done / max(1, total)
                if _wf_cancel_all():
                    logger.warning(
                        "[WF] oprire walk-forward (anulare/buget) după %d/%d jocuri.",
                        done,
                        total,
                    )
                    break
            if STATE.get("wf_seq") == my_seq and not STATE.get("wf_user_cancel"):
                STATE["wf_status"] = ""
                STATE["wf_progress"] = 1.0
        except Exception as exc:  # noqa: BLE001
            STATE["wf_status"] = f"Walk-forward eșuat: {exc}"
        finally:
            _stale = STATE.get("wf_seq") != my_seq
            _user_cancelled = bool(STATE.get("wf_user_cancel"))
            if not _stale:
                if STATE.get("job_start_time") and STATE.get("wf_elapsed") is None:
                    STATE["wf_elapsed"] = time.time() - STATE["job_start_time"]
                _save_report_file()  # rescriu raportul acum CU statisticile walk-forward
                # Refresh-ul UI se face DOAR pe event-loop (în _tick), nu din thread:
                # elementele NiceGUI nu sunt thread-safe.
                STATE["results_dirty"] = True
                if _user_cancelled:
                    # Anulare explicită: FĂRĂ finalizare (mail/shutdown) — utilizatorul
                    # tocmai a oprit tot; un shutdown aici ar închide PC-ul după cancel.
                    STATE["wf_status"] = "Walk-forward anulat (fără mail/oprire PC)."
                    logger.info("[WF] finalizare sărită: anulare explicită din UI.")
                else:
                    # Mail-ul a plecat deja imediat după generare (vezi status_panel).
                    # ABIA ACUM (walk-forward terminat): oprirea PC-ului (dacă e cerută).
                    _finalize_pipeline()
                STATE["results_dirty"] = (
                    True  # banner-ul de oprire apare la următorul tick
                )
                # Textul din wf_status („anulat"/„eșuat") rămâne vizibil, dar WF NU
                # mai rulează: flag-ul separat oprește refresh-ul de 1s și bara de progres.
                STATE["wf_running"] = False

    STATE["wf_progress"] = 0.0
    STATE["wf_start"] = time.time()  # pt ETA walk-forward
    STATE["wf_status"] = "📊 Pornesc walk-forward backtest (paralel ~80% CPU)..."
    STATE["wf_running"] = True
    threading.Thread(target=_worker_wf, daemon=True).start()


# --------------------------------------------------------------------------- #
# UI — randare
# --------------------------------------------------------------------------- #
# Mesajul de FAILED vine din `result_json`; îl trunchiem ca să nu inunde panoul.
_FAIL_MSG_MAX_CHARS = 800
_UNSTARTED_WORKER_WAIT_S = 45.0


def _abandon_unstarted_ui_job(reason: str) -> None:
    """Scoate de pe ecran un job pe care worker-ul nu l-a preluat (0%, fără log)."""
    try:
        jid = STATE.get("active_job_id")
        if jid is not None:
            cancel_pending_running_jobs(reason, job_ids=[int(jid)])
    except Exception as exc:  # noqa: BLE001
        logger.warning("abandon unstarted: %s", exc)
    STATE["active_job_id"] = None
    STATE["job_start_time"] = None


@ui.refreshable
def status_panel() -> None:
    job_id = STATE.get("active_job_id")
    bench_on = _bench_running()

    if job_id:
        stt = get_job_status(int(job_id))
        if not stt:
            STATE["active_job_id"] = None
            ui.label("Job invalid / dispărut.").classes("text-negative")
            return
        pct = int(stt.get("progress_pct") or 0)
        state = str(stt.get("status") or "")
        if state == "COMPLETED":
            payload = decode_queue_result(str(stt.get("result_json") or "{}"))
            if not (isinstance(payload, tuple) and len(payload) == 2):
                # Payload gol/corupt (cursă cu cancel, pickle rupt, worker ucis între
                # `complete_job` și scriere). ACEEAȘI gardă ca `_recover_completed_job`
                # — până acum ramura LIVE n-o avea și mergea mai departe cu `None`:
                # `STATE["results"] = None`, dar mail trimis, walk-forward pornit și
                # `last_finalized_job_id` setat, deci jobul nu se mai putea relua.
                # Marcăm văzut (altfel ecranul reintră aici la fiecare tick), dar
                # FĂRĂ mail / WF / shutdown, și spunem de ce.
                with STATE_LOCK:
                    STATE["active_job_id"] = None
                    SETTINGS["last_finalized_job_id"] = int(job_id)
                _save_settings()
                logger.error(
                    "[JOB] #%s COMPLETED cu payload invalid (%r) — "
                    "fără mail/walk-forward/shutdown.",
                    job_id,
                    type(payload).__name__,
                )
                ui.label(
                    f"⚠️ Job #{job_id} s-a terminat, dar rezultatul e ilizibil "
                    "(payload gol sau corupt). Nu s-a trimis mail și nu s-a rulat "
                    "walk-forward. Vezi loto.log și regenerează."
                ).classes("text-negative")
                return
            # Claim ATOMIC: un SINGUR renderer duce jobul în finalize. Dacă două
            # taburi/reconnect-uri intră aproape simultan în ramura COMPLETED, doar cel
            # care încă vede active_job_id == job_id procesează (mail/shutdown o dată);
            # ceilalți doar afișează. Și marcăm jobul ca finalizat ACUM (înainte de
            # WF/mail/shutdown) → o repornire în timpul walk-forward-ului NU-l reia.
            with STATE_LOCK:
                claimed = STATE.get("active_job_id") == int(job_id)
                if claimed:
                    # Durata generării O SINGURĂ DATĂ (fixă) — altfel 'Rezultate (în X)'
                    # creștea live cât rula walk-forward-ul.
                    if STATE.get("job_start_time") and STATE.get("job_elapsed") is None:
                        STATE["job_elapsed"] = time.time() - STATE["job_start_time"]
                    STATE["results"] = payload
                    STATE["results_recovered"] = (
                        None  # rezultat PROASPĂT → fără marcaj „vechi"
                    )
                    STATE["active_job_id"] = None
                    SETTINGS["last_finalized_job_id"] = int(job_id)
            if not claimed:
                ui.label("✅ Ultima generare e gata (vezi mai jos).").classes(
                    "text-positive"
                )
                return
            _save_settings()
            _save_report_file()  # raport imediat (fără WF); rescris după walk-forward

            # Mail-ul conține doar pool-ul generat (fără stats WF, vezi _build_mail_body) →
            # numerele sunt deja fixate acum; nu are rost să aștepte walk-forward-ul de
            # raportare (poate dura minute/ore). Trimis o singură dată (claimed == True mai sus).
            # SMTP (connect + STARTTLS + login, timeout 30s fiecare) NU pe event-loop:
            # un server lent bloca UI-ul >60s → «connection lost».
            def _mail_bg():
                try:
                    _maybe_send_results_email()
                except Exception as exc:  # noqa: BLE001
                    logger.error("[MAIL] trimitere imediată eșuată: %s", exc)

            threading.Thread(target=_mail_bg, name="mail-results", daemon=True).start()
            _start_walk_forward()  # async; oprirea PC se face la FINALUL walk-forward-ului
            results_panel.refresh()
            try:
                ui.run_javascript(SOUND_JS)  # beep de finalizare
            except Exception:  # noqa: BLE001
                pass
            # _maybe_shutdown() NU aici — walk-forward-ul încă rulează în fundal.
            # Oprirea se declanșează în _worker_wf (la final) sau pe ramura fără rezultate.
            ui.label("✅ Generare finalizată — rulează walk-forward...").classes(
                "text-positive text-lg"
            )
            _shutdown_banner()
            return
        if state in ("FAILED", "CANCELLED"):
            STATE["active_job_id"] = None
            # Mesajul de eroare NU e o coloană proprie: `job_queue.fail_job` îl scrie
            # în `result_json` (întreg) și în `log_tail` (ultimii 6000 de octeți).
            # `stt.get("error_msg")` era o cheie INEXISTENTĂ în schema `jobs` → panoul
            # arăta mereu „Job FAILED:" și nimic, exact când utilizatorul are cea mai
            # mare nevoie de motiv (CSV corupt, worker mort la import, disc plin).
            _err = str(stt.get("result_json") or stt.get("log_tail") or "").strip()
            if len(_err) > _FAIL_MSG_MAX_CHARS:
                _err = _err[-_FAIL_MSG_MAX_CHARS:]
            ui.label(
                f"Job {state}: {_err or 'fără mesaj în coadă (vezi loto.log)'}"
            ).classes("text-negative")
            return
        # 0% + fără log = worker-ul NU a preluat jobul. Nu ținem ecranul blocat
        # pe «se inițializează...» la infinit. Leftover la boot (fără job_start_time)
        # → scoatem imediat. Click Generează în sesiunea asta → așteptăm worker-ul
        # câteva zeci de secunde, apoi abandonăm.
        if is_unstarted_job(stt):
            t0 = STATE.get("job_start_time")
            if t0 is None:
                _abandon_unstarted_ui_job(
                    "Job nepornit la afișare (0%, fără log) — scos de pe ecran."
                )
                ui.label(
                    "Gata de lucru. Încarcă CSV-uri și apasă Generează / Auto-Pilot."
                ).classes("text-caption")
                return
            waited = time.time() - float(t0)
            ensure_worker_running()
            if waited > _UNSTARTED_WORKER_WAIT_S:
                # Worker-ul e SECVENȚIAL și observă anularea doar în progress_cb:
                # după «Anulează TOT» în timpul wheeling-ului, procesul mai calculează
                # minute întregi jobul vechi, iar jobul nou stă PENDING legitim.
                # Abandonăm doar când procesul chiar nu există.
                if is_worker_running():
                    current = "worker-ul termină jobul anterior; aștept..."
                else:
                    _abandon_unstarted_ui_job("Worker-ul nu a preluat jobul în 45s.")
                    ui.label("Worker-ul nu a pornit. Reîncearcă Generează.").classes(
                        "text-negative"
                    )
                    return
            else:
                current = "aștept worker-ul..."
            elapsed_txt = f" · scurs {_fmt_dur(waited)}"
            ui.label(f"⏳ Job în rulare (#{job_id}) — {pct}%{elapsed_txt}").classes(
                "text-bold"
            )
            ui.linear_progress(value=0, show_value=False).props("instant-feedback")
            ui.label(f"➡️ {current}").classes("text-caption text-info")
            return
        with ui.card().classes("w-full"):
            tail = str(stt.get("log_tail") or "").strip()
            lines = tail.splitlines() if tail else []
            current = lines[-1] if lines else "se inițializează..."
            elapsed_txt = ""
            if STATE.get("job_start_time"):
                elapsed_txt = (
                    f" · scurs {_fmt_dur(time.time() - STATE['job_start_time'])}"
                )
            # Worker mort (kill -9 / crash) lăsa jobul RUNNING la infinit:
            # reatașarea de la startup cheamă ensure o dată; _tick nu o refăcea.
            ensure_worker_running()
            ui.label(f"⏳ Job în rulare (#{job_id}) — {pct}%{elapsed_txt}").classes(
                "text-bold"
            )
            ui.linear_progress(value=pct / 100.0, show_value=False).props(
                "instant-feedback"
            )
            ui.label(f"➡️ {current}").classes("text-caption text-info")
            if len(lines) > 1:
                with ui.expansion(
                    f"Pași detaliați ({len(lines)})", value=False
                ).classes("w-full"):
                    ui.code("\n".join(lines[-15:]), language="text").classes(
                        "w-full max-h-48 overflow-auto text-xs"
                    )
        return

    if bench_on:
        _start = None
        try:
            _p = BENCH_PID_FILE.read_text(encoding="utf-8").strip().split("|")
            _start = float(_p[1]) if len(_p) > 1 else None
        except Exception:  # noqa: BLE001
            pass
        rc = _bench_progress_from(BENCH_LOG_FILE, _start)
        with ui.card().classes("w-full"):
            if rc:
                ui.html(
                    render_html_safe(
                        t"🔬 <b style='color:#38bdf8'>RE-BENCH</b> — {rc[1]}"
                    )
                )
                ui.linear_progress(value=rc[0], show_value=False).props(
                    "instant-feedback"
                ).classes("w-full")
            ui.label(
                "Testez toate metodele (CPU, pe toate nucleele). Auto-Pilot pornește la final."
            ).classes("text-caption")
            ui.html(_hw_telemetry_html())  # consum live CPU/RAM
        # Clasament PARȚIAL live: metodele apar pe măsură ce termină.
        _render_bench_live_leaderboard(_start, progress=(rc[0] if rc else None))
        return

    _shutdown_banner()
    if isinstance(STATE.get("results"), tuple):
        rec = STATE.get("results_recovered")
        if rec:
            # Rezultate recuperate dintr-o sesiune anterioară (job vechi, neprelucrat la
            # momentul lui) → avertizăm CLAR: nu sunt din rularea curentă.
            ui.label(
                f"⚠️ Rezultate RECUPERATE dintr-o sesiune anterioară ({rec}) — "
                "verifică data extragerii înainte să joci; re-rulează pentru numere noi."
            ).classes("text-warning text-bold")
        else:
            ui.label("✅ Ultima generare e gata (vezi mai jos).").classes(
                "text-positive"
            )
    else:
        ui.label(
            "Gata de lucru. Încarcă CSV-uri și apasă Generează / Auto-Pilot."
        ).classes("text-caption")


SOUND_JS = (
    "try{const c=new (window.AudioContext||window.webkitAudioContext)();"
    "const o=c.createOscillator();const g=c.createGain();o.connect(g);g.connect(c.destination);"
    "o.type='sine';o.frequency.value=880;g.gain.value=0.08;o.start();"
    "o.stop(c.currentTime+0.35);}catch(e){}"
)


def _next_draw_date() -> str:
    """Următoarea extragere (Loteria Română: 6/49, 5/40, Joker — JOI și DUMINICĂ)."""
    base = _dt.now().date()
    o = base.toordinal()
    for i in range(0, 8):
        d = base.fromordinal(o + i)
        if d.weekday() in (3, 6):  # 3 = Joi, 6 = Duminică
            return d.strftime("%d-%m-%Y")
    return base.strftime("%d-%m-%Y")


def _build_mail_body() -> str:
    """Conținut CONCIS: data extragerii + pool-ul unic per joc."""
    results = STATE.get("results")
    if not (isinstance(results, tuple) and len(results) == 2):
        return "Nu există rezultate de generare."
    rb, _ = results

    def _nums(seq):
        return " ".join(str(int(x)) for x in sorted(seq)) if seq else "—"

    lines = [
        f"📅 Extragere (următoarea, Joi/Duminică): {_next_draw_date()}",
        f"(generat: {_dt.now().strftime('%d-%m-%Y %H:%M')})",
        "",
    ]
    # Ordine FIXĂ în mail: 6/49 → Joker → 5/40 (aplatizăm jocurile din toate fișierele).
    # Păstrăm fname ca să putem arăta ultima extragere reală din CSV pentru fiecare joc.
    games = sorted(
        ((fn, g, d) for fn, outs in rb for g, d in outs.items()),
        key=lambda t: _GAME_DISPLAY_ORDER.get(_game_label_for(str(t[1])), 99),
    )
    for fn, g, d in games:
        primary = _primary_pool_data(d)
        joker = sorted(int(x) for x in (primary.get("hard_core_joker") or []))
        lines.append(f"=== {g.upper()} ===")
        info = _last_csv_draw(fn)
        if info:
            _ds, _dn, _dj = info
            _draw = " ".join(str(x) for x in _dn) + (
                f" + joker {_dj}" if _dj is not None else ""
            )
            lines.append(f"ultima extragere CSV: {_ds or '?'} → {_draw}")
        lines.append(
            "POOL:   "
            + _nums(primary.get("hard_core") or [])
            + (f"  | joker: {_nums(joker)}" if joker else "")
        )
        lines.append("")
    return "\n".join(lines).strip()


def _send_test_email() -> None:
    """Buton (declanșat de utilizator): trimite un mail de test ca să confirmi configul."""
    cfg = load_mail_config(PROJECT_ROOT)
    if not cfg:
        ui.notify(
            "📧 Lipsesc credențialele în mail_config.json (smtp_user/smtp_pass).",
            type="warning",
        )
        return
    body = (
        "Test e-mail Loto Enterprise — configurarea funcționează ✅\n"
        f"Următoarea extragere: {_next_draw_date()}\n"
        "La finalul bench-ului vei primi: data + pool-ul fiecărui joc."
    )
    try:
        send_email(cfg, "🎰 Loto — mail de test", body)
        ui.notify(
            f"📧 Mail de test trimis la {cfg['mail_to']}. Verifică inbox-ul.",
            type="positive",
        )
        logger.info("[MAIL] test trimis la %s", cfg["mail_to"])
    except Exception as exc:  # noqa: BLE001
        ui.notify(f"📧 Test eșuat: {exc}", type="negative")
        logger.error("[MAIL] test eșuat: %s", exc)


def _maybe_send_results_email() -> None:
    """Trimite rezultatele pe mail la finalul pipeline-ului, dacă e bifat
    'mail_on_complete' ȘI SMTP-ul e configurat (mail_config.json / env). Best-effort:
    orice eroare e logată, NU oprește restul (shutdown etc.)."""
    if not SETTINGS.get("mail_on_complete"):
        return
    cfg = load_mail_config(PROJECT_ROOT)
    if not cfg:
        logger.warning(
            "[MAIL] cerut, dar SMTP neconfigurat (mail_config.json / env) — sar peste."
        )
        try:
            ui.notify(
                "📧 Mail cerut, dar lipsesc credențialele (vezi mail_config.json).",
                type="warning",
            )
        except Exception:  # noqa: BLE001
            pass
        return
    try:
        body = _build_mail_body()
    except Exception as exc:  # noqa: BLE001
        logger.error("[MAIL] build body: %s", exc)
        body = "(eroare la construirea conținutului)"
    subject = f"🎰 Loto — numere pentru extragerea {_next_draw_date()}"
    try:
        send_email(cfg, subject, body)  # doar esențialul (data + pool), fără atașament
        logger.info("[MAIL] rezultate trimise la %s", cfg["mail_to"])
        try:
            ui.notify(
                f"📧 Rezultate trimise pe mail ({cfg['mail_to']}).", type="positive"
            )
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        logger.error("[MAIL] trimitere eșuată: %s", exc)
        try:
            ui.notify(f"📧 Mail eșuat: {exc}", type="negative")
        except Exception:  # noqa: BLE001
            pass


def _finalize_pipeline() -> None:
    """La finalul walk-forward-ului (sau ramura fără rezultate): oprește PC-ul (dacă
    e cerut). Mailul NU se trimite aici — pleacă imediat după generare (vezi
    status_panel, ramura COMPLETED), fiindcă nu are conținut dependent de WF
    (`_build_mail_body` = doar numerele generate) și n-are rost să aștepte minute/ore
    de validare retroactivă doar ca notificare să ajungă mai târziu."""
    logger.info(
        "[FINALIZE] post-walk-forward: shutdown_on_complete=%s",
        SETTINGS.get("shutdown_on_complete"),
    )
    try:
        _maybe_shutdown()
    except Exception as exc:  # noqa: BLE001
        logger.error("[SHUTDOWN] finalize a eșuat: %s", exc)


def _maybe_shutdown() -> None:
    """Auto-shutdown PC la final dacă e cerut (bifă sau .shutdown_pending.flag)."""
    flag = PROJECT_ROOT / ".shutdown_pending.flag"
    want = bool(SETTINGS.get("shutdown_on_complete")) or flag.exists()
    if not want or STATE.get("_shutdown_initiated"):
        return
    STATE["_shutdown_initiated"] = True
    STATE["_shutdown_at"] = time.time()
    if os.name == "nt":
        try:
            subprocess.Popen(
                [
                    "shutdown",
                    "/s",
                    "/t",
                    "60",
                    "/f",
                    "/c",
                    "Loto Enterprise: shutdown automat după job complete",
                ]
            )
            logger.warning("[SHUTDOWN] shutdown /s /t 60 lansat (anulabil).")
        except Exception as exc:  # noqa: BLE001
            logger.error("[SHUTDOWN] eșuat: %s", exc)
            STATE["_shutdown_initiated"] = False
    else:
        logger.warning(
            "[SHUTDOWN] cerut, dar OS non-Windows — sar peste comanda reală."
        )
    try:
        flag.unlink(missing_ok=True)
    except OSError:
        pass


def _cancel_shutdown() -> None:
    if os.name == "nt":
        try:
            subprocess.Popen(["shutdown", "/a"])
        except Exception as exc:  # noqa: BLE001
            logger.error("[SHUTDOWN] anulare eșuată: %s", exc)
    STATE["_shutdown_initiated"] = False
    ui.notify("Oprire anulată.", type="positive")
    status_panel.refresh()


def _shutdown_banner() -> None:
    if not STATE.get("_shutdown_initiated"):
        return
    with ui.card().classes("w-full bg-red-900"):
        ui.label("🔌 Oprire PC programată (60s). Poți anula:").classes("text-bold")
        ui.button("❌ ANULEAZĂ OPRIREA", on_click=_cancel_shutdown).props(
            "color=negative"
        )


def _read_bench_log_tail(n: int = 50) -> str:
    """Ultimele n linii din bench_full.log (procesul de bench, separat de worker)."""
    if not BENCH_LOG_FILE.exists():
        return ""
    try:
        # Coada, nu tot fișierul: `logs_panel` se re-randează la fiecare tick cât
        # timp rulează un bench, iar bench_full.log crește pe toată durata lui.
        # `read_tail_lines` întoarce exact aceleași linii (verificat pe fișier
        # gol / fără newline final / 18 MB).
        lines = read_tail_lines(str(BENCH_LOG_FILE), n)
        return "".join(lines).strip()
    except Exception as exc:  # noqa: BLE001
        return f"(eroare citire bench_full.log: {exc})"


def _clear_all_logs() -> None:
    """Curăță atât loto.log (engine/worker) cât și bench_full.log (benchmark)."""
    clear_logs()  # rescrie loto.log cu un header
    try:
        if BENCH_LOG_FILE.exists():
            BENCH_LOG_FILE.write_text("", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("clear bench log: %s", exc)
    logs_panel.refresh()
    ui.notify("Loguri curățate (loto.log + bench_full.log).", type="positive")


@ui.refreshable
def logs_panel() -> None:
    # Toolbar: curăță AMBELE loguri (loto.log + bench_full.log) + refresh manual.
    with ui.row().classes("w-full items-center gap-2 mb-1"):
        ui.button("🗑️ Curăță logurile", on_click=_clear_all_logs).props(
            "outline dense no-caps color=negative"
        ).classes("text-xs")
        ui.button("🔄 Reîmprospătează", on_click=logs_panel.refresh).props(
            "outline dense no-caps"
        ).classes("text-xs")
    # ── Engine / Worker (loto.log) ── include faza POST-BENCH: selectia metodei
    # castigatoare din best_methods.json, scoringul, POST-HOC si walk-forward.
    ui.label(
        "⚙️ Engine / Worker — loto.log (include ce se întâmplă DUPĂ bench)"
    ).classes("text-xs text-bold text-cyan-400")
    # citim din cache (populat de thread-ul _tick) ca să nu blocăm event-loop-ul UI
    _logtxt = STATE.get("_log_cache")
    if _logtxt is None:
        try:
            _logtxt = read_logs_filtered(120)
        except Exception:  # noqa: BLE001
            _logtxt = "(loguri indisponibile)"
    ui.code(_logtxt, language="text").classes("w-full max-h-72 overflow-auto text-xs")

    # ── Bench (bench_full.log) ── proces separat; afisat doar daca exista log.
    bench_tail = _read_bench_log_tail(50)
    if bench_tail:
        ui.label(
            "📊 Bench — bench_full.log (benchmark metode + best_methods.json)"
        ).classes("text-xs text-bold text-amber-400 mt-2")
        ui.code(bench_tail, language="text").classes(
            "w-full max-h-56 overflow-auto text-xs"
        )

def _render_results_bundle(results_bundle, res_prefix: str = "") -> None:
    # 1) Meniu global analiză — sus, închis implicit.
    _render_analysis_menu(results_bundle, res_prefix)

    # 2) Pool-urile per joc — DOAR pool + bilete de jucat (fără clasament/walk-forward).
    #    Sortăm fișierele după joc (6/49, Joker, 5/40), nu după ordinea de încărcare.
    ordered_bundle = sorted(
        results_bundle,
        key=lambda fo: min(
            (_GAME_DISPLAY_ORDER.get(_game_label_for(str(g)), 99) for g in fo[1]),
            default=99,
        ),
    )
    for fname, outs in ordered_bundle:
        with ui.card().classes("w-full"):
            ui.label(f"📄 {fname}").classes("text-subtitle1 text-bold")
            for game, raw_data in _ordered_game_items(outs):
                data = _primary_pool_data(raw_data)
                with ui.expansion(f"🎯 {game.upper()}", value=True).classes("w-full"):
                    _render_pool_body(fname, game, data)


def _new_draws_summary():
    """Câte extrageri noi s-au adăugat de la ultimul bench (per joc + total), din
    semnăturile CSV stampilate în best_methods.json de modulul `freshness`
    (`write_signatures_to_best_methods`, apelat la finalul fiecărui bench).

    Întoarce None dacă freshness e indisponibil. `any_bench` = există măcar o
    semnătură de la un bench anterior (altfel primul bench e oricum complet)."""
    try:
        from loto_enterprise.benchmark.freshness import (
            check_freshness,
            aggregate_recommendation,
        )

        reports = check_freshness()
    except Exception:  # noqa: BLE001
        return None
    per, total, any_bench = {}, 0, False
    for gk, r in reports.items():
        if gk == "joker_urna2":  # alias pe aceeași sursă ca joker_urna1 — nu dubla
            continue
        if getattr(r, "status", "") == "missing":
            continue
        cached = int(getattr(r, "cached_rows", 0) or 0)
        cur = int(getattr(r, "current_rows", 0) or 0)
        if cached > 0:
            any_bench = True
        d = cur - cached
        if d > 0:
            per[gk] = d
            total += d
    try:
        rec = aggregate_recommendation(reports)
    except Exception:  # noqa: BLE001
        rec = ""
    return {"total": total, "per": per, "rec": rec, "any_bench": any_bench}


# Panoul de istoric adaptiv folosea două nume NEDEFINITE (NameError la orice
# apel). Fișierul canonic e cel scris de core.adaptive_feedback (rădăcina repo);
# pool-urile acceptate de UI sunt 6..16 (clamp-ul din worker/încărcare).
ADAPTIVE_STATE_FILE = PROJECT_ROOT / "adaptive_state.json"
SUPPORTED_POOLS = range(6, 17)


def _clean_stale_adaptive(stale_keys) -> None:
    # NU e @ui.refreshable: e o ACȚIUNE de mutare (buton), nu funcție de randare.
    try:
        from ui_shared import file_lock

        with file_lock(ADAPTIVE_STATE_FILE):  # nu ne batem cu worker-ul pe RMW
            raw = json.loads(ADAPTIVE_STATE_FILE.read_text(encoding="utf-8"))
            for k in stale_keys:
                raw.pop(k, None)
            atomic_write_json(ADAPTIVE_STATE_FILE, raw)
        ui.notify(f"Șters {len(stale_keys)} configurări stale.", type="positive")
    except Exception as exc:  # noqa: BLE001
        ui.notify(f"Eroare la curățare: {exc}", type="negative")
    adaptive_history_panel.refresh()


@ui.refreshable
def adaptive_history_panel() -> None:
    if not ADAPTIVE_STATE_FILE.exists():
        ui.label(
            "Fără istoric adaptiv încă (se creează după prima generare cu feedback)."
        ).classes("text-caption")
        return
    try:
        raw = json.loads(ADAPTIVE_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        raw = {}
    if not raw:
        ui.label("Fără istoric adaptiv încă.").classes("text-caption")
        return

    stale = []
    for k in raw:
        try:
            if int(str(k).split("_")[-1]) not in SUPPORTED_POOLS:
                stale.append(k)
        except (ValueError, IndexError):
            pass

    ui.label(
        "Stare persistentă Adaptive Feedback v2 — telemetrie evenimente "
        "(catastrofă/underperf/normal), regime resets, hard inversions."
    ).classes("text-caption")
    if stale:
        with ui.row().classes("items-center gap-3"):
            ui.label(
                f"⚠️ {len(stale)} configurări STALE (pool inaccesibil 6-16): {', '.join(stale)}"
            ).classes("text-warning text-caption")
            ui.button(
                "🗑️ Curăță stale", on_click=lambda s=stale: _clean_stale_adaptive(s)
            ).props("flat dense color=negative")

    icons = {
        "catastrophe": "🔥",
        "underperf": "⚠️",
        "normal": "✅",
        "regime_reset": "🚨",
    }
    for key in sorted(raw):
        entry = raw[key] or {}
        hist = entry.get("history", []) or []
        rs = entry.get("regime_state", {}) or {}
        mode = rs.get("active_mode", "normal")
        streak = int(rs.get("streak_zero", 0) or 0)
        events = [str(h.get("event", "?")) for h in hist]
        hits = [int(h.get("pool_hits", 0) or 0) for h in hist]
        n = len(events)
        n_cat = events.count("catastrophe")
        mean_h = (sum(hits) / n) if n else 0.0
        max_h = max(hits) if hits else 0
        badge = "RESET" if mode == "reset" else "NORMAL"
        title = f"{key}  [{badge}]" + ("  [STALE]" if key in stale else "")
        with ui.expansion(title, value=False).classes("w-full"):
            with ui.row().classes("gap-6"):
                cat_txt = f"{n_cat} ({n_cat / n * 100:.0f}%)" if n else "0"
                for lbl, val in [
                    ("Total extrageri", n),
                    ("Mean hits", f"{mean_h:.2f}"),
                    ("Best", max_h),
                    ("Catastrofe", cat_txt),
                    ("Streak zero", streak),
                ]:
                    with ui.column().classes("items-center gap-0"):
                        ui.label(lbl).classes("text-caption")
                        ui.label(str(val)).classes("text-subtitle1")
            if entry.get("last_pool_date"):
                ui.label(f"Ultima predicție: {entry['last_pool_date']}").classes(
                    "text-caption"
                )
            if hits:
                ui.echart(
                    {
                        "tooltip": {"trigger": "axis"},
                        "xAxis": {
                            "type": "category",
                            "data": list(range(1, len(hits) + 1)),
                        },
                        "yAxis": {"type": "value"},
                        "series": [
                            {
                                "type": "line",
                                "data": hits,
                                "smooth": True,
                                "areaStyle": {},
                            }
                        ],
                        "grid": {"left": 30, "right": 10, "top": 10, "bottom": 20},
                    }
                ).classes("w-full").style("height:140px")
                recent = hist[-min(15, len(hist)) :]
                seq = " ".join(
                    f"{icons.get(str(h.get('event', '?')), '•')}{int(h.get('pool_hits', 0) or 0)}"
                    for h in recent
                )
                ui.label(f"Ultimele {len(recent)}: {seq}").classes("text-caption")

    total_learned = sum(len(e.get("history", []) or []) for e in raw.values())
    n_reset = sum(
        1
        for e in raw.values()
        if (e.get("regime_state") or {}).get("active_mode") == "reset"
    )
    ui.label(
        f"📈 Global: {total_learned} extrageri învățate · {n_reset} configurări în mod RESET."
    ).classes("text-caption text-bold")


def _refresh_status() -> None:
    status_panel.refresh()
    logs_panel.refresh()


# --------------------------------------------------------------------------- #
# Submeniu: procentul exact al fiecărui prag de restrângere, per joc
# --------------------------------------------------------------------------- #
_BASE_TABLE_GAMES = (
    ("6/49", "loto_6_49.csv", 6, 49),
    ("5/40", "loto_5_40.csv", 6, 40),  # 6 numere extrase
    ("Joker — Urna 1 (5/45)", "joker.csv", 5, 45),
)

_BASE_TABLE_MEMO: dict = {}  # (fișier, mtime, size, geometrie, pool) → (rânduri, n_extrageri)
# 3 jocuri × pool 6..16 = 33 combinații. Sub atât, memo-ul se golea înainte să
# apuce să fie folosit: o plimbare înainte și înapoi peste dimensiunile de pool
# recalcula tot de fiecare dată.
_BASE_TABLE_MEMO_MAX = len(_BASE_TABLE_GAMES) * 11
# Tabelul se calculează abia când submeniul e deschis: ~0.7 s pentru trei jocuri
# ar fi fost plătiți la fiecare încărcare de pagină, chiar fără să fie deschis.
_BASE_TABLE_OPENED = {"value": False}


def _base_interval_rows(csv_name: str, draw_n: int, max_num: int, pool_size: int):
    """Tabelul de intervale al unui joc, memoizat pe fișier + geometrie + pool.

    Calculul e exact (hipergeometric per extragere), nu simulare, și durează sub
    o secundă per joc — memo-ul e doar ca redesenarea sidebar-ului să nu îl reia.
    Întoarce None dacă istoricul lipsește sau e prea scurt ca să fie tăiat în
    două, cazuri în care submeniul afișează motivul în loc de cifre.
    """
    from loto_enterprise.benchmark.runner import _list_istoric_dirs
    from loto_enterprise.core.base_threshold import interval_table
    from loto_enterprise.core.draw_validation import valid_draw_matrix

    path = next(
        (d / csv_name for d in _list_istoric_dirs() if (d / csv_name).exists()), None
    )
    if path is None:
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (str(path), stat.st_mtime_ns, stat.st_size, draw_n, max_num, pool_size)
    if key in _BASE_TABLE_MEMO:
        return _BASE_TABLE_MEMO[key]
    try:
        draws, _ = valid_draw_matrix(
            pd.read_csv(path),
            [f"n{i}" for i in range(1, draw_n + 1)],
            draw_n=draw_n,
            max_num=max_num,
        )
        result = (interval_table(draws, max_num, pool_size, draw_n), len(draws))
    except Exception as exc:  # noqa: BLE001
        logger.warning("tabel intervale %s: %s", csv_name, exc)
        return None
    while len(_BASE_TABLE_MEMO) >= _BASE_TABLE_MEMO_MAX:
        _BASE_TABLE_MEMO.pop(next(iter(_BASE_TABLE_MEMO)))
    _BASE_TABLE_MEMO[key] = result
    return result


@ui.refreshable
def _render_base_interval_tables() -> None:
    """Cel mai bun interval de fiecare lățime, per joc, lângă controlul lui.

    Procentele diferă de la joc la joc pentru că geometria diferă, de aceea
    tabelul e per joc. Coloana „uniform" este ACELAȘI calcul pe extrageri
    generate aleator, unde niciun număr nu e mai bun decât altul: dacă acolo
    apare un campion la fel de bun, campionul din coloana reală nu dovedește
    nimic. Submeniul nu setează niciun interval — alegerea rămâne a
    utilizatorului (AGENTS.md §6).
    """
    from loto_enterprise.core.base_threshold import theoretical_rate

    if not _BASE_TABLE_OPENED["value"]:
        ui.label("Deschide submeniul pentru a calcula tabelul.").classes(
            "text-caption text-grey"
        )
        return
    # `ui.number` își aplică min/max abia la blur, deci în timpul tastării pot
    # ajunge aici valori ca 0 sau 1. Fără plafonare, geometria era invalidă și
    # tabelul raporta „istoric indisponibil" pentru un istoric perfect valid.
    pool = max(6, min(16, _int_setting("pool_size_val")))
    ui.label(
        f"Pentru fiecare lățime de interval, intervalul cu cea mai bună rată de 3+ "
        f"la un pool de {pool} numere. Calcul exact (hipergeometric per extragere), "
        "nu simulare. Coloana «Toată extragerea» e altceva: de câte ori au încăput "
        "TOATE numerele extrase în interval — plafonul unui sistem care ar juca "
        "întregul interval, nu rata unui pool de dimensiune fixă. Ultimul rând este "
        "jocul nerestrâns și cade pe referința teoretică."
    ).classes("text-caption text-grey")
    for label, csv_name, draw_n, max_num in _BASE_TABLE_GAMES:
        if pool > max_num:
            continue
        data = _base_interval_rows(csv_name, draw_n, max_num, pool)
        if data is None:
            ui.label(f"· {label}: istoric indisponibil.").classes(
                "text-caption text-grey"
            )
            continue
        rows, n_draws = data
        with ui.expansion(f"{label} — {n_draws} extrageri", value=False).classes(
            "w-full"
        ):
            ui.table(
                columns=[
                    {"name": "w", "label": "Lățime", "field": "w", "align": "center"},
                    {
                        "name": "span",
                        "label": "Interval",
                        "field": "span",
                        "align": "center",
                    },
                    {"name": "whole", "label": "Tot", "field": "whole", "align": "center"},
                    {"name": "h1", "label": "Jum. 1", "field": "h1", "align": "center"},
                    {"name": "h2", "label": "Jum. 2", "field": "h2", "align": "center"},
                    {
                        "name": "ctrl",
                        "label": "🎲 Uniform",
                        "field": "ctrl",
                        "align": "center",
                    },
                    {
                        "name": "full",
                        "label": "Toată extragerea",
                        "field": "full",
                        "align": "center",
                    },
                ],
                rows=[
                    {
                        "w": r.width,
                        "span": r.label,
                        "whole": f"{r.whole:.2f}%",
                        "h1": f"{r.first_half:.2f}%",
                        "h2": f"{r.second_half:.2f}%",
                        "ctrl": f"{r.control_label} · {r.control:.2f}%",
                        "full": f"{r.full_draw:.2f}% (aşteptat {r.full_draw_theoretical:.2f}%)",
                    }
                    for r in rows
                ],
                row_key="w",
                pagination=0,
            ).classes("w-full")
            _moved = sum(
                1
                for r in rows
                if r.width < max_num and r.second_half < r.first_half
            )
            ui.label(
                f"Referință teoretică, identică pentru ORICE pool de {pool} numere: "
                f"{theoretical_rate(max_num, pool, draw_n):.2f}%. "
                f"Din {len(rows) - 1} intervale alese pe tot istoricul, {_moved} au "
                "ieșit mai slabe pe a doua jumătate decât pe prima."
            ).classes("text-caption text-grey")
    ui.label(
        "Cum se citește: un pool de dimensiune fixă are aceeași probabilitate "
        "indiferent care numere îl compun. Diferențele dintre intervale sunt abateri "
        "ale istoricului, iar cu cât intervalul e mai îngust, cu atât rămân mai puține "
        "combinații distincte și cu atât cifra e mai zgomotoasă — la lățime egală cu "
        "pool-ul există o singură combinație posibilă. Compară fiecare rând cu "
        "coloana uniformă, unde nu există nimic de găsit: dacă acolo apare un câștig "
        "de aceeași mărime, câștigul din coloana reală e zgomot."
    ).classes("text-caption text-grey")


# --------------------------------------------------------------------------- #
# Pagina principală
# --------------------------------------------------------------------------- #
@ui.page("/")
def main_page() -> None:
    ui.dark_mode().enable()

    # Chevron-ul expansion-urilor: vârful în JOS când e DESCHIS (arată spre conținut),
    # în sus când e închis — invers față de implicitul Quasar. Inversăm rotația global
    # pentru toate expansion-urile (q-expansion-item) printr-o singură regulă.
    ui.add_css("""
        .q-expansion-item__toggle-icon { transform: rotate(180deg) !important; }
        .q-expansion-item__toggle-icon--rotated { transform: rotate(0deg) !important; }
    """)

    with ui.header().classes("items-center justify-between"):
        ui.label("🎰 Loto Enterprise Wheeling").classes("text-h5")
        ui.label("NiceGUI — stare persistentă, fără reload").classes("text-caption")

    # ---- Sidebar (drawer stânga) ----
    with ui.left_drawer(fixed=False).props("width=360 bordered").classes("p-3"):
        ui.label("1. Încărcare Date CSV").classes("text-bold")

        async def _on_upload(e) -> None:
            # NiceGUI 3.12: e.file.read() e async. Încărcare DOAR manuală — nu
            # persistăm/auto-restaurăm nimic; ce alegi tu intră în sesiune.
            try:
                content = await e.file.read()
                name = e.file.name
                df = pd.read_csv(io.BytesIO(content))
            except Exception as exc:  # noqa: BLE001
                ui.notify(f"Nu pot citi fișierul: {exc}", type="negative")
                return
            STATE["datasets"] = [(f, d) for f, d in STATE["datasets"] if f != name] + [
                (name, df)
            ]
            ui.notify(f"Încărcat {name} ({len(df)} extrageri).", type="positive")
            datasets_label.refresh()

        ui.upload(on_upload=_on_upload, multiple=True, auto_upload=True).props(
            "accept=.csv"
        ).classes("w-full")

        @ui.refreshable
        def datasets_label() -> None:
            if STATE["datasets"]:
                ui.label(
                    "Încărcate: " + ", ".join(fn for fn, _ in STATE["datasets"])
                ).classes("text-caption text-positive")
                with ui.expansion("📅 Istoric CSV", value=False).classes("w-full"):
                    for fn, df in STATE["datasets"]:
                        _last = _csv_last_date(df)
                        ui.label(
                            f"{fn}: {len(df)} extrageri × {len(df.columns)} coloane"
                            + (f" (ultima: {_last})" if _last else "")
                        ).classes("text-caption")
            else:
                ui.label("Niciun CSV încărcat.").classes("text-caption text-warning")

        datasets_label()

        ui.separator()
        ui.label("2. Setări Algoritm").classes("text-bold")

        def _bind_save(widget, key):
            widget.bind_value(SETTINGS, key)
            widget.on_value_change(lambda: _save_settings())
            return widget

        _pool_input = _bind_save(
            ui.number("Dimensiune Pool (Nucleu Dur)", min=6, max=16, step=1).classes(
                "w-full"
            ),
            "pool_size_val",
        )
        _bind_save(
            ui.number("Garanție minimă (Set Cover)", min=3, max=6, step=1).classes(
                "w-full"
            ),
            "guarantee_val",
        )
        ui.label(
            "6 este sistem complet pentru 6/49; la 5/40 și Joker garanția efectivă "
            "este plafonată la 5 numere extrase."
        ).classes("text-caption text-grey")
        _bind_save(
            ui.number(
                "Garanția se aplică dacă în pool cad (0 = câte cere garanția)",
                min=0,
                max=6,
                step=1,
            ).classes("w-full"),
            "wheel_condition_val",
        )
        ui.label(
            "Lotto design „t dacă p”: de exemplu garanție 3 cu 4 = un bilet cu 3 numere garantat "
            "doar când cad 4 numere din pool. Economia de bilete depinde de dimensiunea pool-ului."
        ).classes("text-caption text-grey")
        _bind_save(
            ui.number(
                "Limită maximă variante (0=nelimitat)", min=0, max=10000, step=10
            ).classes("w-full"),
            "max_variants_val",
        )
        _bind_save(
            ui.number(
                "Penalizare numere extrase în ultimele N extrageri (0 = oprit)",
                min=0,
                max=50,
                step=1,
            ).classes("w-full"),
            "recent_penalty_draws_val",
        )
        _bind_save(
            ui.number(
                "Factor penalizare per apariție (0..0.99)", min=0, max=0.99, step=0.05
            ).classes("w-full"),
            "recent_penalty_factor_val",
        )
        ui.label(
            "Scorul unui număr extras de k ori în ultimele N extrageri se înmulțește cu factor^k. "
            "Apariția recentă nu face un număr mai puțin probabil la următoarea extragere. "
            "Avantajul penalizării nu este demonstrat; 0 extrageri o oprește. "
            "Walk-forward aplică aceeași setare."
        ).classes("text-caption text-grey")
        ui.label(
            "Restrânge candidații jucați la un interval de numere, SEPARAT pentru "
            "fiecare joc — de exemplu 10–40 la 6/49 în loc de 1–49. FĂRĂ avantaj "
            "statistic demonstrat: probabilitatea de hit a unui pool de dimensiune "
            "fixă e identică matematic, indiferent de care numere îl compun "
            "(verificat pe istoricul acestei aplicații, vezi tabelul de mai jos). "
            "E doar o preferință personală de compoziție a pool-ului, ca penalizarea "
            "recentă de mai sus — bifa e implicit OPRITĂ și aplicația nu alege și nu "
            "recomandă niciun interval. Walk-forward aplică aceeași restricție."
        ).classes("text-caption text-grey")

        def _on_restrict_base_toggle(event) -> None:
            if event.value:
                for _label, suffix, max_num in _RESTRICT_BASE_GAMES:
                    lo_key = f"restrict_base_min_{suffix}_val"
                    hi_key = f"restrict_base_max_{suffix}_val"
                    if not SETTINGS.get(lo_key) and not SETTINGS.get(hi_key):
                        # Prima activare, per joc: punct de plecare ca bifa să nu se
                        # deschidă pe câmpuri goale — NU e un interval recomandat de
                        # aplicație. Scalat de la exemplul „10-40" al lui 6/49
                        # (49 - 9 = 40) la universul fiecărui joc, ca marginea de sus
                        # să nu depășească niciodată jocul (5/40 nu poate merge la 40
                        # dacă exemplul ar fi copiat direct). Tabelul de mai jos și
                        # scripts/analysis/bench_base_threshold.py arată zgomot
                        # statistic pentru orice interval ales — utilizatorul schimbă
                        # liber ambele praguri sau lasă unul pe 0.
                        SETTINGS[lo_key] = 10
                        SETTINGS[hi_key] = max(10, max_num - 9)
            _save_settings()

        _restrict_enabled = ui.checkbox(
            "🎯 Restrânge baza de numere jucate (implicit oprită)"
        ).classes("w-full")
        _restrict_enabled.bind_value(SETTINGS, "restrict_base_enabled_val")
        _restrict_enabled.on_value_change(_on_restrict_base_toggle)

        _RESTRICT_BASE_ROW_LABEL = {
            "6/49": "6/49 (1–49)",
            "5/40": "5/40 (1–40)",
            "joker": "Joker Urna 1 (1–45)",
        }
        for _label, _suffix, _max_num in _RESTRICT_BASE_GAMES:
            with ui.row().classes("w-full items-center gap-2") as _rb_row:
                ui.label(_RESTRICT_BASE_ROW_LABEL[_label]).classes(
                    "text-caption w-24"
                )
                _bind_save(
                    ui.number(
                        "de la", min=0, max=_max_num, step=1
                    ).classes("w-20"),
                    f"restrict_base_min_{_suffix}_val",
                )
                _bind_save(
                    ui.number(
                        "până la", min=0, max=_max_num, step=1
                    ).classes("w-20"),
                    f"restrict_base_max_{_suffix}_val",
                )
            # Rândurile apar doar cât bifa e activă — ascunse, nu dispărute: valorile
            # tastate rămân la reactivare. Config_json ignoră oricum toate cele trei
            # praguri cât bifa e oprită (vezi `_active_restrict_base`), deci o
            # valoare rămasă într-un câmp nu poate ajunge în producție pe furiș.
            _rb_row.bind_visibility_from(_restrict_enabled, "value")
        ui.label(
            "Interval inversat (minim > maxim) este ignorat și consemnat în audit, "
            "nu aplicat peste o bază goală."
        ).classes("text-caption text-grey").bind_visibility_from(
            _restrict_enabled, "value"
        )

        def _toggle_intervals(event) -> None:
            _BASE_TABLE_OPENED["value"] = bool(event.value)
            _render_base_interval_tables.refresh()

        _intervals = ui.expansion(
            "📐 Cel mai bun interval de fiecare lățime, per joc", value=False
        ).classes("w-full")
        with _intervals:
            _render_base_interval_tables()
        _intervals.on_value_change(_toggle_intervals)
        # Procentele depind de dimensiunea pool-ului, deci tabelul se recalculează
        # când aceasta se schimbă. Intervalul ales de utilizator nu intră în calcul —
        # tabelul arată toate lățimile, nu îl evidențiază pe cel setat.
        _pool_input.on_value_change(lambda: _render_base_interval_tables.refresh())
        _bind_save(
            ui.number(
                "Analizează doar ultimele X% extrageri", min=0, max=100, step=5
            ).classes("w-full"),
            "lookback_val",
        )
        _bind_save(
            ui.number(
                "Fereastră bench (telemetrie, nu schimbă pool-ul) (%)",
                min=10,
                max=100,
                step=10,
            ).classes("w-full"),
            "sim_depth_val",
        )
        ui.label(
            "Fereastra de mai sus e telemetrie Auto-Pilot (unde avg_hits a picat pe bench). "
            "NU filtrează numere și NU schimbă biletele. Walk-forward validează ultimele "
            f"{int(WF_DEPTH_PERCENT)}% din istoric. «Ultimele X% extrageri» taie CSV-ul de producție "
            "(0 = tot istoricul)."
        ).classes("text-caption text-grey")
        _bind_save(
            ui.number("⏱ Buget walk-forward (minute)", min=1, max=480, step=5).classes(
                "w-full"
            ),
            "wf_budget_min",
        )

        def _on_target_change(e):
            target = _clamped_bench_target(e.value)
            SETTINGS["bench_hit_target"] = target
            _save_settings()
            try:
                import loto_enterprise.benchmark.decision as decision

                decision.BENCH_HIT_TARGET = target
                os.environ["LOTO_BENCH_TARGET"] = str(target)
                if (PROJECT_ROOT / "bench_results" / "folds.csv").exists():
                    decision.update_best_methods_with_auto_pilot()
                    _mismatch = False
                    try:
                        from loto_enterprise.core.method_selector import (
                            recommend_optimal_config,
                        )

                        for _gk in ("loto_6_49", "loto_5_40", "joker_urna1"):
                            _c = recommend_optimal_config(
                                _gk, _int_setting("pool_size_val")
                            )
                            if _c.get("rate_col_mismatch"):
                                _mismatch = True
                                break
                    except Exception:  # noqa: BLE001
                        pass
                    if _mismatch:
                        ui.notify(
                            f"Decizie actualizată, DAR folds nu au coloane {target}+ "
                            f"— s-a folosit 4+ (rate_col_mismatch). Rulează Re-Bench.",
                            type="warning",
                        )
                    else:
                        ui.notify(
                            f"Decizia Auto-Pilot a fost actualizată pentru {target}+ hits!",
                            type="info",
                        )
                    _refresh_status()
                    results_panel.refresh()
            except FileNotFoundError:
                # best_methods.json e gitignored: pe un clone proaspăt (sau după
                # ștergere) nu există, iar `update_best_methods_with_auto_pilot`
                # aruncă ÎNAINTE de `ui.notify`/`_refresh_status`, deci utilizatorul
                # schimba ținta și nu vedea absolut nimic — nici succes, nici
                # eroare — deși decizia NU fusese recalculată.
                ui.notify(
                    "Nu există încă best_methods.json — rulează un Re-Bench "
                    "ca ținta să fie aplicată.",
                    type="warning",
                )
            except Exception as exc:
                logger.warning("Eroare la schimbarea țintei de hituri: %s", exc)
                ui.notify(f"Nu am putut recalcula decizia: {exc}", type="negative")

        ui.select(
            {3: "3+ Hits", 4: "4+ Hits"},
            value=_clamped_bench_target(),
            label="🎯 Țintă Optimizare / Bench",
            on_change=_on_target_change,
        ).classes("w-full")
        ui.label(
            "Ținta se aplică la 6/49 și Joker (Urna 1). Loto 5/40 rămâne mereu pe 4+: "
            "hiturile se numără pe toate cele 6 numere extrase, iar 3 numere nu aduc premiu."
        ).classes("text-caption text-grey")
        ui.label(
            f"Validarea pool-ului (pe ultimele {int(WF_DEPTH_PERCENT)}% din istoric): "
            "Joker → 5/40 → 6/49 (6/49 ultim). "
            "WF paralel (~80% CPU) — de obicei minute, nu ore. Bugetul e plafon de siguranță."
        ).classes("text-caption text-grey")
        _bind_save(
            ui.checkbox("🔌 Oprește PC-ul automat la final"), "shutdown_on_complete"
        )
        _bind_save(
            ui.checkbox("📧 Trimite rezultatele pe mail la final"), "mail_on_complete"
        )
        ui.button("📧 Trimite mail de test", on_click=_send_test_email).props(
            "outline no-caps size=sm"
        ).classes("text-caption")

        ui.separator()
        ui.label("3. Control Execuție").classes("text-bold")
        _BTN = "w-full"
        _BTN_STYLE = "white-space:normal;line-height:1.2;min-height:40px"
        ui.button(
            "⚡ Auto-Pilot (decizie bench + generează)",
            on_click=apply_autopilot_and_generate,
        ).props("color=primary no-caps").classes(_BTN).style(_BTN_STYLE)
        ui.button(
            "🚀 Generează (setări manuale)",
            on_click=lambda: submit_generation(pure=False),
        ).props("no-caps").classes(_BTN).style(_BTN_STYLE)

        ui.separator()
        ui.button("🔬 RE-BENCH", on_click=run_rebench).props(
            "color=orange no-caps"
        ).classes(_BTN).style(_BTN_STYLE)
        _bt = _clamped_bench_target()
        ui.label(
            "Un singur bench testează metodele relevante fiecărui joc (exclusiv CPU), "
            "pe toate nucleele (în paralel). În fiecare joc, metodele concurează în "
            f"ACELAȘI clasament → UN câștigător (regula {_bt}+) → UN Auto-Pilot → UN walk-forward. "
            "Vezi clasamentul complet la 🏆 Clasament bench."
        ).classes("text-caption")
        _eta_folds = _target_bench_folds()
        if _eta_folds:
            ui.label(
                f"⏱ ETA estimat: {_estimate_bench_eta(_eta_folds)} pentru "
                f"{_eta_folds} folduri — calculat din durata ultimei rulări; "
                "prima estimare după o schimbare de matrice e optimistă."
            ).classes("text-caption text-grey")
        # Curare REVERSIBILĂ a setului de metode (curated_methods.json). Dacă e
        # activă, bench-ul rulează un SUBSET — spunem clar câte și cum se anulează.
        _cur = _curation_banner_info()
        if _cur is not None:
            _pg = _cur.get("per_game") or {}
            _main_pg_txt = "/".join(
                str(int(_pg[g]))
                for g in ("loto_6_49", "loto_5_40", "joker_urna1")
                if g in _pg
            )
            _urna2_n = _pg.get("joker_urna2")
            if _main_pg_txt and _urna2_n is not None:
                _pg_bit = (
                    f"matrice Re-Bench = {_main_pg_txt} jocuri principale "
                    f"+ {int(_urna2_n)} Urna 2, plus baseline-urile structurale"
                )
            elif _main_pg_txt:
                _pg_bit = f"matrice Re-Bench = {_main_pg_txt} per joc, plus baseline-urile structurale"
            elif _urna2_n is not None:
                _pg_bit = f"matrice Re-Bench = {int(_urna2_n)} Urna 2, plus baseline-urile structurale"
            else:
                _pg_bit = "matrice Re-Bench = tot setul activ"
            _n_after = _cur["n_after"]
            ui.html(
                render_html_safe(
                    t"🎯 <b>Curare activă: {_n_after} metode din {_cur['n_before']}</b> "
                    t"(uniune eligibilă; {_pg_bit})."
                )
            ).classes("text-caption text-info")
            ui.label(
                "Dezactivare (revine la toate metodele): șterge sau golește lista "
                f"'active' din {_cur['path']}, apoi rulează un Re-Bench. "
                "Nimic nu se pierde — nu e blacklist."
            ).classes("text-caption text-grey")
            if _cur["missing_required"]:
                ui.label(
                    "⚠️ Lipsesc din curare metode structurale "
                    f"({', '.join(_cur['missing_required'])}) — decizia bench poate "
                    "cădea pe low_confidence. Adaugă-le în curated_methods.json."
                ).classes("text-caption text-negative")
        # Gard anti-surpriză: extrageri noi de la ultimul bench + avertisment că datele
        # noi invalidează cache-ul (re-bench = recalcul complet). Snapshot la randarea
        # paginii (se reîmprospătează la reload). Vezi _new_draws_summary / freshness.
        _fresh = _new_draws_summary()
        if _fresh is not None and _fresh["any_bench"]:
            if _fresh["total"] > 0:
                _g2l = {v: k for k, v in GK_MATRIX.items()}
                _parts = ", ".join(
                    f"{_g2l.get(gk, gk)} +{d}" for gk, d in _fresh["per"].items()
                )
                _col = (
                    "text-negative"
                    if _fresh["rec"] == "full_rebench"
                    else "text-warning"
                )
                _fresh_total = _fresh["total"]
                ui.html(
                    render_html_safe(
                        t"🆕 <b>+{_fresh_total} extrageri noi</b> de la ultimul bench ({_parts})."
                    )
                ).classes("text-caption " + _col)
                ui.label(
                    "⚠️ Datele noi invalidează cache-ul → Re-Bench = recalcul COMPLET (nu rapid). "
                    "Pentru generarea zilnică NU e nevoie de re-bench: Auto-Pilot folosește deja "
                    "datele noi, iar câștigătorul bench abia se schimbă la câteva extrageri."
                ).classes("text-caption " + _col)
            elif _fresh["rec"] in ("quick_rebench", "full_rebench"):
                ui.label(
                    "⚠️ Datele s-au schimbat de la ultimul bench → Re-Bench recalculează complet (fără cache)."
                ).classes("text-caption text-warning")
            elif _target_data_ready():
                ui.label(
                    "✅ Date neschimbate de la ultimul bench → Re-Bench folosește cache-ul (rapid)."
                ).classes("text-caption text-positive")
            else:
                ui.label(
                    f"⚠️ Următorul Re-Bench va fi COMPLET (~lent, nu din cache): datele pentru pragul "
                    f"curent (≥{_bench_target()}) nu-s încă în cache (schemă nouă / prag schimbat). "
                    "O singură dată — apoi redevine rapid."
                ).classes("text-caption text-warning")
        _bind_save(
            ui.checkbox("⚡ Pornește Auto-Pilot automat după Re-Bench"),
            "autopilot_after_bench",
        )

        ui.separator()
        ui.button("🔴 Anulează TOT Procesul", on_click=cancel_all).props(
            "color=negative outline no-caps"
        ).classes("w-full").style(_BTN_STYLE)
        ui.button(
            "🗑️ Șterge Log", on_click=lambda: (clear_logs(), logs_panel.refresh())
        ).props("outline no-caps").classes("w-full").style(_BTN_STYLE)

    # ---- Zona principală ----
    with ui.column().classes("w-full p-4 gap-2"):
        status_panel()
        with ui.expansion("🛠 Consolă DEBUG / Loguri (live)", value=False).classes(
            "w-full"
        ):
            logs_panel()
        results_panel()

    # ---- Polling fără reload. Munca BLOCANTĂ (citiri loguri OneDrive, psutil, pid-uri)
    # rulează în io_bound (thread), ca event-loop-ul UI să NU se blocheze → fără
    # 'connection lost'. Doar refresh-ul UI (rapid, din cache STATE) e pe loop. ----
    async def _tick() -> None:
        from nicegui import run as _nrun

        def _blocking_probe():
            """Rulat în THREAD: pid bench + citiri loguri (lente OneDrive) → cache STATE."""
            try:
                bn = _bench_running()
            except Exception:  # noqa: BLE001
                bn = False
            try:
                STATE["_log_cache"] = read_logs_filtered(120)
            except Exception:  # noqa: BLE001
                pass
            return bn

        try:
            bench_now = await _nrun.io_bound(_blocking_probe)
        except Exception:  # noqa: BLE001
            bench_now = False

        _active = bool(
            STATE.get("active_job_id") or bench_now or STATE.get("wf_running")
        )
        if STATE.pop("results_dirty", False):
            # Cerut din thread-ul WF: refresh-ul se execută AICI, pe event-loop.
            try:
                results_panel.refresh()
                status_panel.refresh()
            except Exception as exc:  # noqa: BLE001
                logger.debug("[tick] refresh după walk-forward eșuat: %s", exc)
        # Re-Bench terminat → Auto-Pilot automat
        if STATE.get("bench_was_running") and not bench_now:
            STATE["bench_was_running"] = False
            if not STATE.get("bench_cancelled"):
                _on_bench_finished()
        elif bench_now:
            STATE["bench_was_running"] = True
            STATE["bench_cancelled"] = False
        # Refresh UI (rapid, din STATE) — doar când e activ
        if _active:
            logs_panel.refresh()
            status_panel.refresh()
        if STATE.get("wf_running"):
            # DOAR progresul WF — NU tot bundle-ul, ca expansion-urile deschise
            # (🏆 Clasament bench etc.) să NU se închidă la fiecare poll de 1s.
            wf_progress_panel.refresh()

    ui.timer(1.0, _tick)


# --------------------------------------------------------------------------- #
# Recuperare la pornire a unui job terminat cât UI-ul era jos
# --------------------------------------------------------------------------- #
# Fereastra în care un job COMPLETED proaspăt mai DECLANȘEAZĂ mail + shutdown la
# pornirea UI-ului. Peste ea: DOAR afișăm rezultatul (fără shutdown-surpriză la o
# repornire mult ulterioară, fără mail cu numere vechi). Ținut SCURT intenționat:
# dacă UI-ul revine în câteva minute e aproape sigur o repornire automată (cazul
# „finalize ratat"); mai târziu = probabil utilizator prezent, care poate re-rula.
# Shutdown-ul rămâne oricum anulabil 60s prin banner.
RECOVERY_FINALIZE_WINDOW_S = 10 * 60


def _completed_age_seconds(job: dict) -> float | None:
    """Secunde de la finalizarea jobului. completed_at e UTC (CURRENT_TIMESTAMP).
    None dacă lipsește/necitibil (joburi de dinainte de migrarea coloanei) SAU dacă
    ceasul a sărit înapoi semnificativ (vechime negativă mare) — în acel caz NU
    riscăm să clasificăm un job vechi drept „proaspăt" (= shutdown-surpriză)."""
    ts = job.get("completed_at")
    if not ts:
        return None
    try:
        t = _dt.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S")  # naiv = UTC
        now_utc = _dt.now(_tz.utc).replace(
            tzinfo=None
        )  # naiv UTC (fără deprecation utcnow)
        delta = (now_utc - t).total_seconds()
        if delta < -120:
            # Ceas dat înapoi (corecție NTP, resume VM, baterie BIOS) → suspect, NU proaspăt.
            logger.warning(
                "[RECOVERY] completed_at în viitor cu %.0fs (ceas?) → tratez ca vechi.",
                -delta,
            )
            return None
        return max(0.0, delta)  # micile negative (sub-secundă) → 0
    except Exception:  # noqa: BLE001
        return None


def _recover_completed_job(*, allow_finalize: bool = True) -> None:
    """get_active_job() vede DOAR PENDING/RUNNING. Dacă worker-ul a terminat un job
    cât UI-ul era complet jos, rezultatul (+ mail/shutdown de la final) ar rămâne
    orfan. Îl readucem în flux O SINGURĂ DATĂ:
      • PROASPĂT (în fereastră și allow_finalize) → flux COMPLET: afișare +
        walk-forward + mail + shutdown (ca o finalizare normală pe care UI-ul a ratat-o);
      • VECHI / fără completed_at → DOAR afișare (fără shutdown-surpriză, fără mail vechi).
      • START_8000 (`allow_finalize=False`) → DOAR afișare chiar dacă e proaspăt:
        „sesiune nouă, fără job automat” nu are voie să trimită mail sau să oprească PC-ul.
    `last_finalized_job_id` (persistat) împiedică re-procesarea la următoarea repornire."""
    last = get_latest_completed_job()
    if not last:
        return
    jid = int(last["id"])
    try:
        already = int(SETTINGS.get("last_finalized_job_id") or 0)
    except (TypeError, ValueError):
        already = 0
    if jid == already:
        return  # deja dus prin finalize într-o sesiune anterioară

    payload = decode_queue_result(str(last.get("result_json") or "{}"))
    if not (isinstance(payload, tuple) and len(payload) == 2):
        # payload gol/invalid (ex. cancel-race) → marcăm văzut, nu reîncercăm la infinit
        SETTINGS["last_finalized_job_id"] = jid
        _save_settings()
        return

    age = _completed_age_seconds(last)
    if allow_finalize and age is not None and age <= RECOVERY_FINALIZE_WINDOW_S:
        # Proaspăt → status_panel îl preia exact ca pe o finalizare normală (decode +
        # STATE["results"] + walk-forward + mail + shutdown) și setează last_finalized.
        # NU pornim worker-ul (jobul e gata).
        STATE["active_job_id"] = jid
        logger.warning(
            "[RECOVERY] job #%s terminat acum %ss (în fereastră) → "
            "finalizez complet (mail/shutdown).",
            jid,
            int(age),
        )
    else:
        # Vechi sau fără completed_at → doar afișăm numerele, fără mail/shutdown.
        # Marcăm CLAR că-s dintr-o sesiune anterioară (la loto, a juca numere vechi
        # crezându-le curente e o eroare reală) — afișat ca avertisment în status_panel.
        when = str(last.get("completed_at") or "")[:16] or "sesiune anterioară"
        with STATE_LOCK:
            STATE["results"] = payload
            STATE["results_recovered"] = f"job #{jid} · {when}"
        SETTINGS["last_finalized_job_id"] = jid
        _save_settings()
        try:
            _save_report_file()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[RECOVERY] raport: %s", exc)
        _why = (
            "START_8000 fresh"
            if not allow_finalize
            else "necunoscut"
            if age is None
            else f"{int(age)}s"
        )
        logger.warning(
            "[RECOVERY] job #%s display-only (%s) → fără mail/shutdown.", jid, _why
        )


# --------------------------------------------------------------------------- #
# Bootstrap
# --------------------------------------------------------------------------- #
def _startup() -> None:
    init_job_queue()
    # NU marcăm joburile RUNNING ca eșuate: worker.py e proces separat care
    # supraviețuiește repornirii UI-ului → un job viu trebuie re-atașat, nu omorât.
    _load_settings()
    # NU auto-încărcăm CSV-uri: utilizatorul încarcă manual de fiecare dată.
    # La boot-ul procesului UI user-ul n-a apăsat încă Generează. Un job 0%
    # fără log e leftover — dacă îl reatașăm, ecranul rămâne pe
    # «⏳ Job în rulare (#1) — 0% / se inițializează...» la infinit
    # (worker-ul nu l-a preluat). START_8000 anulează TOATE leftover-urile.
    fresh_start = is_fresh_ui_start()
    try:
        if fresh_start:
            n = cancel_pending_running_jobs(
                "Pornire START_8000: sesiune nouă, fără job automat."
            )
            if n:
                logger.warning(
                    "[STARTUP] LOTO_FRESH_START: anulate %s job(uri) leftover.",
                    n,
                )
        else:
            active = get_active_job()
            if active and is_unstarted_job(active):
                jid = int(active["id"])
                # DOAR jobul orfan identificat, nu tot ce e în zbor: fără
                # `job_ids` UPDATE-ul prindea orice PENDING/RUNNING, deci un job
                # aflat în lucru era anulat împreună cu orfanul.
                cancel_pending_running_jobs(
                    f"Job orfan #{jid} anulat la pornirea UI (0%, fără log).",
                    job_ids=[jid],
                )
                logger.warning(
                    "[STARTUP] job #%s nepornit → anulat (nu reatașez).",
                    jid,
                )
            elif active:
                STATE["active_job_id"] = int(active["id"])
                ensure_worker_running()
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_active_job startup: %s", exc)
    # Job terminat cât UI-ul era COMPLET jos (get_active_job vede doar PENDING/RUNNING)
    # → altfel rezultatul + mail/shutdown rămân orfane. Doar dacă nu avem deja unul activ.
    try:
        if not STATE.get("active_job_id"):
            _recover_completed_job(allow_finalize=not fresh_start)
    except Exception as exc:  # noqa: BLE001
        logger.warning("recover completed job startup: %s", exc)


# Star-import skips _-prefixed names; functions look up globals in the defining
# module. Copy private helpers (and `time`) so Generate / Auto-Pilot / panels
# resolve after the UI split.
def _sync_ui_namespace() -> None:
    import ui_bench
    import ui_hits
    import ui_results
    import ui_runtime

    facade = sys.modules[__name__]
    modules = (ui_runtime, ui_results, ui_bench, ui_hits, facade)
    merged: dict = {}
    for mod in modules:
        for key, value in vars(mod).items():
            if key.startswith("__") and key.endswith("__"):
                continue
            if key.startswith("_") or key == "time":
                merged[key] = value
    merged["time"] = time
    for mod in modules:
        vars(mod).update(merged)


_sync_ui_namespace()

app.on_startup(_startup)

if __name__ in {"__main__", "__mp_main__"}:
    _port = int(os.environ.get("LOTO_UI_PORT", "8000"))
    # show=False: browserul e deschis de START_8000.bat (mai fiabil pe Windows).
    # reconnect_timeout mărit: cât rulează bench/walk-forward, event-loop-ul poate fi
    # ocupat (citiri loguri OneDrive) → fără timeout generos, WebSocket pica 'connection lost'.
    ui.run(
        title="Loto Enterprise Wheeling",
        port=_port,
        reload=False,
        show=False,
        dark=True,
        reconnect_timeout=60.0,
    )

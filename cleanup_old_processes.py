"""Omoară UI / worker / bench rămase din sesiunea anterioară.

Apelat din START_8000.bat ÎNAINTE de reset_jobs.py. Best-effort: nu blochează
pornirea dacă un PID a murit între scan și kill.

De ce NU e suficient PowerShell + CommandLine like %~dp0:
  `_launch_bench` pornește `python bench_all_methods.py` cu cale RELATIVĂ
  (cwd = proiect, exe = venv în D:\\_BUILD\\_LOTO, în AFARA repo-ului) → calea
  proiectului NU apare în CommandLine. Același bug era în cancel_all() din UI
  (comentariu în app_nicegui.py). În plus, Stop-Process / taskkill fără /T
  lasă copiii ProcessPool în viață: pe Windows uciderea părintelui NU omoară
  descendenții.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

# Consola Windows e cp1252 by default -> diacriticele arunca UnicodeEncodeError.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCRIPT_MARKERS = frozenset({
    "app_nicegui.py",
    "worker.py",
    "bench_all_methods.py",
})
_PYTHON_NAMES = frozenset({
    "python", "python.exe", "pythonw", "pythonw.exe",
    "python3", "python3.exe",
})
_SYSTEM_PIDS = frozenset({0, 4})


def _norm(path: str | os.PathLike[str] | None) -> str:
    if not path:
        return ""
    return os.path.normcase(str(path)).replace("\\", "/").rstrip("/")


def path_is_under(path: str | os.PathLike[str] | None, root: str | os.PathLike[str]) -> bool:
    """True dacă `path` e `root` sau un fiu al lui. Acceptă / și \\, case-insensitive."""
    npath = _norm(path)
    nroot = _norm(root)
    if not npath or not nroot:
        return False
    return npath == nroot or npath.startswith(nroot + "/")


def _basename(part: str) -> str:
    """Basename care tratează și `\\` (Path.name pe POSIX lasă tot Windows path-ul)."""
    return str(part).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def cmdline_script_names(cmdline: list[str] | tuple[str, ...] | None) -> set[str]:
    names: set[str] = set()
    for part in cmdline or ():
        base = _basename(part)
        if base:
            names.add(base.lower())
    return names


@dataclass(frozen=True)
class ProcView:
    pid: int
    name: str = ""
    exe: str | None = None
    cmdline: tuple[str, ...] = ()
    cwd: str | None = None
    parent_pid: int | None = None


def is_stale_proc(
    proc: ProcView,
    *,
    venv_dir: str | os.PathLike[str],
    project_root: str | os.PathLike[str],
    keep_pids: set[int],
) -> bool:
    """True dacă procesul e python al acestui proiect și trebuie omorât la restart.

    Potrivire pe DOUĂ axe (OR), ca un filtru să nu le scape pe celelalte:
      1. exe-ul e în venv-ul dedicat (UI, worker, bench, copii ProcessPool)
      2. un script al proiectului e pe cmdline ȘI (calea proiectului e în cmdline
         SAU CWD-ul e proiectul) — acoperă python pornit în afara venv-ului
    """
    if proc.pid in keep_pids or proc.pid in _SYSTEM_PIDS:
        return False
    name = (proc.name or "").lower()
    if name and name not in _PYTHON_NAMES:
        return False

    if path_is_under(proc.exe, venv_dir):
        return True

    scripts = cmdline_script_names(proc.cmdline)
    if not (scripts & SCRIPT_MARKERS):
        return False

    cmd = _norm(" ".join(proc.cmdline))
    if _norm(project_root) and _norm(project_root) in cmd:
        return True
    return bool(proc.cwd) and path_is_under(proc.cwd, project_root)


def expand_descendants(stale_pids: set[int], procs: list[ProcView]) -> set[int]:
    """Adaugă tot arborele de copii. Windows nu omoară descendenții odată cu părintele."""
    by_parent: dict[int, list[int]] = {}
    for p in procs:
        if p.parent_pid is None:
            continue
        by_parent.setdefault(p.parent_pid, []).append(p.pid)
    out = set(stale_pids)
    stack = list(stale_pids)
    while stack:
        pid = stack.pop()
        for child in by_parent.get(pid, ()):
            if child not in out and child not in _SYSTEM_PIDS:
                out.add(child)
                stack.append(child)
    return out


def select_stale_pids(
    procs: list[ProcView],
    *,
    venv_dir: str | os.PathLike[str],
    project_root: str | os.PathLike[str],
    keep_pids: set[int],
    listen_pids: set[int] | None = None,
) -> set[int]:
    stale = {
        p.pid
        for p in procs
        if is_stale_proc(p, venv_dir=venv_dir, project_root=project_root, keep_pids=keep_pids)
    }
    if listen_pids:
        stale.update(pid for pid in listen_pids if pid not in keep_pids and pid not in _SYSTEM_PIDS)
    return expand_descendants(stale, procs) - keep_pids - _SYSTEM_PIDS


def _keep_pids() -> set[int]:
    keep = {os.getpid(), 0}
    try:
        import psutil
        keep.update(p.pid for p in psutil.Process().parents())
    except Exception:
        try:
            keep.add(os.getppid())
        except OSError:
            pass
    return keep


def _snapshot() -> list[ProcView]:
    import psutil
    views: list[ProcView] = []
    for proc in psutil.process_iter(["pid", "name", "exe", "cmdline", "ppid"]):
        info = proc.info
        cwd: str | None = None
        try:
            cwd = proc.cwd()
        except (psutil.Error, OSError):
            cwd = None
        cmdline = info.get("cmdline") or []
        views.append(
            ProcView(
                pid=int(info["pid"]),
                name=str(info.get("name") or ""),
                exe=info.get("exe"),
                cmdline=tuple(str(x) for x in cmdline),
                cwd=cwd,
                parent_pid=info.get("ppid"),
            )
        )
    return views


def _listen_pids(port: int) -> set[int]:
    if port <= 0:
        return set()
    import psutil
    pids: set[int] = set()
    try:
        conns = psutil.net_connections(kind="inet")
    except (psutil.Error, OSError):
        return pids
    for conn in conns:
        try:
            if conn.status != psutil.CONN_LISTEN:
                continue
            if not conn.laddr or int(conn.laddr.port) != port:
                continue
            if conn.pid:
                pids.add(int(conn.pid))
        except (TypeError, ValueError, AttributeError):
            continue
    return pids


def _kill_pid(pid: int) -> bool:
    import psutil
    try:
        psutil.Process(pid).kill()
        return True
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return False


def kill_stale_project_processes(
    *,
    venv_dir: str | os.PathLike[str],
    project_root: str | os.PathLike[str] | None = None,
    port: int = 8000,
    dry_run: bool = False,
) -> list[int]:
    root = Path(project_root) if project_root else Path(__file__).resolve().parent
    keep = _keep_pids()
    procs = _snapshot()
    listening = _listen_pids(port)
    stale = select_stale_pids(
        procs,
        venv_dir=venv_dir,
        project_root=root,
        keep_pids=keep,
        listen_pids=listening,
    )
    # Copiii întâi: parent_pid care NU e tot stale vine după; sortăm invers după
    # adâncime aproximativă (PID mare nu e adâncime). Construim adâncimea din ppid.
    by_pid = {p.pid: p for p in procs}

    def depth(pid: int) -> int:
        d = 0
        seen: set[int] = set()
        cur = pid
        while cur in by_pid and by_pid[cur].parent_pid and cur not in seen:
            seen.add(cur)
            cur = by_pid[cur].parent_pid  # type: ignore[assignment]
            d += 1
            if cur not in stale:
                break
        return d

    ordered = sorted(stale, key=depth, reverse=True)
    acted: list[int] = []
    for pid in ordered:
        if dry_run:
            print(f"[CLEANUP] dry-run PID {pid}")
            acted.append(pid)
            continue
        if _kill_pid(pid):
            print(f"[CLEANUP] Oprit PID {pid}")
            acted.append(pid)
        else:
            print(f"[CLEANUP] PID {pid} deja mort sau inaccesibil")
    if not acted:
        print("[CLEANUP] Niciun proces vechi de oprit.")
    return acted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--venv",
        default=os.environ.get("VIRTUAL_ENV", r"D:\_BUILD\_LOTO\.venv"),
        help="Venv dedicat (implicit D:\\_BUILD\\_LOTO\\.venv)",
    )
    parser.add_argument(
        "--project-root",
        default=str(Path(__file__).resolve().parent),
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        kill_stale_project_processes(
            venv_dir=args.venv,
            project_root=args.project_root,
            port=args.port,
            dry_run=args.dry_run,
        )
    except Exception as exc:  # noqa: BLE001 — pornirea NU trebuie blocată
        print(f"[CLEANUP] Esec best-effort: {type(exc).__name__}: {exc}")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

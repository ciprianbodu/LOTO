"""Sterge artefactele reziduale care nu mai sunt citite de aplicatie.

Rulat de ACTUALIZARI.bat dupa migrarea/curatarea cache-ului WF. Lista este
EXPLICITA (allow-list): fiecare intrare e un artefact al unei functii sterse sau
al unei configuratii vechi, pe care codul curent nu il mai citeste (verificat cu
grep la 2026-09-28). Nimic urmarit de git nu se sterge; fara git, fisierele
care ar putea fi urmarite (module sterse) sunt sarite. Iesire mereu 0.

    python cleanup_residual.py [--dry-run]
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Directoare din radacina proiectului, fara niciun cititor in codul curent.
OBSOLETE_DIRS = (
    "venv_timesfm",  # stack TimesFM/GPU eliminat (AGENTS §10)
    ".venv311",  # venv Python 3.11, inlocuit de D:\_BUILD\_LOTO\.venv (3.14)
    "lightning_logs",  # loguri PyTorch Lightning (NeuralForecast eliminat)
    ".numba_cache",  # cache numba, dependinta eliminata
    ".loto_cache",  # cache vechi, niciun cititor
    "bench_results_gpu",  # rezultate bench GPU, metode GPU eliminate
    ".pytest_cache",  # regenerat de pytest
)

# Fisiere din radacina proiectului, fara niciun cititor in codul curent.
OBSOLETE_FILES = (
    "disabled_methods.json",  # tombstone eliminat la 14.09.2026
    "backtest_results.json",  # output backtest vechi
    ".machine_profile",
    ".button_press_ts",
)

# Tipare (numai in radacina proiectului): backup-uri ale unor scrieri vechi.
OBSOLETE_GLOBS = (
    "*.backup_overnight",
    "*.pre_autopilot",
    "best_methods.json.pre_*",
)

# Module/teste sterse din repo (git log --diff-filter=D). O copie ramasa
# neurmarita (ex. sincronizare cloud) ar fi colectata de pytest si ar esua.
# Nu includem .bat: fisierele .bat personale nu se sterg niciodata.
DELETED_MODULES = (
    "analiza_math_external.py",
    "prune_methods.py",
    "refine_649_blend.py",
    "search_649_methods.py",
    "loto_enterprise/benchmark/disabled.py",
    "loto_enterprise/benchmark/methods_classical.py",
    "loto_enterprise/benchmark/methods_coverage.py",
    "loto_enterprise/benchmark/methods_graph.py",
    "loto_enterprise/benchmark/methods_math_extra.py",
    "loto_enterprise/benchmark/methods_ml.py",
    "loto_enterprise/benchmark/methods_revived.py",
    "loto_enterprise/benchmark/methods_search_649.py",
    "loto_enterprise/benchmark/methods_top649.py",
    "scripts/analysis/eval_revived_cover.py",
    "test_analiza_math_external.py",
    "test_disabled.py",
    "test_math_external_curation.py",
    "test_methods_math_extra_robustness.py",
    "test_parity_balance.py",
    "test_prune_methods.py",
    "test_revived_cover_curation.py",
    "test_revived_cpu_methods.py",
    "test_search_649_methods.py",
    "test_sum_affinity.py",
)

# Loguri scrise in radacina proiectului inainte de runtime_paths; ramase
# acolo cand RUNTIME_ROOT e alt director (D:\_BUILD\_LOTO), nu le mai scrie nimeni.
LEGACY_ROOT_LOGS = ("loto.log", "bench_full.log", "startup_8000.log")

# Directoare in care nu coboram niciodata dupa __pycache__.
_NO_DESCEND = {".git", ".claude", "_ISTORIC", "node_modules"}


def _tracked_files(root: Path) -> set[str] | None:
    """Caile urmarite de git (relative, cu '/'); None daca git nu raspunde."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            timeout=60,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return {p for p in out.decode("utf-8", "replace").split("\0") if p}


def _size(path: Path) -> int:
    if path.is_file() or path.is_symlink():
        try:
            return path.lstat().st_size
        except OSError:
            return 0
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass
    return total


def _is_tracked(rel: str, tracked: set[str]) -> bool:
    prefix = rel.rstrip("/") + "/"
    return rel in tracked or any(t.startswith(prefix) for t in tracked)


def _is_venv(path: Path) -> bool:
    return (path / "pyvenv.cfg").exists()


def collect_candidates(
    root: Path, runtime_root: Path, tracked: set[str] | None
) -> list[tuple[Path, str]]:
    """Lista (cale, motiv) a artefactelor de sters; numai din allow-list."""
    root = root.resolve()
    found: list[tuple[Path, str]] = []

    def add(path: Path, reason: str, needs_git: bool = False) -> None:
        if not (path.exists() or path.is_symlink()):
            return
        try:
            rel = path.resolve().relative_to(root).as_posix()
        except ValueError:
            return  # niciodata in afara proiectului
        if tracked is None:
            if needs_git:
                return
        elif _is_tracked(rel, tracked):
            return
        found.append((path, reason))

    for name in OBSOLETE_DIRS:
        p = root / name
        if p.is_dir():
            add(p, "director obsolet")
    for name in OBSOLETE_FILES:
        p = root / name
        if p.is_file():
            add(p, "fisier obsolet", needs_git=True)
    for pattern in OBSOLETE_GLOBS:
        for p in sorted(root.glob(pattern)):
            if p.is_file():
                add(p, "backup vechi", needs_git=True)
    for rel in DELETED_MODULES:
        p = root / rel
        if p.is_file():
            add(p, "modul sters din repo", needs_git=True)

    for dirpath, dirs, _files in os.walk(root):
        here = Path(dirpath)
        keep = []
        for d in dirs:
            if d in _NO_DESCEND or _is_venv(here / d) or (here / d / ".git").exists():
                continue
            if d == "__pycache__":
                add(here / d, "bytecode Python")
            else:
                keep.append(d)
        dirs[:] = keep

    try:
        separate_runtime = runtime_root.resolve() != root
    except OSError:
        separate_runtime = False
    if separate_runtime:
        for name in LEGACY_ROOT_LOGS:
            p = root / name
            if p.is_file():
                add(p, "log vechi (runtime mutat)")

    # Cache WF legacy din bench_results deja prezent la destinatia runtime:
    # citirile se fac numai din destinatie, copia veche e inaccesibila.
    try:
        from loto_enterprise.core.walk_forward_adapter import (
            CACHE_DIR as wf_dir,
            LEGACY_CACHE_DIR as legacy_dir,
        )

        if legacy_dir.resolve() != Path(wf_dir).resolve():
            for p in sorted(legacy_dir.glob("walk_forward_*.pkl")):
                if (Path(wf_dir) / p.name).exists():
                    add(p, "cache WF legacy deja migrat")
    except Exception as exc:  # noqa: BLE001 - raportat, nu fatal
        print(f"  [WARN] verificare cache WF legacy esuata: {exc}")

    # Deduplicare (un __pycache__ poate fi si altfel prins) si fara copii
    # ale unui director deja ales.
    unique: list[tuple[Path, str]] = []
    seen: set[Path] = set()
    for p, reason in found:
        if p in seen or any(parent in seen for parent in p.parents):
            continue
        seen.add(p)
        unique.append((p, reason))
    return unique


def _delete(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def run(
    root: Path,
    runtime_root: Path,
    dry_run: bool = False,
    tracked: set[str] | None | bool = True,
    purge_bench: bool = True,
) -> dict:
    """tracked=True -> citeste git ls-files; altfel foloseste valoarea data."""
    if tracked is True:
        tracked = _tracked_files(root)
    if tracked is None:
        print("  [WARN] git indisponibil: sar fisierele care ar putea fi urmarite.")
    result = {"deleted": [], "errors": [], "bytes": 0, "dry_run": dry_run}
    for path, reason in collect_candidates(root, runtime_root, tracked):
        size = _size(path)
        verb = "Ar sterge" if dry_run else "Sters"
        if not dry_run:
            try:
                _delete(path)
            except OSError as exc:
                result["errors"].append(f"{path}: {exc}")
                print(f"  [WARN] Nu pot sterge {path}: {exc}")
                continue
        result["deleted"].append(str(path))
        result["bytes"] += size
        print(f"  {verb}: {path} ({reason}, {size / 1048576:.2f} MB)")

    if purge_bench:
        try:
            from loto_enterprise.benchmark.bench_cache import purge_stale_fold_cache

            r = purge_stale_fold_cache(dry_run=dry_run)
            n = r.get("stale", 0) if dry_run else r.get("deleted", 0)
            mb = float(r.get("stale_mb", 0) or 0)
            print(
                f"  Cache benchmark: {n} folduri de alte versiuni decat "
                f"{r.get('version')} ({mb:.1f} MB)"
            )
            result["bytes"] += int(mb * 1048576)
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"bench_cache: {exc}")
            print(f"  [WARN] Curatare cache benchmark esuata: {exc}")

    print(
        f"  Total {'de eliberat' if dry_run else 'eliberat'}: "
        f"{result['bytes'] / 1048576:.1f} MB, "
        f"{len(result['deleted'])} intrari, {len(result['errors'])} erori"
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        root = Path(__file__).resolve().parent
        sys.path.insert(0, str(root))
        from runtime_paths import RUNTIME_ROOT

        run(root, RUNTIME_ROOT, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001 - nu opreste niciodata .bat-ul
        print(f"  [WARN] Curatare reziduale esuata: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

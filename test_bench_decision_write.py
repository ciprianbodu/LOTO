"""Decizia scrisă de `bench_all_methods.py` (audit 2026-10-08).

- O singură scriere: câștigătorii, matricea Auto-Pilot și semnăturile ajung pe
  disc împreună. Înainte, fișierul se scria întâi fără `auto_pilot_per_pool`
  (producția juca vechii câștigători după avg_hits), cu semnăturile ștampilate
  înaintea matricei.
- Matricea eșuată lasă decizia anterioară neatinsă, fără semnături noi.
- Fără LOTO_BENCH_TARGET, ținta vine din setările UI-ului (.ui_state.json),
  nu din implicitul 3.

Bench-ul rulează într-un proces separat (pool-ul lui de procese nu rămâne
copil al pytest-ului), pe o țară străină și căi temporare, ca testele de țară:
fișierele românești nu se ating.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

import bench_all_methods as bam
from loto_enterprise.benchmark import decision
from loto_enterprise.core.lotteries import PROJECT_ROOT, require_lottery

ROOT = Path(__file__).resolve().parent
RO_FILES = [
    ROOT / "bench_results" / "folds.csv",
    ROOT / "bench_results" / "report.json",
    ROOT / "best_methods.json",
]

# Rulează `main()` după `@PATCH@` (cod Python), cu argumentele date.
_RUNNER = """
import json, sys
from pathlib import Path
sys.path.insert(0, @ROOT@)
import bench_all_methods as bam
import ui_shared
from loto_enterprise.benchmark import decision
DEC = Path(@DEC@)
bam.UI_STATE_FILE = Path(@UI_STATE@)
@PATCH@
sys.argv = @ARGV@
sys.exit(bam.main())
"""


def _digest(paths):
    return {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
        for p in paths
    }


@pytest.fixture
def mini_bench(tmp_path):
    """`bench_all_methods.main()` pe 160 de extrageri Germania, random + frequency."""
    src = PROJECT_ROOT / require_lottery("de_lotto").csv
    ist = tmp_path / "ist"
    ist.mkdir()
    pd.read_csv(src, dtype=str).tail(160).to_csv(ist / src.name, index=False)
    dec = tmp_path / "dec" / "best_methods.json"
    argv = [
        "bench_all_methods.py",
        "--country", "DE",
        "--istoric", str(ist),
        "--methods", "random,frequency",
        "--percentiles", "10,50,100",
        "--force-decision",
        "--no-shuffled-control",
        "--no-rich",
        "--no-cache",
        "--out", str(tmp_path / "out"),
        "--decision", str(dec),
    ]
    before = _digest(RO_FILES)

    def run(patch="", *, target="3", ui_state=None):
        env = dict(os.environ)
        env["LOTO_RUNTIME_DIR"] = str(tmp_path / "rt")
        env["LOTO_BENCH_CACHE_DIR"] = str(tmp_path / "bc")
        env["LOTO_POOL_HISTORY_FILE"] = str(tmp_path / "ph.json")
        env.pop("LOTO_BENCH_TARGET", None)
        if target is not None:
            env["LOTO_BENCH_TARGET"] = target
        source = (
            _RUNNER.replace("@PATCH@", patch)
            .replace("@ROOT@", repr(str(ROOT)))
            .replace("@DEC@", repr(str(dec)))
            .replace("@UI_STATE@", repr(str(ui_state or tmp_path / "absent.json")))
            .replace("@ARGV@", repr(argv))
        )
        return subprocess.run(
            [sys.executable, "-c", source],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=600,
        )

    yield run, dec
    assert _digest(RO_FILES) == before


def test_bench_writes_the_decision_once_with_matrix_and_signatures(
    mini_bench, tmp_path
):
    run, dec = mini_bench
    log = tmp_path / "writes.jsonl"
    proc = run(
        f"""
_real = ui_shared.atomic_write_json
def _recording(path, obj, **kw):
    if Path(path) == DEC:
        with open({str(log)!r}, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj) + "\\n")
    return _real(path, obj, **kw)
ui_shared.atomic_write_json = _recording
"""
    )
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    written = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    # Nicio stare intermediară fără matrice: o generare în fereastra aceea
    # juca vechii câștigători după avg_hits, fără poarta față de random.
    assert len(written) == 1
    cfg = written[0]
    assert cfg["games"]["de_lotto"]["auto_pilot_per_pool"]
    assert cfg["_meta"]["csv_signatures"]["de_lotto"]["rows"] == 160
    assert cfg["_meta"]["engine_signature"]
    assert cfg["_meta"]["bench_hit_target"] == 3
    assert json.loads(dec.read_text(encoding="utf-8")) == cfg


def test_failed_matrix_keeps_the_previous_decision_and_signatures(mini_bench):
    run, dec = mini_bench
    dec.parent.mkdir(parents=True)
    dec.write_text(
        json.dumps({"_meta": {"country": "DE", "previous": True}, "games": {}}),
        encoding="utf-8",
    )
    before = dec.read_bytes()
    proc = run(
        """
def _broken(*_a, **_kw):
    raise RuntimeError("simulated matrix failure")
decision.build_auto_pilot_matrix = _broken
"""
    )
    # Fără fișier fără matrice și fără semnături care l-ar declara „la zi”.
    assert dec.read_bytes() == before
    # Codul de ieșire spune că decizia nu s-a scris.
    assert proc.returncode == 1, proc.stdout[-3000:] + proc.stderr[-3000:]
    assert "NU a fost rescris" in proc.stdout


def test_console_bench_uses_the_target_saved_by_the_ui(mini_bench, tmp_path):
    run, dec = mini_bench
    state = tmp_path / "ui_state.json"
    state.write_text(json.dumps({"bench_hit_target": 4}), encoding="utf-8")
    # Ca din ACTUALIZARI.bat / verifica_mediu.py: fără LOTO_BENCH_TARGET.
    proc = run(target=None, ui_state=state)
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    cfg = json.loads(dec.read_text(encoding="utf-8"))
    cells = [
        c
        for c in cfg["games"]["de_lotto"]["auto_pilot_per_pool"].values()
        if "hit_target" in c
    ]
    assert cells and {c["hit_target"] for c in cells} == {4}
    assert cfg["_meta"]["bench_hit_target"] == 4
    assert "Ținta deciziei: 4+" in proc.stdout


def test_resolve_bench_target_order(tmp_path):
    state = tmp_path / "ui_state.json"
    state.write_text(json.dumps({"bench_hit_target": 4}), encoding="utf-8")
    assert bam.resolve_bench_target({"LOTO_BENCH_TARGET": "3"}, state) == (
        3,
        "LOTO_BENCH_TARGET",
    )
    assert bam.resolve_bench_target({}, state) == (4, "ui_state.json")
    assert bam.resolve_bench_target({"LOTO_BENCH_TARGET": " "}, state)[0] == 4
    assert bam.resolve_bench_target({}, tmp_path / "absent.json") == (3, "implicit")
    state.write_text("{corupt", encoding="utf-8")
    assert bam.resolve_bench_target({}, state) == (3, "implicit")
    state.write_text(json.dumps({"bench_hit_target": 7}), encoding="utf-8")
    assert bam.resolve_bench_target({}, state)[0] == 3  # clamp 3/4


def test_decision_target_mismatch_follows_the_effective_game_target():
    cell = lambda t: {"scorer": "frequency", "hit_target": t}  # noqa: E731
    cfg = {
        "_meta": {"bench_hit_target": 3},
        "games": {
            "loto_6_49": {"draw_n": 6, "auto_pilot_per_pool": {"k6": cell(3)}},
            "loto_5_40": {"draw_n": 6, "pick_n": 5, "auto_pilot_per_pool": {"k5": cell(4)}},
            "joker_urna1": {"draw_n": 5, "auto_pilot_per_pool": {"k5": {"error": "x"}}},
            "joker_urna2": {"draw_n": 1, "auto_pilot_per_pool": {"k1": cell(1)}},
        },
    }
    # 5/40 rămâne 4+ cu orice selector; Urna 2 e top-1; celula fără țintă
    # (Joker Urna 1) ia ținta din ștampila `_meta.bench_hit_target`.
    assert decision.decision_target_mismatch(cfg, 3) == {}
    assert decision.decision_target_mismatch(cfg, 4) == {"loto_6_49": 3, "joker_urna1": 3}
    del cfg["_meta"]
    assert decision.decision_target_mismatch(cfg, 4) == {"loto_6_49": 3}
    assert decision.decision_target_mismatch({"games": "corupt"}, 4) == {}

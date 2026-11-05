"""Manifestul designurilor (covering_designs/MANIFEST.json) trebuie să fie
complet și proaspăt: fiecare fișier .txt din covering_designs/ e listat, fiecare
intrare existentă are sha256-ul curent al fișierului, iar coverele complete
(C/L/R/RL) au acoperirea 1.0 în manifest.

Regenerare după orice schimbare intenționată a unui design:
    .venv/Scripts/python.exe scripts/analysis/gen_design_manifest.py
Validarea la 100% a fiecărui design e separată (test_covering_designs.py,
test_lotto_design_and_penalty.py, auditul exhaustiv din scripts/analysis/).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

DESIGNS = Path("covering_designs")
MANIFEST = DESIGNS / "MANIFEST.json"


def _load() -> dict:
    assert MANIFEST.exists(), (
        "MANIFEST.json lipsește — rulează scripts/analysis/gen_design_manifest.py"
    )
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_manifest_lists_every_design_file_and_no_ghost():
    m = _load()
    listed = {e["file"] for e in m["entries"]}
    on_disk = {
        p.relative_to(DESIGNS).as_posix()
        for p in DESIGNS.rglob("*.txt")
        if p.name != "MANIFEST.json"
    }
    assert listed == on_disk, (
        f"manifest vs disc: lipsesc din manifest {sorted(on_disk - listed)[:5]}, "
        f"fantome în manifest {sorted(listed - on_disk)[:5]}"
    )
    assert m["total_files"] == len(m["entries"]) == len(on_disk)


def test_manifest_hashes_are_fresh():
    m = _load()
    stale = []
    for e in m["entries"]:
        p = DESIGNS / e["file"]
        actual = hashlib.sha256(p.read_bytes()).hexdigest()
        if actual != e["sha256"]:
            stale.append(e["file"])
    assert not stale, (
        f"hash-uri învechite în MANIFEST.json (rulează gen_design_manifest.py): {stale[:5]}"
    )


def test_manifest_counts_and_full_cover_coverage():
    m = _load()
    counts = m["counts"]
    # 55 covere clasice (C_*) + 99 lotto (L_*) la nivelul rădăcinii
    assert counts.get("C", 0) >= 55
    assert counts.get("L", 0) >= 99
    full = [e for e in m["entries"] if e["kind"] in ("C", "L", "R", "RL")]
    assert full, "niciun cover complet în manifest"
    bad = [e["file"] for e in full if e.get("coverage") != 1.0]
    assert not bad, f"covere complete cu acoperire != 1.0 în manifest: {bad[:5]}"
    # fiecare intrare are geometria și numărul de bilete
    for e in m["entries"]:
        assert e.get("kind") in ("C", "L", "H", "R", "RL", "X", "LR"), e
        assert isinstance(e.get("tickets"), int) and e["tickets"] > 0, e["file"]
        if e["kind"] in ("C", "L", "R", "RL"):
            assert e.get("v") and e.get("pick") and e.get("guarantee"), e["file"]

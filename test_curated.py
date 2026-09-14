"""Teste pentru loto_enterprise/benchmark/curated.py — verificare globala.

apply_curation() calcula `missing_required` (random/frequency lipsa din
`active`) doar pentru log, fara sa reinjecteze metodele in `kept` — un
`curated_methods.json` editat manual fara "random"/"frequency" excludea
tacut ambele din Re-Bench, contrazicand regula de aur CLAUDE.md §4.3
("trebuie sa ramana in lista activa")."""
from __future__ import annotations

import json

from loto_enterprise.benchmark import curated as cur


def test_apply_curation_reinjects_required_methods_when_omitted(tmp_path, monkeypatch):
    path = tmp_path / "curated_methods.json"
    path.write_text(json.dumps({"active": ["autocorr", "pair_affinity"]}), encoding="utf-8")
    monkeypatch.setattr(cur, "_PATH", path)

    candidates = ["autocorr", "pair_affinity", "random", "frequency", "gap_poisson"]
    kept, info = cur.apply_curation(candidates)

    assert "random" in kept
    assert "frequency" in kept
    assert info["missing_required"] == []


def test_apply_curation_reports_missing_required_when_not_a_candidate_at_all(tmp_path, monkeypatch):
    """Daca metoda cerinta nici macar nu e in candidati (ex. blacklistata),
    nu poate fi reinjectata -- ramane raportata, nu inventata din nimic."""
    path = tmp_path / "curated_methods.json"
    path.write_text(json.dumps({"active": ["autocorr"]}), encoding="utf-8")
    monkeypatch.setattr(cur, "_PATH", path)

    candidates = ["autocorr"]  # nici random, nici frequency nu sunt candidate
    kept, info = cur.apply_curation(candidates)

    assert "random" not in kept
    assert "frequency" not in kept
    assert set(info["missing_required"]) == {"random", "frequency"}


def test_apply_curation_keeps_required_methods_already_present(tmp_path, monkeypatch):
    path = tmp_path / "curated_methods.json"
    path.write_text(
        json.dumps({"active": ["random", "frequency", "autocorr"]}), encoding="utf-8"
    )
    monkeypatch.setattr(cur, "_PATH", path)

    candidates = ["random", "frequency", "autocorr"]
    kept, info = cur.apply_curation(candidates)

    assert kept == ["random", "frequency", "autocorr"]
    assert info["missing_required"] == []

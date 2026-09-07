"""Teste pentru search_649_methods.py — scrie direct in best_methods.json si
methods_top649.py (fisiere citite de aplicatia reala), fara sa faca parte din
pipeline-ul automat (verificare globala 2026-09-07)."""
from __future__ import annotations

import json

import search_649_methods as sm


def _result(method: str, rate: float = 0.15, lift: float = 0.25) -> sm.SearchResult:
    return sm.SearchResult(method=method, rate_4plus_k16=rate, lift_vs_baseline=lift, failed=False)


def test_top649_alias_matches_between_generate_and_patch():
    """Alias-ul calculat de _top649_alias trebuie sa fie IDENTIC cu ce
    genereaza generate_methods_top649 pentru aceeasi pozitie — altfel
    "scorer"-ul scris in best_methods.json nu se rezolva in METHODS."""
    top = [_result("649_blend_katz_gap_x"), _result("autocorr", rate=0.14), _result("dmd", rate=0.13)]
    assert sm._top649_alias(top[0].method, 0) == "top649_01_649_blend_katz_gap_x"
    assert sm._top649_alias(top[1].method, 1) == "top649_02_autocorr"


def test_patch_best_methods_writes_single_member_ensemble_with_resolvable_alias(tmp_path, monkeypatch):
    """ENSEMBLE_MAX_METHODS=1: un singur membru, nu top-3. avg_hits=0.0, nu None
    (ar arunca TypeError la citire in method_selector). scorer/ensemble folosesc
    ALIASUL, nu numele brut al candidatului."""
    bm_path = tmp_path / "best_methods.json"
    bm_path.write_text(json.dumps({
        "games": {"loto_6_49": {"auto_pilot_per_pool": {"k11": {"scorer": "existing", "avg_hits": 1.0}}}},
    }), encoding="utf-8")
    monkeypatch.setattr(sm, "ROOT", tmp_path)

    top = [_result("649_blend_new_candidate", rate=0.20, lift=0.30),
           _result("autocorr", rate=0.18), _result("dmd", rate=0.17)]
    sm.patch_best_methods(top, baseline=0.10)

    cfg = json.loads(bm_path.read_text(encoding="utf-8"))
    ap = cfg["games"]["loto_6_49"]["auto_pilot_per_pool"]
    # k11 (alt pool) neatins
    assert ap["k11"]["scorer"] == "existing"
    k16 = ap["k16"]
    expected_alias = sm._top649_alias("649_blend_new_candidate", 0)
    assert k16["scorer"] == expected_alias
    assert k16["ensemble"] == [{"method": expected_alias, "weight": 1.0}]
    assert k16["avg_hits"] == 0.0
    assert isinstance(k16["avg_hits"], float)  # nu None


def test_patch_best_methods_missing_file_is_noop(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(sm, "ROOT", tmp_path)
    sm.patch_best_methods([_result("x")], baseline=0.1)
    assert not (tmp_path / "best_methods.json").exists()


def test_generate_methods_top649_uses_same_alias_scheme(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "ROOT", tmp_path)
    (tmp_path / "loto_enterprise" / "benchmark").mkdir(parents=True)
    top = [_result("649_blend_new_candidate", rate=0.20)]
    sm.generate_methods_top649(top)
    text = (tmp_path / "loto_enterprise" / "benchmark" / "methods_top649.py").read_text(encoding="utf-8")
    assert '"top649_01_649_blend_new_candidate": METHODS["649_blend_new_candidate"]' in text

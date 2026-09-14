"""Teste pentru scan_all_methods_hits.py — _print_top() calcula lift-ul fata
de o realizare empirica `random`, nu fata de baseline-ul teoretic."""

from __future__ import annotations

from types import SimpleNamespace

import scan_all_methods_hits as sah


def _game(key="loto_6_49", max_num=49, draw_n=6):
    return SimpleNamespace(key=key, max_num=max_num, draw_n=draw_n)


def test_print_top_accepts_gamedef_and_filters_by_key(capsys):
    game = _game()
    rows = [
        {
            "method": "a",
            "game": "loto_6_49",
            "failed": False,
            "rate_4plus_k16": 0.02,
            "rate_3plus_k16": 0.10,
        },
        {
            "method": "b",
            "game": "loto_5_40",
            "failed": False,
            "rate_4plus_k16": 0.99,
            "rate_3plus_k16": 0.99,
        },
        {
            "method": "c",
            "game": "loto_6_49",
            "failed": True,
            "rate_4plus_k16": 0.50,
            "rate_3plus_k16": 0.50,
        },
    ]
    sah._print_top(rows, game, 16, "rate_4plus", n=5)
    out = capsys.readouterr().out
    assert "loto_6_49" in out
    assert f"{'a':32s}" in out
    # metoda din alt joc si cea failed nu apar in coloana de metoda a topului
    assert f"{'b':32s}" not in out
    assert f"{'c':32s}" not in out


def test_print_top_lift_uses_theoretical_baseline_not_empirical_random(
    capsys, monkeypatch
):
    game = _game()
    captured = {}

    def fake_expected_random_rate(max_num, draw_n, pool, target):
        captured["args"] = (max_num, draw_n, pool, target)
        return 0.10

    monkeypatch.setattr(
        "loto_enterprise.benchmark.decision.expected_random_rate",
        fake_expected_random_rate,
    )
    rows = [
        {
            "method": "random",
            "game": "loto_6_49",
            "failed": False,
            "rate_4plus_k16": 0.30,
            "rate_3plus_k16": 0.30,
        },
        {
            "method": "m1",
            "game": "loto_6_49",
            "failed": False,
            "rate_4plus_k16": 0.20,
            "rate_3plus_k16": 0.20,
        },
    ]
    sah._print_top(rows, game, 16, "rate_4plus", n=5)

    assert captured["args"] == (49, 6, 16, 4)
    out = capsys.readouterr().out
    # lift asteptat pentru m1 fata de baseline teoretic 0.10: (0.20-0.10)/0.10 = +100.0%
    assert "+100.0%" in out
    assert "vs random teoretic" in out


def test_print_top_handles_missing_baseline_gracefully(capsys, monkeypatch):
    game = _game()
    monkeypatch.setattr(
        "loto_enterprise.benchmark.decision.expected_random_rate",
        lambda *a, **k: 0.0,
    )
    rows = [
        {
            "method": "m1",
            "game": "loto_6_49",
            "failed": False,
            "rate_4plus_k16": 0.20,
            "rate_3plus_k16": 0.20,
        }
    ]
    sah._print_top(rows, game, 16, "rate_4plus", n=5)
    out = capsys.readouterr().out
    assert "vs random teoretic" not in out

"""Teste pentru update_csv.py (verificare globala 2026-09-07) — rulat pe FIECARE
pornire a aplicatiei (ACTUALIZARI.bat + START_8000.bat), scrie direct in
_ISTORIC/, sursa de adevar consumata de engine/benchmark/walk-forward."""

from __future__ import annotations

from datetime import date

import update_csv as uc


def _write_csv(path, rows):
    lines = ["date,n1,n2,n3,n4,n5,n6"]
    for r in rows:
        lines.append(",".join([r] + [str(n) for n in range(1, 7)]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# _last_date_in_csv — trebuie sa fie MAXIMUL, nu data ultimului rand citit
# --------------------------------------------------------------------------- #
def test_last_date_is_the_maximum_not_the_last_row(tmp_path):
    """Fisier NEordonat (corectie manuala, imbinare) — ultimul rand citit NU e
    cea mai recenta data. Cu bug-ul vechi, aceasta ar fi intors 01-01-2020."""
    csv_path = tmp_path / "loto_6_49.csv"
    _write_csv(csv_path, ["15-06-2026", "01-01-2020", "10-06-2026"])
    assert uc._last_date_in_csv(csv_path) == date(2026, 6, 15)


def test_last_date_ordered_file_still_correct(tmp_path):
    csv_path = tmp_path / "loto_6_49.csv"
    _write_csv(csv_path, ["01-01-2026", "02-01-2026", "03-01-2026"])
    assert uc._last_date_in_csv(csv_path) == date(2026, 1, 3)


def test_last_date_missing_file_returns_none(tmp_path):
    assert uc._last_date_in_csv(tmp_path / "does_not_exist.csv") is None


def test_last_date_existing_but_empty_returns_none(tmp_path):
    csv_path = tmp_path / "loto_6_49.csv"
    csv_path.write_text("date,n1,n2,n3,n4,n5,n6\n", encoding="utf-8")
    assert uc._last_date_in_csv(csv_path) is None


# --------------------------------------------------------------------------- #
# _append_rows_atomic — nu mai duplica un rand a carui data e deja in CSV
# --------------------------------------------------------------------------- #
def test_append_skips_rows_whose_date_already_exists(tmp_path):
    csv_path = tmp_path / "loto_6_49.csv"
    _write_csv(csv_path, ["10-06-2026"])
    new_rows = [
        {"date": date(2026, 6, 10), "main": [1, 2, 3, 4, 5, 6]},  # deja in CSV
        {"date": date(2026, 6, 17), "main": [7, 8, 9, 10, 11, 12]},  # chiar noua
    ]
    written = uc._append_rows_atomic(csv_path, new_rows, has_joker=False, num_main=6)
    assert written == 1
    text = csv_path.read_text(encoding="utf-8")
    assert text.count("10-06-2026") == 1  # neduplicat
    assert "17-06-2026" in text


def test_append_all_new_returns_full_count(tmp_path):
    csv_path = tmp_path / "loto_6_49.csv"
    new_rows = [{"date": date(2026, 1, 1), "main": [1, 2, 3, 4, 5, 6]}]
    written = uc._append_rows_atomic(csv_path, new_rows, has_joker=False, num_main=6)
    assert written == 1
    assert csv_path.exists()


def test_append_nothing_to_add_does_not_touch_file(tmp_path):
    csv_path = tmp_path / "loto_6_49.csv"
    _write_csv(csv_path, ["10-06-2026"])
    before = csv_path.read_text(encoding="utf-8")
    new_rows = [{"date": date(2026, 6, 10), "main": [1, 2, 3, 4, 5, 6]}]  # deja prezent
    written = uc._append_rows_atomic(csv_path, new_rows, has_joker=False, num_main=6)
    assert written == 0
    assert csv_path.read_text(encoding="utf-8") == before


# --------------------------------------------------------------------------- #
# _extract_draws — respinge extrageri cu numere duplicate INTR-O SINGURA extragere
# --------------------------------------------------------------------------- #
def test_extract_draws_rejects_duplicate_numbers_within_one_draw():
    # a doua extragere are 7 aparand de doua ori (fragment HTML deformat)
    text = "2026-06-10 1 2 3 4 5 6\n2026-06-17 7 7 9 10 11 12"
    draws = uc._extract_draws(text, num_main=6, has_joker=False, after=None, max_num=49)
    assert len(draws) == 1
    assert draws[0]["date"] == date(2026, 6, 10)


def test_extract_draws_accepts_valid_distinct_draws():
    text = "2026-06-10 1 2 3 4 5 6\n2026-06-17 7 8 9 10 11 12"
    draws = uc._extract_draws(text, num_main=6, has_joker=False, after=None, max_num=49)
    assert len(draws) == 2
    assert {d["date"] for d in draws} == {date(2026, 6, 10), date(2026, 6, 17)}


def test_extract_draws_rejects_number_out_of_range(monkeypatch):
    """Fragment HTML deformat (rand vecin, separator lipsa) poate produce un
    numar valid ca sir de cifre dar imposibil pentru geometria jocului (ex.
    62 pe 6/49, max_num=49) — inainte doar duplicatele erau respinse, nu si
    intervalul, iar randul ajungea in _ISTORIC/ ca sa fie respins tacut mai
    tarziu, la citire prin draw_validation.py."""
    text = "2026-06-10 1 2 3 4 5 62\n2026-06-17 7 8 9 10 11 12"
    draws = uc._extract_draws(text, num_main=6, has_joker=False, after=None, max_num=49)
    assert len(draws) == 1
    assert draws[0]["date"] == date(2026, 6, 17)


def test_extract_draws_rejects_joker_number_out_of_range():
    text = "2026-06-10 1 2 3 4 5 + 25\n2026-06-17 6 7 8 9 10 + 15"
    draws = uc._extract_draws(
        text, num_main=5, has_joker=True, after=None, max_num=45, joker_max=20
    )
    assert len(draws) == 1
    assert draws[0]["date"] == date(2026, 6, 17)
    assert draws[0]["joker"] == 15


# --------------------------------------------------------------------------- #
# update_all — CSV existent dar TRUNCHIAT (fara nicio data valida) e sarit,
# nu tratat ca "prima rulare" (care ar rescrie cu doar cateva luni de istoric).
# --------------------------------------------------------------------------- #
def test_update_all_skips_truncated_csv_instead_of_treating_as_fresh_start(
    tmp_path, monkeypatch, capsys
):
    istoric = tmp_path / "_ISTORIC"
    istoric.mkdir()
    # Fisier EXISTENT dar fara niciun rand cu data valida - trunchiat/corupt.
    (istoric / "loto_6_49.csv").write_text("date,n1,n2,n3,n4,n5,n6\n", encoding="utf-8")
    (istoric / "joker.csv").write_text("date,n1,n2,n3,n4,n5,joker\n", encoding="utf-8")
    (istoric / "loto_5_40.csv").write_text("date,n1,n2,n3,n4,n5,n6\n", encoding="utf-8")

    monkeypatch.setattr(uc, "_find_istoric_dir", lambda: istoric)
    calls = []

    def _fake_get_page_text(url):
        calls.append(url)
        return "2026-01-01 1 2 3 4 5 6"

    monkeypatch.setattr(uc, "_get_page_text", _fake_get_page_text)
    total = uc.update_all()

    assert total == 0
    assert (
        calls == []
    )  # niciun fetch — jocurile trunchiate sunt sarite INAINTE de fetch
    out = capsys.readouterr().out
    assert "trunchiat" in out.lower() or "corupt" in out.lower()
    # Fisierul ramane exact cum era - nerescris cu "totul e nou".
    assert (istoric / "loto_6_49.csv").read_text(
        encoding="utf-8"
    ) == "date,n1,n2,n3,n4,n5,n6\n"


def test_update_all_bootstraps_normally_when_file_genuinely_missing(
    tmp_path, monkeypatch
):
    """Fisierul LIPSA (nu doar gol) e in continuare tratat ca prima rulare —
    garda vizeaza doar cazul EXISTENT-dar-corupt, nu bootstrap-ul legitim."""
    istoric = tmp_path / "_ISTORIC"
    istoric.mkdir()
    monkeypatch.setattr(uc, "_find_istoric_dir", lambda: istoric)
    monkeypatch.setattr(uc, "_get_page_text", lambda url: "2026-01-01 1 2 3 4 5 6")
    total = uc.update_all()
    assert total == 3  # cate un rand nou pentru fiecare din cele 3 jocuri
    assert (istoric / "loto_6_49.csv").exists()

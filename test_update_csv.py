"""Teste pentru update_csv.py (verificare globala 2026-09-07) — rulat pe FIECARE
pornire a aplicatiei (ACTUALIZARI.bat + START_8000.bat), scrie direct in
_ISTORIC/, sursa de adevar consumata de engine/benchmark/walk-forward."""

from __future__ import annotations

from datetime import date

import pytest

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
# _append_rows_atomic — nu duplica un rând identic (dată + numere);
# o a doua extragere în aceeași zi, cu numere diferite, se păstrează.
# --------------------------------------------------------------------------- #
def test_append_skips_exact_duplicate_rows(tmp_path):
    csv_path = tmp_path / "loto_6_49.csv"
    _write_csv(csv_path, ["10-06-2026"])
    new_rows = [
        {"date": date(2026, 6, 10), "main": [1, 2, 3, 4, 5, 6]},  # identic
        {"date": date(2026, 6, 17), "main": [7, 8, 9, 10, 11, 12]},  # nou
    ]
    written = uc._append_rows_atomic(csv_path, new_rows, has_joker=False, num_main=6)
    assert written == 1
    text = csv_path.read_text(encoding="utf-8")
    assert text.count("10-06-2026") == 1  # neduplicat
    assert "17-06-2026" in text
    assert b"\r" not in csv_path.read_bytes()  # LF, ca istoricele externe


def test_append_keeps_same_day_extra_with_different_numbers(tmp_path):
    csv_path = tmp_path / "loto_6_49.csv"
    _write_csv(csv_path, ["10-06-2026"])  # 1,2,3,4,5,6
    new_rows = [
        {"date": date(2026, 6, 10), "main": [7, 8, 9, 10, 11, 12]},
    ]
    written = uc._append_rows_atomic(csv_path, new_rows, has_joker=False, num_main=6)
    assert written == 1
    text = csv_path.read_text(encoding="utf-8")
    assert text.count("10-06-2026") == 2
    assert "7,8,9,10,11,12" in text


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


# --------------------------------------------------------------------------- #
# update_all — verificarea paginii înainte de scriere (audit 2026-10-08).
# Fiecare caz scria altfel un istoric greșit, publicat apoi de PushHistory.
# --------------------------------------------------------------------------- #
HEADERS = {
    "loto_6_49": "date,n1,n2,n3,n4,n5,n6\n",
    "joker": "date,n1,n2,n3,n4,n5,joker\n",
    "loto_5_40": "date,n1,n2,n3,n4,n5,n6\n",
}


def _run(tmp_path, monkeypatch, rows: dict, pages: dict):
    """CSV-uri cu `rows` per joc (celelalte doar antet: sărite fără fetch) și
    paginile `pages` per joc; întoarce (adăugate, ieșire, conținut înainte)."""
    istoric = tmp_path / "_ISTORIC"
    istoric.mkdir()
    for key, cfg in uc.GAME_CONFIGS.items():
        text = HEADERS[key] + "".join(r + "\n" for r in rows.get(key, ()))
        (istoric / cfg["csv_name"]).write_text(text, encoding="utf-8")
    before = {p.name: p.read_bytes() for p in istoric.iterdir()}
    url_to_game = {cfg["recent_url"]: key for key, cfg in uc.GAME_CONFIGS.items()}
    monkeypatch.setattr(uc, "_find_istoric_dir", lambda: istoric)
    monkeypatch.setattr(
        uc, "_get_page_text", lambda url: pages.get(url_to_game[url], "")
    )
    added = uc.update_all()
    return added, before, istoric


def _unchanged(istoric, before):
    return {p.name: p.read_bytes() for p in istoric.iterdir()} == before


@pytest.mark.parametrize(
    "key, rows, page, day",
    [
        # Site-ul corectează rândul ultimei zile stocate (40 -> 41): nu e a doua
        # extragere a zilei.
        (
            "loto_6_49",
            ["01-10-2026,3,19,49,48,2,8", "04-10-2026,24,10,12,15,32,40"],
            "2026-10-01 3 19 49 48 2 8 2026-10-04 24 10 12 15 32 41",
            "04-10-2026",
        ),
        # Numai numărul Joker corectat.
        (
            "joker",
            ["01-10-2026,1,2,3,4,5,7", "04-10-2026,6,7,8,9,10,9"],
            "2026-10-01 1 2 3 4 5 + 7 2026-10-04 6 7 8 9 10 + 19",
            "04-10-2026",
        ),
        # O zi mai veche din fereastră, corectată pe site, lângă o extragere nouă.
        (
            "loto_5_40",
            ["27-09-2026,1,2,3,4,5,6", "01-10-2026,7,8,9,10,11,12"],
            "2026-09-27 1 2 3 4 5 7 2026-10-01 7 8 9 10 11 12 2026-10-04 13 14 15 16 17 18",
            "27-09-2026",
        ),
    ],
)
def test_update_all_refuses_when_the_site_changed_a_stored_draw(
    tmp_path, monkeypatch, capsys, key, rows, page, day
):
    added, before, istoric = _run(tmp_path, monkeypatch, {key: rows}, {key: page})
    out = capsys.readouterr().out
    assert added == 0, out
    assert _unchanged(istoric, before)
    assert f"EROARE verificare: extragerile din {day} diferă" in out
    assert f"NEACTUALIZATE: {uc.GAME_CONFIGS[key]['display_name']}" in out


def test_update_all_ignores_site_differences_older_than_the_window(
    tmp_path, monkeypatch, capsys
):
    """Pagina 5/40 merge până în 1995 și păstrează rânduri greșite vechi
    (24-10-2024, corectat local): ele nu blochează update-ul."""
    rows = [
        "24-10-2024,14,15,28,10,25,26",
        "27-10-2024,13,34,11,16,10,39",
        "01-10-2026,1,2,3,4,5,6",
    ]
    page = (
        "2024-10-24 13 34 11 16 10 39 2024-10-27 13 34 11 16 10 39 "
        "2026-10-01 1 2 3 4 5 6 2026-10-04 7 8 9 10 11 12"
    )
    added, _, istoric = _run(
        tmp_path, monkeypatch, {"loto_5_40": rows}, {"loto_5_40": page}
    )
    assert added == 1, capsys.readouterr().out
    lines = (istoric / "loto_5_40.csv").read_text(encoding="utf-8").splitlines()
    assert lines[-1] == "04-10-2026,7,8,9,10,11,12"


@pytest.mark.parametrize(
    "bad, reason",
    [
        ("2026-10-01 3 19 49 49 2 8", "numere repetate"),  # 48 scris 49
        ("2026-10-01 3 19 49 48 2 50", "număr în afara 1..49"),
        ("2062-10-01 3 19 49 48 2 8", "dată în viitor"),  # anul greșit pe site
        # Rânduri pe care modelul nu le mai potrivea deloc: o cifră în plus și o
        # celulă lipsă (care împrumuta „20” din anul datei următoare).
        ("2026-10-01 3 19 490 48 2 8", "rând necitibil"),
        ("2026-10-01 3 19 48 2 8", "rând necitibil"),
    ],
)
def test_update_all_refuses_an_invalid_site_row_after_the_last_stored_draw(
    tmp_path, monkeypatch, capsys, bad, reason
):
    """Rândul invalid era sărit tăcut, iar extragerea de după el intra: după
    corectura site-ului, extragerea lui (dinaintea ultimei date) nu mai venea."""
    rows = ["24-09-2026,1,2,3,4,5,6", "27-09-2026,7,8,9,10,11,12"]
    page = f"2026-09-24 1 2 3 4 5 6 2026-09-27 7 8 9 10 11 12 {bad} 2026-10-04 24 10 12 15 32 40"
    added, before, istoric = _run(
        tmp_path, monkeypatch, {"loto_6_49": rows}, {"loto_6_49": page}
    )
    out = capsys.readouterr().out
    assert added == 0, out
    assert _unchanged(istoric, before)
    assert (
        "EROARE verificare: rândul de pe site" in out and f"e invalid ({reason})" in out
    )


@pytest.mark.parametrize(
    "rows, page, previous",
    [
        # Rândul nou copiază ultimul rând stocat (în altă ordine).
        (
            ["01-10-2026,1,2,3,4,5,6", "04-10-2026,32,14,4,7,28,34"],
            "2026-10-01 1 2 3 4 5 6 2026-10-04 32 14 4 7 28 34 2026-10-07 34 28 14 7 4 32",
            "04-10-2026",
        ),
        # Două rânduri noi, al doilea copie a primului (ca 24-10-2024 = 27-10-2024).
        (
            ["01-10-2026,1,2,3,4,5,6"],
            "2026-10-01 1 2 3 4 5 6 2026-10-04 13 34 11 16 10 39 2026-10-07 13 34 11 16 10 39",
            "04-10-2026",
        ),
    ],
)
def test_update_all_refuses_a_new_draw_that_copies_the_previous_row(
    tmp_path, monkeypatch, capsys, rows, page, previous
):
    """AGENTS.md §4.1: verifica_istoric refuză fișierul abia după scriere, iar
    CSV-ul local (citit de UI, bench, WF) rămânea cu copia."""
    added, before, istoric = _run(
        tmp_path, monkeypatch, {"loto_5_40": rows}, {"loto_5_40": page}
    )
    out = capsys.readouterr().out
    assert added == 0, out
    assert _unchanged(istoric, before)
    assert (
        "EROARE verificare: extragerea din 07-10-2026 repetă numerele rândului "
        f"din {previous} (copie, nu extragere)" in out
    )


def test_update_all_reports_an_error_when_the_page_has_no_draws(
    tmp_path, monkeypatch, capsys
):
    """Pagina de protecție servită cu 200 sau altă structură a tabelului:
    înainte „la zi”, iar ACTUALIZARI.bat nu avertiza (caută „EROARE”)."""
    rows = {key: ["01-10-2026,1,2,3,4,5,6"] for key in uc.GAME_CONFIGS}
    page = "Checking your browser before accessing loto49.ro ... Please wait"
    pages = {key: page for key in uc.GAME_CONFIGS}
    added, before, istoric = _run(tmp_path, monkeypatch, rows, pages)
    out = capsys.readouterr().out
    assert added == 0 and _unchanged(istoric, before)
    assert out.count("EROARE verificare: pagina nu conține nicio extragere") == 3
    assert "NEACTUALIZATE: Loto 6/49, Joker, Loto 5/40" in out
    assert "la zi" not in out


def test_a_stored_date_without_leading_zero_is_the_same_draw(tmp_path, monkeypatch, capsys):
    """Un rând adăugat de mână ca „4-10-2026” nu e altă extragere decât
    „2026-10-04” de pe site: nici refuz fals, nici dublură; extragerea nouă intră."""
    rows = ["01-10-2026,1,2,3,4,5,6", "4-10-2026,24,10,12,15,32,40"]
    page = (
        "2026-10-01 1 2 3 4 5 6 2026-10-04 24 10 12 15 32 40 "
        "2026-10-08 7 8 9 10 11 13"
    )
    added, _before, istoric = _run(tmp_path, monkeypatch, {"loto_6_49": rows}, {"loto_6_49": page})
    out = capsys.readouterr().out
    assert added == 1, out
    lines = (istoric / uc.GAME_CONFIGS["loto_6_49"]["csv_name"]).read_text().splitlines()
    assert lines[-2:] == ["4-10-2026,24,10,12,15,32,40", "08-10-2026,7,8,9,10,11,13"]

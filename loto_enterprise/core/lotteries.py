"""Registrul loteriilor: singura sursă de IDENTITATE a unui joc.

Două noțiuni, ținute separat peste tot în aplicație:

- GEOMETRIA (`game_type`: "6/49", "5/40", "joker", "6/45", "5/50"): câte
  numere se extrag, câte are biletul, universul. Motorul, walk-forward-ul și
  wheeling-ul lucrează numai cu ea; un Lotto german 6 din 49 are geometria
  "6/49", exact ca Loto 6/49 românesc.
- IDENTITATEA (`country` ISO + `game_id` + `bench_key`): ce decizie de bench,
  ce istoric de pool-uri, ce tarif, ce bilet fizic, ce zile de extragere.
  Identitatea NU se ghicește din geometrie și nici din numele fișierului.

Jocurile românești păstrează cheile de dinainte (id = eticheta internă
„6/49”/„5/40”/„joker”, cheile de bench `loto_6_49`, `loto_5_40`,
`joker_urna1`/`joker_urna2`, CSV-urile din `_ISTORIC/`), ca task-urile,
cache-urile și deciziile existente să rămână neatinse. Un joc nou este UN rând
în `GAMES` (plus CSV-ul lui în `_ISTORIC/externe/`); restul aplicației citește
de aici. Un id sau o țară necunoscută ridică `UnknownLotteryError`: nu se cade
niciodată tăcut pe România.

Faptele (tarif, bilet, zile) sunt numai cele verificate pe sursele oficiale;
ce nu e verificat rămâne `None`, iar interfața spune „tarif necunoscut” /
„bilet nemodelat”. Modulul e pur stdlib: îl importă worker-ul, benchmark-ul și
interfața fără pandas.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

RO = "RO"
ROMANIA = "România"

# Numele fișierelor și directoarelor de ieșire, relative la rădăcina proiectului.
# România rămâne pe căile de dinainte; celelalte țări au propriul director.
DECISION_FILE = "best_methods.json"
DECISIONS_DIR = "decisions"
BENCH_OUT_DIR = "bench_results"
COUNTRY_BENCH_SUBDIR = "countries"

# Id-urile și cheile de bench străine intră în nume de fișiere (cache WF,
# folds.csv, .bench_pid), deci numai litere mici, cifre și „_”.
ID_PATTERN = re.compile(r"[a-z0-9_]+")
# Subșiruri după care cod vechi ghicește jocul românesc din nume
# (`_game_label_for`, `worker._map_game_label`, `runner.discover_games`).
# Un id sau fișier străin care le conține ar fi luat drept joc românesc.
LEGACY_SNIFF_SUBSTRINGS = ("joker", "649", "6_49", "5_40", "5/40")


class UnknownLotteryError(ValueError):
    """Țară sau joc care nu există în registru (niciodată fallback pe România)."""


@dataclass(frozen=True, kw_only=True)
class SecondDrum:
    """A doua urnă a unei geometrii (Joker: 1 din 20; EuroMillions: 2 din 12)."""

    draw_n: int
    max_n: int
    columns: tuple[str, ...]
    # False = numerele sunt în CSV, dar aplicația nu le generează încă.
    modelled: bool


@dataclass(frozen=True, kw_only=True)
class Geometry:
    name: str
    draw_n: int  # numere EXTRASE (hiturile se numără pe ele)
    pick_n: int  # numere pe BILET (plafonul garanției)
    max_n: int  # universul 1..max_n
    second: SecondDrum | None = None


GEOMETRIES: dict[str, Geometry] = {
    g.name: g
    for g in (
        Geometry(name="6/49", draw_n=6, pick_n=6, max_n=49),
        # Loto 5/40: se extrag 6 numere, biletul are 5.
        Geometry(name="5/40", draw_n=6, pick_n=5, max_n=40),
        Geometry(
            name="joker",
            draw_n=5,
            pick_n=5,
            max_n=45,
            second=SecondDrum(draw_n=1, max_n=20, columns=("joker",), modelled=True),
        ),
        Geometry(name="6/45", draw_n=6, pick_n=6, max_n=45),
        Geometry(
            name="5/50",
            draw_n=5,
            pick_n=5,
            max_n=50,
            # Stelele EuroMillions (2 din 12) stau în CSV ca s1,s2; nu se generează.
            second=SecondDrum(draw_n=2, max_n=12, columns=("s1", "s2"), modelled=False),
        ),
    )
}


@dataclass(frozen=True, kw_only=True)
class Lottery:
    game_id: str
    country: str  # ISO 3166-1 alpha-2 („EU” pentru jocurile multinaționale)
    country_name: str
    name: str
    geometry: str  # cheie în GEOMETRIES
    bench_key: str  # cheia din best_methods.json / folds.csv
    csv: str  # istoricul, relativ la rădăcina proiectului
    per_ticket: int | None  # variante pe un bilet fizic (None = nemodelat)
    price: float | None  # tarif pe variantă (None = necunoscut)
    currency: str
    price_source: str | None
    draw_weekdays: tuple[int, ...]  # date.weekday(): 0 = luni … 6 = duminică
    # True = se joacă online din România pe site-ul oficial; False = doar
    # antrenament (bench, walk-forward, analize); None = neverificat, tratat
    # ca doar antrenament.
    playable_from_ro: bool | None
    display_order: int  # ordinea în rezultate/mail, în cadrul țării
    wf_order: int  # ordinea walk-forward, în cadrul țării (rapid întâi)
    min_hit_target: int = 3  # ținta minimă a deciziei (3 numere aduc premiu)
    bench_key_urna2: str | None = None  # numai geometria "joker"
    # Unde se joacă un joc care NU se joacă online din România, dar se joacă
    # altfel (ex. în agenție, peste graniță). Setat => jocul nu e „doar
    # antrenament”, iar interfața arată nota în locul celei de antrenament.
    play_note: str | None = None

    @property
    def key(self) -> str:
        """Compatibilitate: numele vechi al id-ului."""
        return self.game_id

    @property
    def display(self) -> str:
        return f"{self.country_name} · {self.name}"

    @property
    def geo(self) -> Geometry:
        return GEOMETRIES[self.geometry]

    @property
    def draw_n(self) -> int:
        return self.geo.draw_n

    @property
    def pick_n(self) -> int:
        return self.geo.pick_n

    @property
    def max_n(self) -> int:
        return self.geo.max_n

    @property
    def is_romanian(self) -> bool:
        return self.country == RO

    @property
    def training_only(self) -> bool:
        return self.playable_from_ro is not True and not self.play_note

    @property
    def bench_keys(self) -> tuple[str, ...]:
        if self.bench_key_urna2:
            return (self.bench_key, self.bench_key_urna2)
        return (self.bench_key,)

    @property
    def history_key_prefix(self) -> str:
        """Prefixul cheilor din pool_history.json / adaptive_state.json.

        Gol pentru România (cheile rămân `6/49_12` etc.); celelalte țări nu au
        voie să scrie peste intrarea românească de aceeași geometrie."""
        return "" if self.is_romanian else f"{self.country}_{self.game_id}_"


_RO_PRICE_SOURCE = "loto.ro/info-loto/preturi (verificat 2026-09-04)"

GAMES: tuple[Lottery, ...] = (
    # --- România: id-uri, chei și fișiere de dinainte (neschimbate) ---------
    Lottery(
        game_id="6/49",
        country=RO,
        country_name=ROMANIA,
        name="Loto 6/49",
        geometry="6/49",
        bench_key="loto_6_49",
        csv="_ISTORIC/loto_6_49.csv",
        per_ticket=3,
        price=8.0,
        currency="Lei",
        price_source=_RO_PRICE_SOURCE,
        draw_weekdays=(3, 6),
        playable_from_ro=True,
        display_order=0,
        wf_order=2,
    ),
    Lottery(
        game_id="joker",
        country=RO,
        country_name=ROMANIA,
        name="Joker",
        geometry="joker",
        bench_key="joker_urna1",
        bench_key_urna2="joker_urna2",
        csv="_ISTORIC/joker.csv",
        per_ticket=2,
        price=7.0,
        currency="Lei",
        price_source=_RO_PRICE_SOURCE,
        draw_weekdays=(3, 6),
        playable_from_ro=True,
        display_order=1,
        wf_order=0,
    ),
    Lottery(
        game_id="5/40",
        country=RO,
        country_name=ROMANIA,
        name="Loto 5/40",
        geometry="5/40",
        bench_key="loto_5_40",
        csv="_ISTORIC/loto_5_40.csv",
        per_ticket=4,
        price=5.0,
        currency="Lei",
        price_source=_RO_PRICE_SOURCE,
        draw_weekdays=(3, 6),
        playable_from_ro=True,
        display_order=2,
        wf_order=1,
        # 3 numere nu aduc premiu la 5/40: cel mai mic premiu cere 4.
        min_hit_target=4,
    ),
    # --- Uniunea Europeană: istorice în _ISTORIC/externe/ ------------------
    # Online numai cu domiciliu în Germania (lotto.de, Glücksspielstaatsvertrag).
    Lottery(
        game_id="de_lotto",
        country="DE",
        country_name="Germania",
        name="Lotto 6aus49",
        geometry="6/49",
        bench_key="de_lotto",
        csv="_ISTORIC/externe/germania_lotto_6aus49.csv",
        per_ticket=12,
        price=1.20,
        currency="EUR",
        price_source=(
            "lotto.de/lotto-6aus49/spielregeln (verificat 2026-09-27; "
            "fără taxa pe bilet a landului)"
        ),
        draw_weekdays=(2, 5),
        playable_from_ro=False,
        display_order=0,
        wf_order=0,
    ),
    # Joc online din România neverificat (lotto.pl blochează accesul automat).
    Lottery(
        game_id="pl_lotto",
        country="PL",
        country_name="Polonia",
        name="Lotto",
        geometry="6/49",
        bench_key="pl_lotto",
        csv="_ISTORIC/externe/polonia_lotto_6din49.csv",
        per_ticket=8,
        price=5.00,
        currency="PLN",
        price_source=(
            "bip.totalizator.pl, regulamin Lotto, de la 18.08.2026 "
            "(verificat 2026-09-27)"
        ),
        draw_weekdays=(1, 3, 5),
        playable_from_ro=None,
        display_order=0,
        wf_order=0,
    ),
    # Online numai cu rezidență în Spania (contractul de joc SELAE).
    Lottery(
        game_id="es_primitiva",
        country="ES",
        country_name="Spania",
        name="La Primitiva",
        geometry="6/49",
        bench_key="es_primitiva",
        csv="_ISTORIC/externe/spania_la_primitiva_6din49.csv",
        per_ticket=8,
        price=1.00,
        currency="EUR",
        price_source="loteriasyapuestas.es (verificat 2026-09-27)",
        draw_weekdays=(0, 3, 5),
        playable_from_ro=False,
        display_order=0,
        wf_order=0,
    ),
    # Joc online din România neverificat.
    Lottery(
        game_id="at_lotto",
        country="AT",
        country_name="Austria",
        name="Lotto 6 aus 45",
        geometry="6/45",
        bench_key="at_lotto",
        csv="_ISTORIC/externe/austria_lotto_6aus45.csv",
        per_ticket=12,
        price=1.5,
        currency="EUR",
        price_source="win2day.at / lotterien.at (verificat 2026-09-27)",
        draw_weekdays=(2, 6),
        playable_from_ro=None,
        display_order=0,
        wf_order=0,
    ),
    # Joc online din România neverificat.
    Lottery(
        game_id="be_lotto",
        country="BE",
        country_name="Belgia",
        name="Lotto",
        geometry="6/45",
        bench_key="be_lotto",
        csv="_ISTORIC/externe/belgia_lotto_6din45.csv",
        per_ticket=14,
        price=1.5,
        currency="EUR",
        price_source="loterie-nationale.be (verificat 2026-09-27)",
        draw_weekdays=(2, 5),
        playable_from_ro=None,
        display_order=0,
        wf_order=0,
    ),
    # Joc online din România neverificat.
    Lottery(
        game_id="hu_hatos",
        country="HU",
        country_name="Ungaria",
        name="Hatoslottó",
        geometry="6/45",
        bench_key="hu_hatos",
        csv="_ISTORIC/externe/ungaria_hatoslotto_6din45.csv",
        per_ticket=20,
        price=500.0,
        currency="HUF",
        price_source="szerencsejatek.hu (verificat 2026-09-27)",
        draw_weekdays=(3, 6),
        playable_from_ro=None,
        display_order=0,
        wf_order=0,
    ),
    # Joc online din România neverificat. Două extrageri pe zi.
    Lottery(
        game_id="cz_sportka",
        country="CZ",
        country_name="Cehia",
        name="Sportka",
        geometry="6/49",
        bench_key="cz_sportka",
        csv="_ISTORIC/externe/cehia_sportka_6din49.csv",
        per_ticket=8,
        price=30.0,
        currency="CZK",
        price_source="sazka.cz (verificat 2026-09-27; două extrageri pe zi)",
        draw_weekdays=(2, 4, 6),
        playable_from_ro=None,
        display_order=0,
        wf_order=0,
    ),
    # Joc online din România neverificat.
    Lottery(
        game_id="sk_loto",
        country="SK",
        country_name="Slovacia",
        name="Loto",
        geometry="6/49",
        bench_key="sk_loto",
        csv="_ISTORIC/externe/slovacia_loto_6din49.csv",
        per_ticket=10,
        price=1.2,
        currency="EUR",
        price_source="tipos.sk (verificat 2026-09-27)",
        draw_weekdays=(2, 6),
        playable_from_ro=None,
        display_order=0,
        wf_order=0,
    ),
    # Nu se joacă online din România; se joacă în agenție în Bulgaria.
    Lottery(
        game_id="bg_toto2",
        country="BG",
        country_name="Bulgaria",
        name="Toto 2 6/49",
        geometry="6/49",
        bench_key="bg_toto2",
        csv="_ISTORIC/externe/bulgaria_toto2_6din49.csv",
        # 4 zone pe fișa fizică (după utilizator, info.toto.bg/toto1-i-toto2/
        # toto-2-6-ot-49; pagina nu s-a putut citi automat).
        per_ticket=4,
        price=1.0,
        currency="EUR",
        price_source="după utilizator (2026-09-27); toto.bg nu s-a putut citi automat",
        draw_weekdays=(3, 6),
        playable_from_ro=False,
        play_note="se joacă în agenție în Bulgaria, nu online din România",
        display_order=0,
        wf_order=0,
    ),
    # Online numai cu rezidență într-o țară participantă; stelele nu se modelează.
    Lottery(
        game_id="eu_euromillions",
        country="EU",
        country_name="EuroMillions",
        name="EuroMillions",
        geometry="5/50",
        bench_key="eu_euromillions",
        csv="_ISTORIC/externe/euromillions_5din50_stele.csv",
        per_ticket=None,
        price=None,
        currency="EUR",
        price_source=None,
        draw_weekdays=(1, 4),
        playable_from_ro=False,
        display_order=0,
        wf_order=0,
    ),
)

GAMES_BY_ID: dict[str, Lottery] = {lot.game_id: lot for lot in GAMES}

# Compatibilitate: jocurile românești sub eticheta lor internă („6/49”,
# „5/40”, „joker”), aceeași cu `ui_runtime._game_label_for` și cu cheile
# biletului fizic (`full_ticket.TICKET_VARIANTS`).
LOTTERIES: dict[str, Lottery] = {lot.game_id: lot for lot in GAMES if lot.is_romanian}


def _check_registry() -> None:
    """Invarianți verificați la import: o greșeală de registru oprește
    aplicația, nu produce o decizie sau o cheie de cache greșită."""
    ids: set[str] = set()
    keys: set[str] = set()
    for lot in GAMES:
        if lot.geometry not in GEOMETRIES:
            raise ValueError(f"{lot.game_id}: geometrie necunoscută {lot.geometry!r}")
        if lot.game_id in ids:
            raise ValueError(f"id duplicat în registru: {lot.game_id}")
        ids.add(lot.game_id)
        for key in lot.bench_keys:
            if key in keys:
                raise ValueError(f"cheie de bench duplicată: {key}")
            keys.add(key)
        second = GEOMETRIES[lot.geometry].second
        needs_urna2 = bool(second and second.modelled and second.draw_n == 1)
        if needs_urna2 != bool(lot.bench_key_urna2):
            raise ValueError(f"{lot.game_id}: cheia Urnei 2 nu corespunde geometriei")


_check_registry()


def normalize_country(country) -> str:
    """„de” → „DE”; o țară care nu are niciun joc în registru ridică eroare."""
    cc = str(country or "").strip().upper()
    if not cc or not any(lot.country == cc for lot in GAMES):
        raise UnknownLotteryError(f"țară necunoscută: {country!r}")
    return cc


def countries() -> list[str]:
    """Codurile țărilor: România prima, apoi după numele românesc al țării."""
    names = {lot.country: lot.country_name for lot in GAMES}
    others = sorted((cc for cc in names if cc != RO), key=lambda c: names[c].casefold())
    return [RO, *others] if RO in names else others


def country_name(country) -> str:
    cc = normalize_country(country)
    return next(lot.country_name for lot in GAMES if lot.country == cc)


def games_for_country(country) -> tuple[Lottery, ...]:
    """Jocurile unei țări, în ordinea de afișare."""
    cc = normalize_country(country)
    return tuple(
        sorted(
            (lot for lot in GAMES if lot.country == cc), key=lambda g: g.display_order
        )
    )


def lottery_by_id(game_id) -> Lottery | None:
    return GAMES_BY_ID.get(str(game_id))


def require_lottery(game_id, country=None) -> Lottery:
    """Jocul cu acest id (și, dacă e dată, din această țară). Altfel eroare."""
    lot = GAMES_BY_ID.get(str(game_id))
    if lot is None:
        raise UnknownLotteryError(f"joc necunoscut: {game_id!r}")
    if country is not None and lot.country != normalize_country(country):
        raise UnknownLotteryError(
            f"jocul {game_id!r} aparține țării {lot.country}, nu {country!r}"
        )
    return lot


def lottery_by_bench_key(bench_key) -> Lottery | None:
    """Jocul căruia îi aparține cheia de bench (principală sau Urna 2)."""
    key = str(bench_key)
    return next((lot for lot in GAMES if key in lot.bench_keys), None)


def ro_lottery_for_geometry(geometry) -> Lottery | None:
    """Jocul românesc cu această geometrie (câte unul pe geometrie)."""
    return next(
        (lot for lot in GAMES if lot.is_romanian and lot.geometry == str(geometry)),
        None,
    )


def decision_path_for(country, root: Path | str | None = None) -> Path:
    """Fișierul de decizie al țării: România `best_methods.json` (neschimbat),
    celelalte `decisions/<CC>/best_methods.json`."""
    cc = normalize_country(country)
    base = Path(root) if root is not None else PROJECT_ROOT
    if cc == RO:
        return base / DECISION_FILE
    return base / DECISIONS_DIR / cc / DECISION_FILE


def bench_out_dir_for(country, root: Path | str | None = None) -> Path:
    """Directorul de ieșire al bench-ului: România `bench_results/` (neschimbat),
    celelalte `bench_results/countries/<CC>/`."""
    cc = normalize_country(country)
    base = Path(root) if root is not None else PROJECT_ROOT
    if cc == RO:
        return base / BENCH_OUT_DIR
    return base / BENCH_OUT_DIR / COUNTRY_BENCH_SUBDIR / cc


def display_name(game: str) -> str:
    """„România · Loto 6/49”, „Germania · Lotto 6aus49”; un joc necunoscut
    rămâne cu eticheta lui."""
    lottery = GAMES_BY_ID.get(str(game))
    return lottery.display if lottery else str(game)


def foreign_bench_draw_pick() -> dict[str, tuple[int, int]]:
    """(extrase, bilet) pentru cheile de bench străine (România are tabelul ei
    literal în `benchmark.hit_target`)."""
    out: dict[str, tuple[int, int]] = {}
    for lot in GAMES:
        if lot.is_romanian:
            continue
        out[lot.bench_key] = (lot.draw_n, lot.pick_n)
        if lot.bench_key_urna2:
            out[lot.bench_key_urna2] = (1, 1)
    return out


def foreign_bench_max_num() -> dict[str, int]:
    """Universul pentru cheile de bench străine (baseline hipergeometric)."""
    out: dict[str, int] = {}
    for lot in GAMES:
        if lot.is_romanian:
            continue
        out[lot.bench_key] = lot.max_n
        second = lot.geo.second
        if lot.bench_key_urna2 and second is not None:
            out[lot.bench_key_urna2] = second.max_n
    return out


def foreign_min_hit_target() -> dict[str, int]:
    """Ținta minimă a deciziei pentru cheile străine care cer mai mult de 3."""
    return {
        lot.bench_key: int(lot.min_hit_target)
        for lot in GAMES
        if not lot.is_romanian and int(lot.min_hit_target) > 3
    }

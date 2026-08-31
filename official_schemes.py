"""Scheme reduse predefinite Loteria Română — date PUBLICE, nu wheel-ul nostru.

De ce există modulul: UI-ul compara costul wheel-ului cu „Cod NN" de la agenție,
dar garanția t a schemelor oficiale NU e aceeași cu `guarantee` din setări.
Loto.ro publică totuși destul ca să nu mai zicem „nu e documentată":

  6/49 — pagina oficială *Scheme reduse predefinite* (Tabelul 1: cod / n / variante
  + indexul COMPLET de categorii pentru Cod 48, cu cele 12 bilete). Formularea
  generică pentru TOATE schemele 6/49: dacă ies 6, 5, 4 sau 3 numere din cele
  jucate → „unul sau mai multe câștiguri" (cat. IV = 3 pe un bilet). Indexul
  pe categorii (câți I/II/III/IV) e public doar pentru Cod 48.

  5/40 — pagina HTML analogă 404; broșura oficială (iframe pe loto.ro) are
  Tabelul 5 (coduri) + indexul Cod 15 (7 nr. / 9 var.). 5/40 NU premiază 3
  numere pe bilet (cat. I=5/5, II=5/6, III=4/6).

  Joker — simulatorul loto.ro listează modelele Cod 45/35/34/24/15/14;
  tabelele publice de terminal au și 25/23/13/12. Fără index t pe loto.ro.
  Variantele se înmulțesc cu N jokeri marcați; app-ul costă la N=1.

Comparația cu wheel-ul nostru e pe NUMĂR de variante + limita Schönheim
(imposibilitate matematică a unui t-cover), NU o afirmație că schema oficială
„are garanție t". Terțe (ponturi.ro, iasi365) inventează t — nu le copiem.

Verificat 2026-08-31 pe biletele Cod 48 de pe loto.ro: 3-cover și 4-cover
ale lui {1..9}; indexul de categorii se potrivește bit-cu-bit (vezi teste).
"""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil, comb
from pathlib import Path

_DESIGN_DIR = Path(__file__).resolve().parent / "covering_designs"

# Pagina HTML 6/49 (Tabelul 1 + biletele + indexul Cod 48).
URL_649_SCHEMES = (
    "https://www.loto.ro/loto-new/newLotoSiteNexioFinalVersion/web/"
    "app2.php/jocuri/649_si_noroc/cum_se_joaca/Scheme_reduse_predefinite.html"
)
URL_649_CUM_SE_JOACA = "https://www.loto.ro/?p=3878"
URL_540_BROSURA = (
    "https://www.loto.ro/loto-new/newLotoSiteNexioFinalVersion/web/"
    "app2.php/jocuri/540_si_super_noroc/brosura.html"
)
URL_JOKER_SIM = (
    "https://www.loto.ro/loto-new/newLotoSiteNexioFinalVersion/web/"
    "app2.php/jocuri/joker_si_noroc_plus/joaca_online.html"
)

SOURCE_LABEL = {
    "6/49": "loto.ro Tabelul 1",
    "5/40": "broșura Loto 5/40 (Tabelul 5)",
    "joker": "loto.ro simulator / tabel terminal Joker",
}
SOURCE_URL = {
    "6/49": URL_649_SCHEMES,
    "5/40": URL_540_BROSURA,
    "joker": URL_JOKER_SIM,
}


@dataclass(frozen=True)
class OfficialScheme:
    code: int
    n_numbers: int
    n_variants: int
    n_full: int  # C(n_numbers, pick) — coloana „combinație completă"


def _S(code: int, n: int, n_red: int, pick: int) -> OfficialScheme:
    return OfficialScheme(code, n, n_red, comb(n, pick))


# 6/49 pick=6 — Tabelul 1 loto.ro (verificat 2026-08-31).
_SCHEMES_649: tuple[OfficialScheme, ...] = (
    _S(48, 9, 12, 6),
    _S(49, 10, 15, 6),
    _S(50, 10, 30, 6),
    _S(56, 11, 66, 6),
    _S(57, 12, 22, 6),
    _S(58, 12, 132, 6),
    _S(59, 16, 112, 6),
)

# 5/40 pick=5 — broșura oficială Tabelul 5 (aceleași cifre ca lotoinfo / yumpu).
# Păstrăm doar pool-uri ≤ 16 (plafonul UI).
_SCHEMES_540: tuple[OfficialScheme, ...] = (
    _S(15, 7, 9, 5),
    _S(16, 8, 21, 5),
    _S(17, 9, 30, 5),
    _S(18, 10, 51, 5),
    _S(25, 11, 66, 5),
    _S(26, 12, 113, 5),
    _S(27, 13, 173, 5),
    _S(28, 14, 255, 5),
    _S(35, 15, 243, 5),
    _S(36, 15, 327, 5),
    _S(37, 16, 348, 5),
    _S(38, 16, 443, 5),
)

# Joker pick=5, variante = n_red × N jokeri. N=1 mai jos (costul din app).
# 45/35/34/24/15/14 = modele pe simulatorul loto.ro; 25/23/13/12 = tabel terminal.
_SCHEMES_JOKER: tuple[OfficialScheme, ...] = (
    _S(45, 7, 5, 5),
    _S(35, 8, 6, 5),
    _S(34, 9, 9, 5),
    _S(25, 9, 30, 5),
    _S(24, 10, 14, 5),
    _S(23, 10, 51, 5),
    _S(15, 11, 22, 5),
    _S(14, 12, 38, 5),
    _S(13, 13, 54, 5),
    _S(12, 15, 118, 5),
)

SCHEMES: dict[str, tuple[OfficialScheme, ...]] = {
    "6/49": _SCHEMES_649,
    "5/40": _SCHEMES_540,
    "joker": _SCHEMES_JOKER,
}

# Biletele Cod 48 — transcrise din pagina oficială 6/49 (numerele 1..9).
COD_48_TICKETS: tuple[tuple[int, ...], ...] = (
    (1, 2, 3, 4, 5, 6),
    (1, 2, 3, 4, 7, 8),
    (1, 2, 3, 6, 7, 9),
    (1, 2, 4, 5, 8, 9),
    (1, 2, 5, 6, 7, 8),
    (1, 3, 4, 6, 8, 9),
    (1, 3, 5, 7, 8, 9),
    (1, 4, 5, 6, 7, 9),
    (2, 3, 4, 5, 7, 9),
    (2, 3, 5, 6, 8, 9),
    (2, 4, 6, 7, 8, 9),
    (3, 4, 5, 6, 7, 8),
)

# Index oficial Cod 48: (n_I, n_II, n_III, n_IV) posibile, per k ieșite din cele 9.
# Sursa: aceeași pagină loto.ro. Verificat exhaustiv pe biletele de mai sus.
COD_48_PRIZE_PATTERNS: dict[int, frozenset[tuple[int, int, int, int]]] = {
    3: frozenset({(0, 0, 0, 2), (0, 0, 0, 3)}),
    4: frozenset({(0, 0, 2, 4), (0, 0, 1, 7)}),
    5: frozenset({(0, 1, 3, 7), (0, 0, 6, 4)}),
    6: frozenset({(1, 0, 9, 2), (0, 3, 6, 3)}),
}


def schemes_for(game: str, n_numbers: int) -> tuple[OfficialScheme, ...]:
    return tuple(s for s in SCHEMES.get(game, ()) if s.n_numbers == n_numbers)


def lr_schemes() -> dict[str, dict[int, list[tuple[str, int]]]]:
    """Compat: {joc: {n_numere: [(„Cod NN", n_variante), ...]}}."""
    out: dict[str, dict[int, list[tuple[str, int]]]] = {}
    for game, items in SCHEMES.items():
        by_n: dict[int, list[tuple[str, int]]] = {}
        for s in items:
            by_n.setdefault(s.n_numbers, []).append((f"Cod {s.code}", s.n_variants))
        out[game] = by_n
    return out


# Folosit de app_nicegui (același nume ca înainte).
LR_SCHEMES = lr_schemes()


def schonheim(v: int, k: int, t: int) -> int:
    """Limita inferioară Schönheim C(v,k,t) — niciun t-cover nu e mai mic."""
    if t < 1 or k < t or v < k:
        return 0
    if t == 1:
        return ceil(v / k)
    return ceil(v / k * schonheim(v - 1, k - 1, t - 1))


def our_cover_size(v: int, pick: int, t: int) -> int | None:
    """Numărul de bilete din covering_designs/C_v_pick_t.txt, dacă există."""
    path = _DESIGN_DIR / f"C_{v}_{pick}_{t}.txt"
    if not path.is_file():
        return None
    return sum(1 for ln in path.read_text().splitlines() if ln.strip())


def category_counts_649(tickets: tuple[tuple[int, ...], ...], drawn: tuple[int, ...]) -> tuple[int, int, int, int]:
    """(n_I, n_II, n_III, n_IV) = bilete cu 6/5/4/3 hituri față de `drawn`."""
    d = set(drawn)
    hits = [len(set(t) & d) for t in tickets]
    return (
        sum(h == 6 for h in hits),
        sum(h == 5 for h in hits),
        sum(h == 4 for h in hits),
        sum(h == 3 for h in hits),
    )


def _size_vs_cover(n_official: int, v: int, pick: int, t: int) -> str | None:
    """Comparație matematică (nu afirmație oficială de t)."""
    if t < 1:
        return None
    lb = schonheim(v, pick, t)
    ours = our_cover_size(v, pick, t)
    if n_official < lb:
        return (
            f"{n_official} var. < Schönheim C({v},{pick},{t})={lb} "
            f"→ **nu poate fi {t}-cover**"
        )
    if ours is not None and n_official == ours:
        return (
            f"{n_official} var. = cover-ul nostru g={t} "
            f"(același număr, nu neapărat aceleași bilete)"
        )
    if ours is not None and n_official < ours:
        return (
            f"{n_official} var. vs cover-ul nostru g={t}: {ours} var. "
            f"(oficial e mai ieftin; Schönheim={lb}, deci un {t}-cover e posibil, "
            f"dar n-avem indexul)"
        )
    if ours is not None:
        return (
            f"{n_official} var. vs cover-ul nostru g={t}: {ours} var. "
            f"(oficial e mai scump)"
        )
    return f"{n_official} var.; Schönheim C({v},{pick},{t})={lb}"


def _prize_note(game: str, schemes: tuple[OfficialScheme, ...]) -> str:
    if game == "6/49":
        generic = (
            "dacă ies **3, 4, 5 sau 6** numere din cele jucate, schema asigură "
            "*unul sau mai multe câștiguri* (cat. IV = 3 pe un bilet). "
            "Indexul pe categorii (câți I/II/III/IV) e pe loto.ro **doar pentru Cod 48**."
        )
        if any(s.code == 48 for s in schemes):
            return (
                generic
                + " **Cod 48 (9 nr.):** 3 din 9 → ≥2× cat. IV; 4 din 9 → ≥1× cat. III "
                "(verificat pe cele 12 bilete de pe loto.ro — e și 4-cover). "
                "6 din 9: **fie** 1× cat. I + 9× III + 2× IV, "
                "**fie** 3× cat. II + 6× III + 3× IV (nu e mereu cat. I)."
            )
        return generic
    if game == "5/40":
        cats = (
            "5/40 premiază 5/5, 5/6 și 4/6 — **nu există cat. pentru 3 hituri**."
        )
        if any(s.code == 15 for s in schemes):
            return (
                cats
                + " **Cod 15 (7 nr. / 9 var.):** 4 din 7 → ≥1× cat. III; "
                "5 sau 6 din 7 nu garantează cat. I."
            )
        return (
            cats
            + " Indexul de categorii din broșură e public pentru Cod 15; "
            "celelalte coduri au doar tabelul de variante."
        )
    return (
        "simulatorul loto.ro are modelele de schemă; **fără index t public**. "
        "La agenție variantele se înmulțesc cu N jokeri marcați "
        "(cifrele de aici = N=1, ca wheel-ul din app)."
    )


def format_official_block(
    game: str,
    pool: int,
    *,
    price: float,
    pick: int,
    full_lbl: str,
    joker_note: str = "",
    our_guarantee: int | None = None,
    our_n_tickets: int | None = None,
) -> str:
    """Markdown pentru panoul de cost din UI (scheme + garanție publicată)."""
    schemes = schemes_for(game, pool)
    src = SOURCE_LABEL.get(game, "Loteria Română")
    url = SOURCE_URL.get(game, URL_649_SCHEMES)

    if not schemes:
        return (
            f"💡 **Cost la agenție:** fără schemă redusă oficială pentru {pool} nr. "
            f"la {game}. **{full_lbl}** (toate combinațiile, exhaustiv)."
        )

    parts = []
    for s in schemes:
        parts.append(
            f"**Cod {s.code}** ({s.n_variants} var.{joker_note} ≈ {s.n_variants * price:,.0f} Lei)"
        )
    head = (
        f"💡 **Scheme reduse oficiale** ([{src}]({url}), {pool} nr.): "
        + " sau ".join(parts)
        + f"\n\n*({full_lbl} — toate combinațiile, exhaustiv)*"
    )

    prize = _prize_note(game, schemes)
    body = f"**Ce publică Loteria Română:** {prize}"

    cmp_lines: list[str] = []
    t = int(our_guarantee) if our_guarantee is not None else None
    if t is not None:
        for s in schemes:
            vs = _size_vs_cover(s.n_variants, pool, pick, t)
            if vs:
                cmp_lines.append(f"Cod {s.code}: {vs}")
        extra = f"; wheel-ul generat acum: {our_n_tickets} var." if our_n_tickets else ""
        cmp_lines.append(
            f"Wheel-ul nostru e un cover C({pool},{pick},{t}) la garanția "
            f"**configurată aici**{extra} — nu e garanția din broșura agenției"
        )
    else:
        cmp_lines.append(
            "Nu compara numărul de variante cu wheel-ul de mai jos ca și cum "
            "ar avea aceeași garanție t — vezi indexul public de mai sus"
        )
    cmp = "**Vs. wheel-ul nostru** (număr de variante, nu t oficial):\n" + "\n".join(
        f"- {ln}." for ln in cmp_lines
    )
    return head + "\n\n" + body + "\n\n" + cmp

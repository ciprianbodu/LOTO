"""Importă schemele reduse predefinite ale Loteriei Române din broșurile loto.ro.

Broșurile („Scheme reduse predefinite: desfășurări și indexuri de câștiguri”)
tipăresc, pentru fiecare cod, grila variantelor (X pe pozițiile 1..n) și indexul
de câștiguri: câte variante câștigă la fiecare categorie, după câte numere din
schemă ies la tragere. Scriptul citește grila din coordonatele cuvintelor
(`pdftotext -bbox`, poppler), recalculează indexul din variante și scrie schema
numai dacă indexul recalculat coincide exact cu cel tipărit:

- 6/49: categoriile I-IV, numărul de cazuri pe fiecare model;
- 5/40: categoriile 5/5, 5/6, 4/6, cu al șaselea număr extras enumerat;
- Joker: tabelul fără numărul din câmpul 2, intervalele pe categoriile 2, 4, 6
  și procentul cazurilor.

Ieșire: ``covering_designs/lr/LR_<joc>_cod<NN>.txt`` (o variantă pe linie,
poziții 1..n, poziția 1 = cel mai mic număr jucat) și
``covering_designs/lr/index_oficial.json`` (indexurile tipărite, pe care
`test_lr_schemes.py` le recalculează din fișiere). Se rulează offline; aplicația
citește numai fișierele scrise aici.

    python scripts/analysis/import_lr_schemes.py [--pdf-dir DIR] [--out DIR]

Fără ``--pdf-dir`` descarcă broșurile de pe loto.ro.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCES = {
    "649": "https://www.loto.ro/images/upload/pages/extras_brosura-649.pdf",
    "540": "https://www.loto.ro/images/upload/pages/extras_brosura_Loto540.pdf",
    "joker": "https://www.loto.ro/images/upload/pages/extras_brosura_joker.pdf",
}
# Bilet, univers, numere extrase.
GAMES = {"649": (6, 49, 6), "540": (5, 40, 6), "joker": (5, 45, 5)}
TITLES = {"649": "Loto 6/49", "540": "Loto 5/40", "joker": "Joker"}

WORD = re.compile(
    r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>'
)
INT = re.compile(r"^\d{1,3}(\.\d{3})*$|^\d+$")


# --------------------------------------------------------------------------- #
# Indexul recalculat din variante (folosit și de test)
# --------------------------------------------------------------------------- #
def _masks(variants) -> list[int]:
    return [sum(1 << (p - 1) for p in v) for v in variants]


def index_649(n: int, variants) -> Counter:
    """(numere ieșite din schemă, (cat. I, II, III, IV)) -> cazuri."""
    vm = _masks(variants)
    out: Counter = Counter()
    for k in (6, 5, 4, 3):
        for subset in itertools.combinations(range(n), k):
            sm = sum(1 << p for p in subset)
            hits = [(m & sm).bit_count() for m in vm]
            out[(k, tuple(hits.count(t) for t in (6, 5, 4, 3)))] += 1
    return out


def index_540(n: int, variants) -> Counter:
    """(ieșite din schemă, (5/5, 5/6, 4/6)) -> cazuri, cu al 6-lea extras enumerat.

    Categoria 5/5 cere cinci din primele cinci extrase: o variantă cu cinci
    numere prinse e 5/6 dacă al șaselea extras e printre ele, altfel 5/5.
    """
    vm = _masks(variants)
    out: Counter = Counter()
    for subset in itertools.combinations(range(n), 6):
        sm = sum(1 << p for p in subset)
        hits = [(m & sm).bit_count() for m in vm]
        four = hits.count(4)
        five = [m for m, h in zip(vm, hits) if h == 5]
        for x in subset:
            in_x = sum(1 for m in five if m >> x & 1)
            out[(6, (len(five) - in_x, in_x, four))] += 1
    for k in (5, 4):
        for subset in itertools.combinations(range(n), k):
            sm = sum(1 << p for p in subset)
            hits = [(m & sm).bit_count() for m in vm]
            if k == 5:
                five, four = hits.count(5), hits.count(4)
                out[(5, (five, 0, four))] += 1  # al 6-lea extras în afara schemei
                out[(5, (0, five, four))] += 1  # al 6-lea extras în schemă
            else:
                out[(4, (0, 0, hits.count(4)))] += 1
    return out


def joker_index_errors(n: int, variants, rows) -> list[str]:
    """Abaterile față de tabelul Joker fără numărul din câmpul 2.

    `rows` = [(ieșite, [(min, max) pe categoriile 2, 4, 6], procent)]. Fiecare
    model de câștig intră în primul rând ale cărui intervale îl cuprind;
    procentul rândului și intervalele trebuie să coincidă cu cele tipărite.
    """
    vm = _masks(variants)
    errors = []
    for k in (5, 4, 3):
        patterns = []
        for subset in itertools.combinations(range(n), k):
            sm = sum(1 << p for p in subset)
            hits = [(m & sm).bit_count() for m in vm]
            patterns.append(tuple(hits.count(t) for t in (5, 4, 3)))
        krows = [(tuple(map(tuple, rng)), pct) for kk, rng, pct in rows if kk == k]
        if not krows:
            errors.append(f"k={k}: fără rânduri")
            continue
        assigned = defaultdict(list)
        for pat in patterns:
            idx = next(
                (
                    i
                    for i, (rng, _) in enumerate(krows)
                    if all(lo <= x <= hi for x, (lo, hi) in zip(pat, rng))
                ),
                None,
            )
            if idx is None:
                errors.append(f"k={k}: modelul {pat} nu e în niciun rând")
                break
            assigned[idx].append(pat)
        for i, (rng, pct) in enumerate(krows):
            got = 100 * len(assigned[i]) / len(patterns)
            if abs(got - pct) > 0.011:
                errors.append(f"k={k} rândul {i}: {got:.2f}% față de {pct:.2f}%")
            for c in range(3):
                vals = [p[c] for p in assigned[i]]
                if vals and rng[c] != (0, 0) and (min(vals), max(vals)) != rng[c]:
                    errors.append(
                        f"k={k} rândul {i} categoria {c}: {min(vals)}-{max(vals)} "
                        f"față de {rng[c]}"
                    )
    return errors


def rows_sha256(variants) -> str:
    """Amprenta variantelor, în ordinea broșurii.

    Indexul tipărit nu fixează desfășurarea: o reetichetare a pozițiilor îl
    păstrează, iar la Joker tabelul pe intervale acceptă și câteva variante
    schimbate. Amprenta scrisă la import (după grilă, lista din stânga și
    index) fixează exact variantele verificate."""
    text = "\n".join(" ".join(str(int(x)) for x in v) for v in variants)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def index_errors(game: str, n: int, variants, rows) -> list[str]:
    """Diferențele dintre indexul recalculat și cel tipărit; [] = identic."""
    if game == "joker":
        return joker_index_errors(n, variants, rows)
    printed: Counter = Counter()
    for k, cols, cases, _pct in rows:
        printed[(int(k), tuple(int(c) for c in cols))] += int(cases)
    got = index_649(n, variants) if game == "649" else index_540(n, variants)
    if printed == got:
        return []
    diff = sorted(set(printed.items()) ^ set(got.items()))
    return [f"index diferit: {diff[:6]}"]


# --------------------------------------------------------------------------- #
# Citirea broșurii
# --------------------------------------------------------------------------- #
def _load_words(path: Path):
    text = path.read_text(encoding="utf-8")
    out = []
    for page_no, page in enumerate(text.split("<page ")[1:]):
        for x0, y0, x1, y1, t in WORD.findall(page):
            out.append((page_no, float(x0), float(y0), float(x1), float(y1), t.strip()))
    return out


def _lines(words, tol: float = 2.5):
    """Cuvintele grupate în rânduri (pagină, y), în ordinea de citire."""
    rows = []
    for w in sorted(words, key=lambda w: (w[0], (w[2] + w[4]) / 2, w[1])):
        yc = (w[2] + w[4]) / 2
        if rows and rows[-1][0] == w[0] and abs(rows[-1][1] - yc) <= tol:
            rows[-1][2].append(w)
        else:
            rows.append([w[0], yc, [w]])
    for r in rows:
        r[2].sort(key=lambda w: w[1])
    return rows


def _to_int(t: str) -> int:
    return int(t.replace(".", ""))


def _sections(rows):
    """(cod, rânduri) pentru fiecare titlu „SCHEMA REDUSĂ COD NN”."""
    starts = []
    for i, (_, _, ws) in enumerate(rows):
        toks = [w[5] for w in ws]
        for j in range(2, len(toks) - 1):
            if (
                toks[j] == "COD"
                and toks[j - 1].startswith("REDUS")
                and toks[j - 2].upper().startswith("SCHEMA")
                and INT.match(toks[j + 1])
            ):
                starts.append((i, int(toks[j + 1])))
    return [
        (code, rows[i : starts[n + 1][0] if n + 1 < len(starts) else len(rows)])
        for n, (i, code) in enumerate(starts)
    ]


def _meta(rows):
    """(numere jucate, variante, numere pe variantă) din antetul schemei."""
    n = v = in_variant = None
    for _, _, ws in rows[:12]:
        toks = [w[5] for w in ws]
        low = " ".join(toks).lower()
        nums = [t for t in toks if INT.match(t)]
        if "jucate" in low and nums:
            n = _to_int(nums[-1])
        elif "variante" in low and "schem" in low and "combina" not in low and nums:
            v = _to_int(nums[0])
        elif "variant" in low and ("^n" in low or "în" in low) and nums:
            in_variant = _to_int(nums[0])
    return n, v, in_variant


def _grid(rows, n: int, pick: int):
    """Variantele din grilă; antetul 1..n dă coloanele și se repetă pe pagini."""
    start = next(
        (
            i
            for i, (_, _, ws) in enumerate(rows)
            if any(w[5].startswith("DESF") for w in ws)
        ),
        None,
    )
    if start is None:
        return None, "fără desfășurare"
    header = None
    variants: dict[int, list[int]] = {}
    left_lists: dict[int, list[int]] = {}
    for _, _, ws in rows[start:]:
        toks = [w[5] for w in ws]
        ints = [(w, _to_int(w[5])) for w in ws if INT.match(w[5])]
        vals = [k for _, k in ints]
        for s in range(len(vals)):
            if (
                vals[s] == 1
                and vals[s : s + n] == list(range(1, n + 1))
                and not any(t.upper() == "X" for t in toks)
            ):
                header = [(w[1] + w[3]) / 2 for w, _ in ints[s : s + n]]
                break
        xs = [w for w in ws if w[5] in ("X", "x")]
        if not xs or header is None:
            continue
        first_x = min(w[1] for w in xs)
        left = [(w, k) for w, k in ints if w[3] < first_x]
        if not left:
            continue
        idx = max(left, key=lambda p: p[0][1])[1]
        cols = []
        for w in xs:
            xc = (w[1] + w[3]) / 2
            j = min(range(n), key=lambda c: abs(header[c] - xc))
            if abs(header[j] - xc) > 6:
                return None, f"X departe de coloană la rândul {idx}"
            cols.append(j + 1)
        if idx in variants:
            return None, f"rândul {idx} de două ori"
        variants[idx] = sorted(cols)
        # Joker: lista din stânga repetă varianta; o verificăm separat.
        if len(left) >= pick + 2:
            nums = [k for _, k in sorted(left, key=lambda p: p[0][1])]
            left_lists[idx] = sorted(nums[1 : 1 + pick])
    return (variants, left_lists), ""


def _index_rows(rows, ncat: int):
    """6/49 și 5/40: [(ieșite, categorii, cazuri, procent)]."""
    out = []
    k = None
    for _, _, ws in rows:
        toks = [w[5] for w in ws]
        if any(t.startswith("DESF") for t in toks):
            break
        toks = [t for t in toks if t != "sau"]
        if not toks or not all(
            INT.match(t) or t == "-" or re.match(r"^\d+,\d+$", t) for t in toks
        ):
            continue
        if not re.match(r"^\d+,\d+$", toks[-1]):
            continue
        if len(toks) == ncat + 3:
            k, toks = int(toks[0]), toks[1:]
        if len(toks) != ncat + 2 or k is None:
            continue
        cols = [0 if t == "-" else _to_int(t) for t in toks[:ncat]]
        out.append(
            [k, cols, _to_int(toks[ncat]), float(toks[ncat + 1].replace(",", "."))]
        )
    return out


def _index_rows_joker(rows):
    """Joker, tabelul fără numărul din câmpul 2: [(ieșite, intervale, procent)]."""
    start = next(
        (
            i
            for i, (_, _, ws) in enumerate(rows)
            if " ".join(w[5] for w in ws).startswith("INDEX")
            and "NU" in [w[5] for w in ws]
        ),
        None,
    )
    if start is None:
        return []
    data, ks = [], []
    for page, yc, ws in rows[start:]:
        toks = [w[5] for w in ws]
        if any(t.startswith("DESF") for t in toks):
            break
        if not all(
            INT.match(t)
            or t == "-"
            or re.match(r"^\d+-\d+$", t)
            or re.match(r"^\d+,\d+$", t)
            for t in toks
        ):
            continue
        if len(toks) == 1 and INT.match(toks[0]):
            ks.append((page, yc, int(toks[0])))
            continue
        # Procentul e ultimul: „23,81” sau „100”.
        if not re.match(r"^\d+(,\d+)?$", toks[-1]) or len(toks) not in (4, 5):
            continue
        k = None
        if len(toks) == 5:
            k, toks = int(toks[0]), toks[1:]
        rng = []
        for t in toks[:3]:
            if t == "-":
                rng.append([0, 0])
            elif "-" in t:
                a, b = t.split("-")
                rng.append([int(a), int(b)])
            else:
                rng.append([int(t), int(t)])
        data.append([page, yc, k, rng, float(toks[3].replace(",", "."))])
    for row in data:
        if row[2] is None:
            # Numărul ieșit stă pe rândul din mijlocul grupului: cel mai apropiat pe y.
            cand = [kk for kk in ks if kk[0] == row[0]]
            row[2] = min(cand, key=lambda kk: abs(kk[1] - row[1]))[2]
    return [[r[2], r[3], r[4]] for r in data]


def extract(game: str, bbox_path: Path):
    """Schemele cu variante întregi și codurile sărite.

    Întoarce ([(cod, n, variante, rânduri de index, erori)], [(cod, numere pe
    variantă)]). Sar numai schemele 6/49 cu variante de 2, 3 sau 4 numere, pentru
    combinatele compuse; broșurile 5/40 și Joker au numai variante întregi."""
    pick, _, _ = GAMES[game]
    out, skipped = [], []
    for code, sec in _sections(_lines(_load_words(bbox_path))):
        n, v, in_variant = _meta(sec)
        if in_variant is not None and in_variant != pick:
            skipped.append((code, in_variant))
            continue
        res, why = _grid(sec, n, pick)
        if res is None:
            out.append((code, n, [], [], [why]))
            continue
        var, left = res
        tickets = [var[i] for i in sorted(var)]
        errs = []
        if sorted(var) != list(range(1, (v or 0) + 1)):
            errs.append(f"{len(var)} rânduri față de {v}")
        if any(len(t) != pick or len(set(t)) != pick for t in tickets):
            errs.append("variantă cu alt număr de numere")
        if len({tuple(t) for t in tickets}) != len(tickets):
            errs.append("variante repetate")
        bad = [i for i in left if left[i] != var[i]]
        if bad:
            errs.append(f"lista din stânga diferă la rândurile {bad[:5]}")
        rows = (
            _index_rows_joker(sec)
            if game == "joker"
            else _index_rows(sec, 4 if game == "649" else 3)
        )
        errs += index_errors(game, n, tickets, rows)
        out.append((code, n, tickets, rows, errs))
    return out, skipped


def scheme_file_text(
    game: str, code: int, n: int, tickets, source: str, sha: str
) -> str:
    head = [
        f"# Loteria Romana, {TITLES[game]}, schema redusa predefinita Cod {code}",
        f"# numere: {n}",
        f"# variante: {len(tickets)}",
        f"# Sursa: {source} (sha256 {sha})",
        *(
            [
                "# Pozitia 1 = cel mai mic numar jucat, ultima = cel mai mare (brosura 6/49,",
                "# exemplul 3: numerele teoretice 1..n fata de numerele jucate, crescator).",
            ]
            if game == "649"
            else [
                "# Pozitia 1 = cel mai mic numar jucat, ultima = cel mai mare, ca la 6/49",
                "# (brosura 6/49, exemplul 3); brosura acestui joc nu spune ordinea.",
            ]
        ),
        "# Verificat exact pe indexul de castiguri tiparit (scripts/analysis/import_lr_schemes.py).",
    ]
    return "\n".join(head + [" ".join(map(str, t)) for t in tickets]) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--pdf-dir", type=Path, help="broșurile descărcate deja (numele din URL)"
    )
    ap.add_argument("--out", type=Path, default=ROOT / "covering_designs" / "lr")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    manifest, failed = [], 0
    with tempfile.TemporaryDirectory() as tmp:
        for game, url in SOURCES.items():
            name = url.rsplit("/", 1)[1]
            pdf = (args.pdf_dir / name) if args.pdf_dir else Path(tmp) / name
            if not args.pdf_dir:
                with urllib.request.urlopen(url, timeout=60) as resp:
                    pdf.write_bytes(resp.read())
            sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
            bbox = Path(tmp) / f"{game}.bbox.html"
            subprocess.run(["pdftotext", "-bbox", str(pdf), str(bbox)], check=True)
            schemes, skipped = extract(game, bbox)
            for code, per_variant in skipped:
                print(f"{game} Cod {code}: variante de {per_variant} numere — sărită")
            for code, n, tickets, rows, errs in schemes:
                status = "OK" if not errs else "; ".join(errs)
                print(
                    f"{game} Cod {code}: {n} numere, {len(tickets)} variante — {status}"
                )
                if errs:
                    failed += 1
                    continue
                (args.out / f"LR_{game}_cod{code}.txt").write_text(
                    scheme_file_text(game, code, n, tickets, url, sha), encoding="utf-8"
                )
                manifest.append(
                    {
                        "game": game,
                        "code": code,
                        "n": n,
                        "variants": len(tickets),
                        "source": url,
                        "sha256": sha,
                        "rows_sha256": rows_sha256(tickets),
                        "index": rows,
                    }
                )
    # O schemă pe linie: diferențele dintre două importuri rămân lizibile.
    (args.out / "index_oficial.json").write_text(
        "[\n"
        + ",\n".join(json.dumps(e, ensure_ascii=False) for e in manifest)
        + "\n]\n",
        encoding="utf-8",
    )
    print(f"{len(manifest)} scheme verificate, {failed} respinse")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

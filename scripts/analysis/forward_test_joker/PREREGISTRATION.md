# Test pe extrageri viitoare: `ens_win100+winall+ewma0.01`, Joker Urna 1

Înregistrat la 2026-10-11, înainte de extragerea de duminică seara. Regulile de
mai jos nu se schimbă pe durata testului; orice modificare invalidează testul.

## De unde vine ipoteza

Ecranul din 2026-10-10 (`scripts/analysis/method_screen_2026-10-10.md`) a
testat 1.002 scoreri noi pe 16 celule ale clasamentului. Pe Joker Urna 1, pool
8, 3+, cel mai bun candidat după z-ul evenimentului pe dezvoltare (primele 70%
din zilele de după warmup, 1.389 de extrageri) a fost
`ens_win100+winall+ewma0.01`: 65 de reușite, 4,68%, față de 3,27% la hazard.

Pe confirmare (ultimele 30%, 605 extrageri nevăzute la alegere) a dat 30 de
reușite, 4,96%, față de 19 (3,14%) la liderul celulei, `theta_drift`. Față de
hazard p brut 0,017; față de lider p brut 0,031 (a atins ținta în 20 de
extrageri în care liderul a ratat-o, invers de 9 ori). Pe loteriile 6/45
(Austria, Belgia, Ungaria) și EuroMillions 5/50: 477 de reușite față de 430
așteptate, p 0,010.

După corecția Holm peste cei 48 de candidați înghețați, p ajustat e 0,84 față
de hazard și 1,00 față de lider. Metoda nu a fost promovată. Cifrele de mai sus
sunt alese dintre 48 de candidați, deci sunt umflate de selecție; din 36 de
replicări externe, 3 aveau p sub 0,05, cât se așteaptă din întâmplare 1,8.
Testul de mai jos verifică afirmația pe extrageri pe care nimeni nu le-a văzut.

## Regula testată

- Metoda: copia înghețată `frozen_ens.py` (SHA-256 în
  `preregistration_2026-10-11.json`), verificată la înregistrare pe toate cele
  2.194 de extrageri din istoric: dă exact pool-ul de 8 al ecranului la fiecare
  extragere. Media rangurilor (rang mediu la egalitate) a trei trăsături pe
  zile de extragere: aparițiile în ultimele 100 de zile cu extrageri, aparițiile
  din tot istoricul și o medie mobilă exponențială a aparițiilor pe zi cu
  α = 0,01. Departajare: scor descrescător, apoi aparițiile din tot istoricul,
  apoi numărul mai mare.
- Pentru fiecare extragere Joker cu data **strict după 2026-10-11**, pool-ul se
  calculează numai din extragerile cu dată anterioară; extragerile din aceeași
  zi nu intră în istoric.
- Pool 8 = primele 8 numere din clasament. Numai Urna 1 (5 din 45); numărul
  Joker nu intră în test.
- Reușită = cel puțin 3 dintre cele 5 numere extrase sunt în pool.

## Decizia

Un test secvențial Wald (SPRT), dimensionat cu α = 0,025 (unilateral) și
β = 0,20:

| Pool | H0: aleator | H1: afirmat |
|---|---|---|
| 8 | 3,269% (hipergeometric exact) | 4,959% (30/605, confirmarea din ecran) |

- „Avantaj confirmat” = raportul de verosimilitate atinge pragul superior;
  „fără avantaj” = atinge pragul inferior.
- Plafon: 1.000 de extrageri noi (~9,5 ani). Nedecis la plafon, pool-ul
  primește testul binomial exact unilateral pe toate extragerile, cu α = 0,025;
  confirmă de la 45 de reușite. Dacă nu trece nici testul final, metoda e
  „neconfirmată”, nu „respinsă”.

Caracteristicile de operare sunt calculate exact, nu simulate
(`scripts/analysis/forward_test/sprt_operating.py`, pe fișierul de înregistrare
de aici), la ~105 extrageri pe an (Joker, 2021-2025: 102-107 pe an):

| | Pool 8 |
|---|---|
| Fără avantaj real: respins până la plafon | 89,8% |
| Fără avantaj real: jumătate din rulări respinse până la | 312 extrageri (~3,0 ani) |
| Fără avantaj real: confirmare falsă (SPRT / cu testul final) | 1,7% / 2,5% |
| Avantajul afirmat e real: confirmat până la plafon (SPRT / cu testul final) | 63,1% / 71,4% |
| Avantajul afirmat e real: respins fals | 18,3% |
| Avantajul afirmat e real: neconfirmat la plafon (nici prin testul final) | 10,3% |
| Avantajul afirmat e real: jumătate din rulări confirmate până la | 735 extrageri (~7,0 ani) |

Fără plafon, β = 0,20 ar da 80% putere; plafonul o coboară la 71,4%.

## Ce se verifică la fiecare rulare

`forward_test_joker.py` avertizează dacă `frozen_ens.py` s-a schimbat, dacă istoricul
de dinaintea înregistrării (2.194 de extrageri, ultima pe 2026-10-08) a fost
modificat sau dacă a apărut un rând datat între 2026-10-08 și 2026-10-11, când
nu era programată nicio extragere. Un rând nevalid (alt număr de valori decât
5 + Joker, valori repetate sau în afara intervalului, Joker în afara 1..20) sau
repetat oprește rularea. Ieșirea poartă amprentele SHA-256 ale regulii, ale
evaluatorului și ale înregistrării. Rezultatul se poate recalcula oricând din
istoric: regula e deterministă și folosește numai extragerile anterioare
fiecărei extrageri evaluate.

```powershell
D:\_BUILD\_LOTO\.venv\Scripts\python.exe scripts\analysis\forward_test_joker\forward_test_joker.py
```

## Replicare externă

Replicarea pe 6/45 și 5/50 a fost făcută în ecranul din 2026-10-10, înainte de
această înregistrare, pe istoricele complete (8.300 de extrageri): 477 de
reușite față de 430 așteptate, p 0,010. Nu e un test pe extrageri viitoare și
nu se repetă aici.

## Ce nu schimbă testul

Nimic în aplicație. Producția joacă la Joker Urna 1, pool 8, 3+, metoda din
decizia bench (`theta_drift` pe `folds.csv` versionat). Până la verdict, metoda
nu are avantaj demonstrat și nu se promovează.

## Amendamente

Niciunul. Un amendament e valabil numai înainte de prima extragere evaluată
(prima cu data după 2026-10-11) și se scrie aici, cu data lui, și în
`preregistration_2026-10-11.json`, în `amendments`.

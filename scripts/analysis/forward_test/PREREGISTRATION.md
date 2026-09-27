# Test pe extrageri viitoare: `dmd_forecast`, Loto 6/49

Înregistrat la 2026-09-27, înainte de extragerea de duminică seara. Regulile de
mai jos nu se schimbă pe durata testului; orice modificare invalidează testul.

## De unde vine ipoteza

Pe 2026-09-27 au fost testate 53 de metode (cele 50 din aplicație, `frequency`,
LightGBM și o rețea neuronală) pe 6/49, 5/40 și Joker, cu pool 6 și 12:
antrenare pe primele 70% din istoric, test pe ultimele 30%, 318 comparații.
Nicio metodă n-a trecut corecția pentru numărul de încercări. `dmd_forecast`
a avut cea mai bună cifră brută pe 6/49, cu scorurile recalculate o dată la 10
extrageri: 3,35% extrageri cu 3+ în pool 6 (aleator 1,86%) și 18,79% în pool 12
(aleator 14,80%).

Aceste cifre sunt alese dintre 318 rezultate, deci sunt umflate de selecție.
Recalculată înaintea fiecărei extrageri (cum lucrează aplicația), metoda dă
2,70% și 15,83% pe aceeași perioadă de test, iar pe cele 1.610 extrageri mai
vechi, nefolosite la alegere, 1,68% și 14,16%, adică sub aleator. Testul de mai
jos verifică totuși afirmația optimistă, pe extrageri pe care nimeni nu le-a
văzut.

## Regula testată

- Metoda: copia înghețată `frozen_dmd.py` (SHA-256 în
  `preregistration_2026-09-27.json`), identică la înregistrare cu
  `methods_wave2.score_dmd_forecast`; departajarea canonică: scor descrescător,
  apoi numărul mai mare.
- Pentru fiecare extragere 6/49 cu data **strict după 2026-09-27**, pool-ul se
  calculează numai din extragerile cu dată anterioară; extragerile din aceeași
  zi nu intră în istoric.
- Pool 6 și pool 12 = primele 6, respectiv 12 numere din clasament.
- Reușită = cel puțin 3 dintre numerele extrase sunt în pool.

## Decizia

Două teste secvențiale Wald (SPRT), unul pe pool, cu α = 0,0125 fiecare
(0,025 în total, unilateral) și β = 0,20:

| Pool | H0: aleator | H1: afirmat |
|---|---|---|
| 6 | 1,864% (hipergeometric exact) | 3,346% (26/777) |
| 12 | 14,798% | 18,790% (146/777) |

- „Avantaj confirmat” pentru un pool = raportul de verosimilitate atinge
  pragul superior; „fără avantaj” = atinge pragul inferior.
- Metoda e confirmată dacă cel puțin un pool e confirmat. E respinsă dacă ambele
  pooluri sunt respinse.
- Plafon: 1.000 de extrageri noi (~9,5 ani). Dacă un test nu s-a decis până
  atunci, se raportează testul binomial exact pe toate extragerile, cu α = 0,0125.

Durate simulate (4.000 de repetări), la ~105 extrageri pe an:

| | Pool 6 | Pool 12 |
|---|---|---|
| Fără avantaj real: mediana până la respingere | 263 extrageri (~2,5 ani) | 183 (~1,7 ani) |
| Fără avantaj real: confirmare falsă | 0,8% | 0,9% |
| Avantajul afirmat e real: mediana până la confirmare | 539 (~5,1 ani) | 438 (~4,2 ani) |

## Ce se verifică la fiecare rulare

`forward_test.py` avertizează dacă `frozen_dmd.py` s-a schimbat sau dacă
istoricul de dinaintea înregistrării (2.587 de extrageri, ultima pe 2026-09-24)
a fost modificat. Rezultatul se poate recalcula oricând din istoric: regula e
deterministă și folosește numai extragerile anterioare fiecărei extrageri
evaluate.

```powershell
D:\_BUILD\_LOTO\.venv\Scripts\python.exe scripts\analysis\forward_test\forward_test.py
```

## Replicare pe alte loterii 6/49 (fixată înainte de rezultate)

Aceeași copie înghețată, fără nicio ajustare, rulează pe istoricele complete ale
altor loterii 6 din 49: Germania 6aus49, Canada Lotto 6/49, Polonia Lotto, UK
Lotto (perioada cu 49 de bile, 1994–2015), Spania La Primitiva. Acele extrageri
nu au fost folosite nici la construirea metodei, nici la alegerea ei.

- Intră numai loteriile ale căror date au trecut verificarea pe o a doua sursă
  (cel puțin 20 de extrageri comparate, zero diferențe).
- Pentru fiecare loterie, evaluarea începe cu a 201-a extragere (fereastra
  metodei); pool-ul vine numai din extragerile cu dată anterioară.
- Analiza principală: toate loteriile la un loc, reușită = 3+ în pool, test
  binomial exact unilateral față de rata hipergeometrică, pool 6 și pool 12,
  α = 0,025 fiecare. Rezultatele pe fiecare loterie sunt secundare.
- Datele externe nu se comit; `external_replication.md` va lista sursele și
  amprenta SHA-256 a fiecărui fișier, ca rularea să poată fi refăcută.
- Script: `external_replication.py`.

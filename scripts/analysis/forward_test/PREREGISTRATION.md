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

Două teste secvențiale Wald (SPRT), unul pe pool, dimensionate cu α = 0,0125
fiecare (unilateral) și β = 0,20:

| Pool | H0: aleator | H1: afirmat |
|---|---|---|
| 6 | 1,864% (hipergeometric exact) | 3,346% (26/777) |
| 12 | 14,798% | 18,790% (146/777) |

- „Avantaj confirmat” pentru un pool = raportul de verosimilitate atinge
  pragul superior; „fără avantaj” = atinge pragul inferior.
- Metoda e confirmată dacă cel puțin un pool e confirmat. E respinsă dacă ambele
  pool-uri sunt respinse.
- Plafon: 1.000 de extrageri noi (~9,5 ani). Un pool nedecis la plafon primește
  testul binomial exact unilateral pe toate extragerile, cu α = 0,0125; la pool 6
  confirmă de la 30 de reușite, la pool 12 de la 175. Un pool care nu trece nici
  testul final rămâne „neconfirmat la plafon”, nu „respins”; fără niciun pool
  confirmat, metoda este atunci „neconfirmată”.

Caracteristicile de operare sunt calculate exact, nu simulate
(`sprt_operating.py`), la ~105 extrageri pe an:

| | Pool 6 | Pool 12 |
|---|---|---|
| Fără avantaj real: jumătate din rulări respinse până la | 263 extrageri (~2,5 ani) | 189 (~1,8 ani) |
| Fără avantaj real: confirmare falsă (SPRT / cu testul final) | 0,8% / 1,1% | 1,0% / 1,3% |
| Avantajul afirmat e real: confirmat până la plafon (SPRT / cu testul final) | 63,1% / 69,6% | 71,2% / 76,3% |
| Avantajul afirmat e real: respins fals | 18,7% | 19,0% |
| Avantajul afirmat e real: neconfirmat la plafon | 11,7% | 4,7% |
| Avantajul afirmat e real: jumătate din rulări confirmate până la | 743 (~7,1 ani) | 600 (~5,7 ani) |

Cifrele sunt pe fiecare pool. Fără plafon, β = 0,20 ar da 80% putere; plafonul o
coboară la valorile de mai sus.
Cu testul final, eroarea de tip I e 1,1% la pool 6 și 1,3% la pool 12;
pe ambele, cel mult 2,4% (suma lor).

## Ce se verifică la fiecare rulare

`forward_test.py` avertizează dacă `frozen_dmd.py` s-a schimbat, dacă istoricul
de dinaintea înregistrării (2.587 de extrageri, ultima pe 2026-09-24) a fost
modificat sau dacă a apărut un rând datat între 2026-09-24 și 2026-09-27, când
nu era programată nicio extragere. Un rând nevalid (alt număr de valori decât 6,
valori repetate sau în afara intervalului) sau repetat oprește rularea. Ieșirea
poartă amprentele SHA-256 ale regulii, ale evaluatorului și ale înregistrării.
Rezultatul se poate recalcula oricând din istoric: regula e deterministă și
folosește numai extragerile anterioare fiecărei extrageri evaluate.

```powershell
D:\_BUILD\_LOTO\.venv\Scripts\python.exe scripts\analysis\forward_test\forward_test.py
```

## Replicare pe alte loterii 6/49 (fixată înainte de rezultate)

Aceeași copie înghețată, fără nicio ajustare, rulează pe istoricele complete ale
altor loterii 6 din 49: Germania 6aus49, Canada Lotto 6/49, Polonia Lotto, Spania
La Primitiva. Acele extrageri nu au fost folosite nici la construirea metodei,
nici la alegerea ei.

- Intră numai loteriile ale căror date au trecut verificarea pe o a doua sursă
  (cel puțin 20 de extrageri comparate, zero diferențe).
- Pentru fiecare loterie, evaluarea începe cu a 201-a extragere (fereastra
  metodei); pool-ul vine numai din extragerile cu dată anterioară.
- Analiza principală: toate loteriile la un loc, reușită = 3+ în pool, test
  binomial exact unilateral față de rata hipergeometrică, pool 6 și pool 12,
  α = 0,0125 fiecare. Rezultatele pe fiecare loterie sunt secundare.
- Datele stau în `_ISTORIC/externe/`, cu sursa și verificarea fiecărui fișier în
  README-ul folderului. Rezultatul, cu amprenta SHA-256 (pe LF) a fiecărui fișier
  folosit, se trece în `external_replication.md` din ieșirea `--json`.
- Script: `external_replication.py`.

## Amendamente

Toate sunt anterioare primei extrageri evaluate (prima cu data după
2026-09-27) și anterioare oricărui rezultat al replicării. Parametrii SPRT,
pool-urile, ținta și data de start nu s-au schimbat.

- 2026-09-27, după revizia independentă a protocolului:
  - evaluatorul aplică testul final la plafon și verdictul combinat, scrise în
    protocol de la început, dar neimplementate;
  - tabelul de durate e recalculat exact. Varianta inițială dădea, sub eticheta
    „până la confirmare”, mediana până la orice decizie (539 și 438 de extrageri)
    și nu spunea cât coboară puterea din cauza plafonului;
  - evaluatorul oprește rularea la un rând nevalid sau repetat și semnalează un
    rând datat între ultima extragere înregistrată și data de start;
  - JSON-ul de înregistrare păstrează scorurile metodei la înregistrare. Copia
    înghețată se verifică pe ele, nu pe metoda din aplicație, care se poate
    schimba;
  - replicarea: α coborât de la 0,025 la 0,0125 per pool, ca verdictul pe două
    pooluri să nu depășească 2,5% eroare de tip I; datele sunt citite cu dată
    calendaristică, nu ca text, iar un rând nevalid oprește rularea; datele se
    păstrează în repo, în `_ISTORIC/externe/`;
  - Marea Britanie iese din replicare. Jocul actual e 6 din 59 (din octombrie
    2015), deci nu e o loterie 6/49; arhiva cu 49 de bile nu se păstrează.
- 2026-09-27, după a doua verificare independentă, tot înainte de rularea
  replicării și de prima extragere evaluată:
  - un pool nedecis la plafon și nesemnificativ la testul final este
    „neconfirmat la plafon”, iar metoda fără niciun pool confirmat este
    „neconfirmată”; „respinsă” rămâne numai pentru ambele pool-uri respinse prin
    SPRT;
  - un rând cu alt număr de valori decât 6 oprește evaluarea;
  - după verdictul final, scriptul nu mai anunță pool-ul pentru următoarea
    extragere;
  - setul replicării: cele patru fișiere 6/49 numite în `external_replication.md`
    (Germania, Canada, Polonia, Spania), cu amprentele lor.

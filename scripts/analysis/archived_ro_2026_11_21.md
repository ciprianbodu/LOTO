# Retestarea catalogului LOTO arhivat · 21 noiembrie 2026

Am retestat cele 55 de metode din catalogul istoric, inclusiv `markov_lag2`, reactivată între timp. Ceilalți 54 de scoreri sunt încă arhivați. Cele trei metode compacte și controalele `frequency` și `random` duc totalul la 60 de metode distincte evaluate. Am folosit toate cele 33 de scheme reduse oficiale LR din aplicație și am testat separat top 1 și top 5 pentru Joker Urna 2. [Tabelul complet, 3.900 de rânduri](archived_ro_2026_11_21.csv) · [Inventarul și amprentele arhivelor](archived_ro_2026_11_21.json)

## Metode alese înaintea perioadei de test

În tabel, o reușită la urnele principale înseamnă că cel puțin una dintre variantele codului LR a prins minimum patru numere principale la extragerea respectivă. Coloana «Alegere între metodele retrase» fixează metoda cu cele mai multe astfel de extrageri în perioada de selecție, dintre cele 54 încă arhivate. «Alegere din catalogul actual» aplică aceeași regulă celor trei metode compacte, lui Markov și frecvenței. La Joker Urna 2 se numără potrivirile cu prima bilă sau cu lista de cinci bile. Lista de cinci este informativă, iar prima bilă este cea jucată.

| Joc · pool / cod LR | Extrageri de test | Alegere între metodele retrase | Alegere din catalogul actual | Markov fix | Martor aleator |
|---|---:|---|---|---:|---:|
| 6/49 · 9 / 48 | 719 | `vlmm_self_k3` · 6 | `compact_transition_prior` · 3 | 6 | 2 |
| 6/49 · 16 / 59 | 719 | `runs_persistence` · 54 | `markov_lag2` · 57 | 57 | 56 |
| 5/40 · 8 / 16 | 465 | `pair_lift_last` · 9 | `frequency` · 3 | 6 | 5 |
| 5/40 · 16 / 37 | 465 | `drift_linear` · 82 | `compact_multiscale_shrink` · 66 | 74 | 65 |
| 5/40 · 19 / 48 | 465 | `cusum_burst` · 128 | `frequency` · 140 | 127 | 125 |
| Joker Urna 1 · 10 / 24 | 592 | `bayes_dirichlet_forget` · 2 | `frequency` · 3 | 3 | 3 |
| Joker Urna 1 · 15 / 12 | 592 | `ssa_forecast` · 1 | `compact_multiscale_shrink` · 11 | 15 | 8 |
| Joker Urna 2 · top 1 | 592 | `runs_persistence` · 27 | `markov_lag2` · 31 | 31 | 27 |
| Joker Urna 2 · top 5 | 592 | `season_period2` · 147 | `frequency` · 145 | 156 | 143 |

Pentru **categoria a IV-a la 6/49** am verificat separat pragul 3+. Cu pool 16 și Cod 59, `ridge_pooled_feats`, aleasă din catalogul retras folosind numai perioada de selecție, a avut cel puțin o variantă cu 3+ la **237 din 719 extrageri**. Markov a avut 216, frecvența 223, iar martorul aleator 218. Am verificat și categoria exactă pentru cele patru metode. În fiecare extragere din acele numărători a existat o variantă cu exact trei numere.

La **5/40 cu pool 16 și Cod 37**, `drift_linear` a avut 86 de extrageri cu 4+ în pool și 82 cu 4+ pe cel puțin o variantă, față de 70 și 65 pentru martorul aleator. Așteptările calculate din geometrie sunt 74,52 și 69,34. La **pool 19 și Cod 48**, frecvența a avut 140 de extrageri cu 4+ pe o variantă, iar `cusum_burst` a avut 128. Numărătorile privesc schemele simulate.

## Protocol și semnificație statistică

Prima prognoză are cel puțin 250 de extrageri în istoric. Pentru fiecare zi de tragere am folosit un singur prefix, încheiat înaintea zilei. Primele 70% dintre zilele eligibile servesc selecției metodei. Pe ultimele 30%, identitatea metodei selectate rămâne fixă, iar scorurile se regenerează din istoricul disponibil la fiecare dată. Perioadele de test încep la 07.11.2019 pentru 6/49, 03.04.2022 pentru 5/40 și 08.04.2021 pentru Joker. Toate se încheie la 19.11.2026.

Fiecare pool principal folosește maximum două numere consecutive. Pragurile de pool sunt cumulative și folosesc toate bilele extrase. Pentru 5/40, o variantă are cinci locuri. Șansa teoretică de reușită este hipergeometrică pentru pool, exactă pe aranjamentul variantelor oficiale pentru bilete și K/20 pentru Joker Urna 2. Coloanele «only» din CSV numără extragerile în care numai una dintre cele două metode a reușit. Pragul la care se referă acele coloane apare în `selection_and_pairing_threshold`.

Cele opt metode cu scoruri plate la fiecare dată în Urna 2 au rânduri `no_own_forecast`, fără numărătoare proprie la acest joc. În urnele principale, toate metodele au produs scoruri valabile pe toate datele evaluate.

Niciuna dintre comparațiile cu hazardul nu a trecut pragul de 5% după corecția Holm. La pragul principal, analiza a corectat 3.522 de comparații ale metodelor individuale și separat 122 de rezultate cu metoda fixată înaintea testului. Cel mai mic p brut pentru o metodă individuală a fost 0,000278 pentru `bayes_dirichlet_forget`, cu 39 de extrageri față de 21,30 așteptate la Joker Urna 1, pool 15. După corecție, p = 0,979. Am verificat separat familiile de 3+ și de 5+/6. Rezultatele sunt retrospective, deoarece perioadele istorice au fost analizate în studii anterioare.

## Selecție dinamică sau metodă fixă

Am comparat aceeași bancă în ambele moduri. Metoda fixă se alege pe primele 70% dintre zile după 4+ în pool. Selectorul dinamic reface alegerea înaintea fiecărei zile, folosind cel mult 104 zile deja încheiate, cu cel puțin 26 pentru selecție. Am păstrat regula v5 față de martorul aleator. Cifrele privesc ultimele 30% dintre zile, pe aceleași extrageri și la același buget LR.

| Bancă de metode | Criteriu | Dinamic mai bun | Egalitate | Fix mai bun |
|---|---|---:|---:|---:|
| Activă · 5 candidați | 4+ în pool · 26 dimensiuni | 14 | 3 | 9 |
| Activă · 5 candidați | 4+ pe cel puțin o variantă · 33 coduri LR | 20 | 1 | 12 |
| Extinsă · 59 de candidați | 4+ în pool · 26 dimensiuni | 9 | 4 | 13 |
| Extinsă · 59 de candidați | 4+ pe cel puțin o variantă · 33 coduri LR | 12 | 3 | 18 |

De exemplu, la 6/49 Cod 59, banca activă are 58 de extrageri cu 4+ pe o variantă în regim dinamic și 57 în regim fix. Banca extinsă are 44 și 54. La 5/40 Cod 37, perechile sunt 70 și 66, respectiv 67 și 82. La Joker Cod 12, banca extinsă are 13 și 1, în condițiile în care hazardul ar produce în medie 9,35. Codurile cu aceeași dimensiune folosesc aceleași pool-uri, deci numărul de coduri câștigătoare este descriptiv.

La Joker Urna 2, într-un experiment separat pe top 1, banca activă a obținut 23 de bile corecte dinamic și 31 cu metoda fixă. Banca extinsă, cu 51 de candidați care au produs scoruri proprii, a avut 36 și 27. În aplicație, urna a doua folosește decizia ei fixă.

Rezultatul susține păstrarea selectorului v5 cu banca restrânsă. Simularea cu toate metodele istorice arată mai des pierderi decât câștiguri la pragul ales. [Comparația pe fiecare configurație](dynamic_vs_fixed_ro_2026_11_21.json) · [Scriptul de reproducere](dynamic_vs_fixed_ro_2026_11_21.py)

## Rețetele selectorului dinamic anterior

Am retestat separat cele 1.000 de rețete vechi pe cele șapte scheme de referință pentru urnele principale. Nicio pereche rețetă–schemă nu a trecut pragul de 5% după corecția pentru cele 7.000 de comparații de 4+ în pool sau pe o variantă. Am refăcut și selecția dinamică pentru versiunile v1–v5. La 6/49 cu pool 16, v3 și v5 au avut câte 62 de reușite 4+ în pool. La 5/40 cu pool 16 au avut 81 și 78, iar la pool 19 au avut 146 și 141.

## Reproducere

Rulare din rădăcina proiectului, cu dependențele aplicației instalate.

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python -m scripts.analysis.archived_ro_2026_11_21 --jobs 3 --output archived_replay.csv
```

Rularea scrie CSV-ul de rezultate și un JSON cu inventarul și amprentele arhivelor. [Scriptul](archived_ro_2026_11_21.py)

Rularea completă de la zero a reprodus identic toate cele 3.900 de rânduri din CSV și fișierul JSON generate cu traiectoriile verificate independent. Am confruntat separat 1.380 de totaluri de pe cele șapte scheme de referință și 1.056 de rezultate generate prin enumerarea variantelor oficiale, cu acoperire pentru toate cele 33 de coduri.

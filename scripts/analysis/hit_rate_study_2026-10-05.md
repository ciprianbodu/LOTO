# Rate de hit mai mari: predicție și geometria biletelor, 2026-10-05

## Concluzie

1. **Predicție: nicio metodă nu crește hiturile în pool.** Am testat 491 de
   scoreri noi, în 16 familii, pe cele patru jocuri și pe câte patru mărimi
   de pool: 6.355 de teste. Pe fiecare joc, cel mai bun rezultat din
   dezvoltare nu depășește ce găsește aceeași căutare pe extrageri pur
   aleatoare. P-ul căutării este între 0,55 și 0,67. Cei 20 de candidați
   înghețați nu se confirmă pe ultimele 30% din istoricul românesc (Holm
   minim 0,228). Nici nu se replică pe 30.067 de extrageri 6/49 străine și
   pe 8.290 de extrageri 6/45 și 5/50. Nu se promovează nimic.
2. **Geometrie: la același număr de variante, dispersia e mai bună la
   fiecare prag.** Variantele care se suprapun cât mai puțin, pe tot
   universul jocului, au o probabilitate exact mai mare ca cel puțin una să
   câștige. Diferența e mare la 3+, mai mică la 4+ și dispare la marele
   premiu. Rezultatul e demonstrat prin enumerarea tuturor extragerilor
   posibile, nu prin simulare. A fost implementat ca opțiune în „Bilet
   complet”, implicit oprită.

Nicio parte nu schimbă numărul mediu de variante câștigătoare: el este
`B × P(o variantă ≥ t)` pentru orice aranjare (liniaritatea mediei).
Geometria schimbă numai cât de des cade **cel puțin** un câștig.

## Partea 1. Ecran predictiv

Script: `prediction_screen_2026-10-05.py`; rezultate:
`prediction_screen_2026-10-05.json` (621 s pe 32 de nuclee).

### Protocol (fixat înainte de rulare, în docstring-ul scriptului)

- Jocuri: 6/49 (n1..n6), 5/40 (cele 6 extrase), Joker Urna 1 (5/45), Joker
  Urna 2 (1/20, top-1). Ordinea de extragere este păstrată, pentru scorerii
  de ordine.
- Tăiere pe zi: scorul unei zile vede numai zilele anterioare, inclusiv
  când într-o zi sunt mai multe extrageri. Primele 200 de zile sunt warmup.
  Dezvoltarea sunt primele 70% din zilele rămase; confirmarea sunt ultimele
  30%, citite numai pentru selecția înghețată.
- Egalitățile de scor se rup după frecvența totală anterioară, apoi după
  numărul mai mare. Un candidat la care locul K cade într-un grup de scoruri
  egale în peste 50% din rânduri iese din selecție.
- Metrica: z-ul hiturilor totale în pool față de media și varianța
  hipergeometrice exacte. Secundar: z-ul evenimentului țintă (6/49 3+,
  5/40 4+, Joker 3+, Urna 2 numărul exact). „Stabil” înseamnă exces pozitiv
  pe ambele jumătăți ale dezvoltării.
- **Nulul căutării:** 200 de istorii sintetice uniforme și independente, cu
  aceeași structură de zile. Pe fiecare se reface tot ecranul (toți
  candidații, toate pool-urile) și se reține maximul z.
- Selecția înghețată: pe fiecare joc, primii 3 candidați stabili după z-ul
  hiturilor, apoi primii 2 după z-ul evenimentului.
- Confirmarea: test binomial exact, unilateral, pe ultimele 30% din
  istoricul românesc, cu corecție Holm pe toate cele 20 de teste. Apoi
  replicarea externă cu aceeași formulă și același K: 6/49 pe Germania,
  Cehia, Slovacia, Polonia, Spania și Bulgaria; Urna 1 pe 6/45 (Austria,
  Belgia, Ungaria) și pe EuroMillions 5/50.
- Promovarea cere ambele: Holm < 0,05 în România și p < 0,05 pe datele
  externe concatenate.

### Familii de scoreri

16 familii (numărul de scoreri între paranteze, la 6/49). Docstring-ul
protocolului spune „17 familii”, dar lista lui are aceleași 16 elemente.

- frecvență pe 13 ferestre (26);
- EWMA la 10 rate (20);
- goluri, raportul golului și hazard (6);
- impuls, adică fereastră scurtă minus lungă (14);
- Markov pe decalajele 1, 2, 3, 4, 7 și combinat (12);
- co-apariție cu extragerile anterioare (4);
- **geometria pe grila biletului (288)**: 8 așezări (rânduri de 5..10
  coloane, coloane de 7 și 10 rânduri), 6 vecinătăți (rege, turn, rând,
  coloană, diagonală, cal) și 3 memorii;
- relații pe linia numerelor (56): distanțe fixe, oglindă, cifre inversate,
  aceeași ultimă cifră, aceeași decadă, același rest modulo 3/4/7;
- sume și diferențe de perechi (4);
- ordinea bilelor: poziție, primele extrase (14);
- calendar: ziua săptămânii, luna (4);
- condiționare pe tercila sumei, amplitudinii, parității, numerelor mici,
  consecutivelor și golului maxim din ziua precedentă (12);
- periodicitate 2..28 (20);
- AR comun (5);
- regresie logistică online (2);
- ansambluri (4).

Majoritatea apar și cu semn opus („anti_”).

Unele formule coincid: așezarea pe coloane este transpusa celei pe rânduri,
deci aceeași vecinătate simetrică, sau rândul schimbat cu coloana, dă același
clasament. Din 491 de nume, 411 dau clasamente distincte la jocurile cu pool
(383 din 467 la Urna 2). Trei perechi de nume identice apar și printre cei 20
de candidați înghețați, deci rămân 17 reguli distincte.

### Dezvoltare față de nulul căutării

| Joc | Extrageri / zile | Teste | Excluși la egalități | Cel mai bun z | q95 nul | p căutare |
|---|---:|---:|---:|---:|---:|---:|
| 6/49 | 2590 / 2521 | 1964 | 0 | 3,14 | 3,88 | 0,552 |
| 5/40 | 1741 / 1722 | 1964 | 14 | 3,05 | 3,95 | 0,667 |
| Joker Urna 1 | 2193 / 2130 | 1960 | 52 | 3,00 | 3,86 | 0,662 |
| Joker Urna 2 | 2193 / 2130 | 467 | 0 | 2,90 | 3,76 | 0,577 |

Pe evenimentul țintă: 6/49 3,35 față de 4,05 (p 0,542), 5/40 3,38 față de
4,35 (p 0,612), Urna 1 3,86 față de 3,97 (p 0,095), Urna 2 identic cu hiturile.

Calibrarea confirmă nulul. Pe istoriile sintetice, z-urile candidaților au
media aproximativ 0 și abaterea aproximativ 0,98–0,99. Pe istoricul real,
media e tot aproximativ 0 (−0,005..0,114), cu abaterea 0,93–1,07. Un semnal
real ar împinge distribuția reală spre dreapta; nu o face.

### Confirmare (ultimele 30% românești) și replicare externă

| Joc | Candidat | K | Confirmare | Aleator | p | Holm | Extern obs./aștept. (p) |
|---|---|---:|---:|---:|---:|---:|---|
| 6/49 3+ | anti_hazard | 9 | 53/731 = 7,25% | 6,67% | 0,286 | 1 | 1934 / 2006,8 (0,954) |
| | anti_grid_row6_diag_ewma0.3 | 12 | 101/731 = 13,82% | 14,80% | 0,787 | 1 | 4493 / 4449,3 (0,239) |
| | anti_line_dist10_ewma0.3 | 12 | 131/731 = 17,92% | 14,80% | 0,011 | 0,228 | 4426 / 4449,3 (0,647) |
| | anti_grid_row6_knight_ewma0.02 | 16 | 213/731 = 29,14% | 29,81% | 0,667 | 1 | 8931 / 8962,8 (0,656) |
| | line_dist2_ewma0.3 | 12 | 97/731 = 13,27% | 14,80% | 0,889 | 1 | 4508 / 4449,3 (0,170) |
| 5/40 4+ | grid_row9_knight_last | 10 | 9/476 = 1,89% | 2,58% | 0,866 | 1 | — |
| | line_dist7_ewma0.3 | 12 | 26/476 = 5,46% | 5,48% | 0,534 | 1 | — |
| | anti_grid_row5_diag_last | 16 | 84/476 = 17,65% | 16,03% | 0,183 | 1 | — |
| | grid_col7_row_ewma0.02 (= grid_row7_col) | 10 | 13/476 = 2,73% | 2,58% | 0,458 | 1 | — |
| Urna 1 3+ | anti_grid_row8_king_ewma0.3 | 11 | 46/604 = 7,62% | 8,53% | 0,809 | 1 | 1080 / 1063,6 (0,294) |
| | grid_col7_row_ewma0.02 (= grid_row7_col) | 8 | 27/604 = 4,47% | 3,27% | 0,066 | 1 | 388 / 429,6 (0,980) |
| | anti_markov4 | 8 | 13/604 = 2,15% | 3,27% | 0,959 | 1 | 457 / 429,6 (0,087) |
| | anti_grid_col7_col_ewma0.02 | 11 | 44/604 = 7,28% | 8,53% | 0,881 | 1 | 1117 / 1063,6 (0,039) |
| Urna 2 top-1 | grid_row8_diag_ewma0.3 | 1 | 30/604 = 4,97% | 5,00% | 0,541 | 1 | — |
| | anti_grid_col10_knight_ewma0.02 (= row10) | 1 | 40/604 = 6,62% | 5,00% | 0,046 | 0,869 | — |
| | grid_row9_col_ewma0.02 | 1 | 35/604 = 5,79% | 5,00% | 0,208 | 1 | — |
| | grid_row6_king_last | 1 | 23/604 = 3,81% | 5,00% | 0,930 | 1 | — |

Cel mai bun rezultat românesc (`anti_line_dist10_ewma0.3`, 6/49 pool 12:
17,92% față de 14,80%) nu trece Holm și nu se replică în străinătate: acolo
dă 4426 de evenimente, sub cele 4449 așteptate la întâmplare. Singurul
p < 0,05 extern (`anti_grid_col7_col_ewma0.02`, 6/45) are confirmarea
românească sub aleator. Urna 2 nu are istorice externe cu aceeași geometrie.

### Despre ecranul anterior din aceeași zi

`spatial_math_screen_2026-10-05.md` a folosit ca nul permutarea etichetelor
1..49. Pentru scorerii echivarianți la etichete (frecvență, EWMA, goluri),
permutarea lasă hiturile identice, deci nu e un nul pentru ei. Pentru
nucleele spațiale testează numai structura spațială. Concluzia lui negativă
rămâne valabilă. Ecranul de aici folosește nulul corect pentru orice scorer:
istorii uniforme sintetice, cu reluarea completă a căutării.

## Partea 2. Geometria numerelor pe bilet

Script: `ticket_geometry_2026-10-05.py`; rezultate:
`ticket_geometry_2026-10-05.json` (81 s).

### Întrebarea și metoda

La un număr fix de variante B, ce aranjare dă cea mai mare probabilitate ca
**cel puțin o** variantă să prindă t numere? Toate probabilitățile de mai
jos sunt exacte. Se enumeră toate extragerile posibile ca măști de biți:
13.983.816 la 6/49, 3.838.380 la 5/40 și 1.221.759 la Joker. Pentru fiecare
extragere se calculează maximul pe variante. Valorile coincid cu
`covering.probability.wheel_hit_probabilities` pe fiecare configurație de
producție verificată.

- **Producție:** `generate_wheel` exact ca în aplicație (hitcover cu schimburi
  de profil la buget, La Jolla fără plafon), pe pool-uri 6..16 și garanții
  2..4. Pentru fiecare prag se păstrează cea mai bună combinație (K, g) cu
  cel mult B variante.
- **„Bilet complet” actual:** `full_ticket._fill_variants` pe pool-ul
  implicit 10, cu garanția 4.
- **Dispersie:** `covering.spread.spread_variants`, codul livrat în aplicație.
- **Plafon teoretic:** `B × P(o variantă ≥ t)`. Este atins exact când două
  variante nu pot câștiga împreună, adică au cel mult `2t − extrase − 1`
  numere comune: la 6/49 4+, cel mult 1; la Joker 3+, 0; la 5/40 4+, cel
  mult 1.

### Rezultat: dispersia domină la fiecare buget, prag și joc

Pe toate cele 41 de bugete testate (1..40 de variante), dispersia este cel
puțin la fel de bună ca **cea mai bună** configurație de producție, la
**fiecare** prag (3+, 4+, 5+). Câteva rânduri, în procente:

| Joc, prag | Variante | „Bilet complet” (pool 10) | Cea mai bună producție | Dispersie | Plafon |
|---|---:|---:|---:|---:|---:|
| 6/49 3+ | 3 | 4,93 | 5,53 | 5,58 | 5,59 |
| | 9 | 8,77 | 13,43 | 16,54 | 16,77 |
| | 15 | 9,03 | 19,06 | 26,57 | 27,96 |
| | 30 | 9,03 | 27,00 | 48,40 | 55,91 |
| 6/49 4+ | 9 | 0,723 | 0,880 | 0,888 | 0,888 |
| | 30 | 1,185 | 2,587 | 2,961 | 2,961 |
| 5/40 4+ | 12 | 0,864 | 0,936 | 0,941 | 0,941 |
| | 20 | 1,336 | 1,547 | 1,568 | 1,568 |
| | 40 | 2,220 | 3,017 | 3,137 | 3,137 |
| Joker 3+ | 6 | 3,53 | 3,89 | 3,93 | 3,93 |
| | 10 | 5,10 | 6,12 | 6,53 | 6,55 |
| | 20 | 6,47 | 10,68 | 12,91 | 13,10 |

Configurația implicită fără plafon (pool 10, garanție 4, cover complet),
comparată cu același număr de variante dispersate:

| Joc | Variante | Prag | Cover complet | Dispersie |
|---|---:|---|---:|---:|
| 6/49 | 20 | 3+ / 4+ / 5+ | 9,03 / 1,185 / 0,0349 | 34,31 / 1,974 / 0,0370 |
| 5/40 | 51 | 4+ / 5+ | 2,58 / 0,0452 | 4,00 / 0,0465 |
| Joker | 51 | 3+ / 4+ | 6,47 / 0,622 | 31,63 / 0,839 |

De ce: variantele dintr-un pool de 10 se suprapun mult. Când pool-ul prinde
3 numere, multe variante câștigă **împreună**; când nu le prinde, nu câștigă
niciuna. Media e aceeași, dar câștigurile se adună pe mai puține extrageri.
Pe 6/49, 3+ în pool-ul de 10 apare în 9,03% din extrageri; peste acest
plafon nu trece niciun aranjament în interiorul pool-ului.

### Ce costă și ce nu schimbă

- **Garanția pool-ului:** variantele dispersate nu mai garantează „dacă ies 4
  numere din pool, ai un 4”. Garanția valorează ceva numai dacă pool-ul
  prezice, iar Partea 1 arată că nu prezice. Pe extrageri uniforme,
  dispersia câștigă la fiecare prag.
- **Câștigurile multiple la aceeași extragere** devin mai rare: media
  rămâne aceeași, iar P(cel puțin unul) crește.
- **Marele premiu** are exact aceeași șansă pentru orice B variante distincte.
- **Limita de consecutive** se aplică pe fiecare variantă și nu costă nimic
  măsurabil. Cu cel mult 2 numere consecutive, diferențele sunt sub
  0,01 puncte procentuale la 1, 3, 5 și 10 bilete, pe toate jocurile.
- **Restrângerea bazei** micșorează universul, deci și dispersia posibilă.
  Varianta respectă intervalul; șansele afișate sunt cele exacte pentru el.
- **Timp:** căutarea durează cel mult 0,7 s (40 de variante la 5/40, cu
  limita de consecutive). Calculul exact al șanselor durează cel mult 0,3 s
  la 30 de variante 6/49.

## Implementare

- `covering/spread.py`:
  - `spread_variants` face o căutare locală deterministă, cu 4 reporniri, pe
    probabilitatea exactă ca două variante să câștige împreună
    (`pair_joint_probability`). Ponderea 1 revine pragului țintă, 1e-3
    pragurilor mai mari și 1e-9 celor mai mici, care servesc doar la
    departajare.
  - Folosește întâi numerele cele mai bine clasate.
  - Respectă `max_run` pe fiecare variantă.
  - `ticket_hit_probabilities` dă probabilitatea exactă că cel puțin o
    variantă prinde t numere, prin enumerare.
- `loto_enterprise/core/full_ticket.py`:
  - `build_full_ticket(..., spread=True)` construiește universul în ordine:
    întâi pool-ul afișat, apoi restul clasamentului metodei, apoi celelalte
    numere permise, cu numărul mai mare primul.
  - Respectă restrângerea bazei, limita de consecutive și numărul Joker.
  - Cu `compare=True`, întoarce șansele exacte ale ambelor moduri pentru
    pragurile cu premiu.
- UI: bifa „🎯 Variante dispersate pe bilet” (`full_ticket_spread_val`,
  implicit oprită, persistată). Dialogul afișează șansa exactă a modului
  ales, șansa celuilalt mod pentru același număr de variante și nota despre
  medie și marele premiu.
- Nu schimbă scorerul, pool-ul, wheel-ul principal, bench-ul, walk-forward-ul
  sau payload-ul cozii. „Bilet complet” se calculează la cerere, deci nu e
  nevoie de bump de cache.
- Teste: `test_spread_tickets.py`. Verifică evaluatorul exact față de
  profilul pool-ului, probabilitatea perechilor prin forță brută, atingerea
  plafonului la 4+ și dominanța la fiecare prag (trei jocuri × 1/3/10
  bilete). Mai verifică ordinea pool-ului, restrângerea, Joker, limita de
  consecutive, implicitul oprit și textele.

## Reproducere

```powershell
$env:PYTHONPATH="."
D:\_BUILD\_LOTO\.venv\Scripts\python.exe scripts\analysis\prediction_screen_2026-10-05.py
D:\_BUILD\_LOTO\.venv\Scripts\python.exe scripts\analysis\ticket_geometry_2026-10-05.py
```

Ambele scripturi sunt deterministe (seed fix) și nu scriu stare de aplicație.
Nu există nicio afirmație predictivă: probabilitatea unui pool de a prinde
numere rămâne cea hipergeometrică.

# Audit și mai multe hituri pe bilete, runda a treia, 2026-10-16

Cerința: audit pe ultimul `main` și cod care să dea mai multe hituri, cu accent
pe pool-ul extins (la 6/49, maximum 16). Rata pool-ului rămâne hipergeometrică:
pe extrageri uniforme, orice 16 numere prind 4+ în 7,96% din extrageri la 6/49.
Codul poate mișca numai așezarea numerelor pe bilete, la același pool și la
același număr de bilete. Toate cifrele de mai jos sunt probabilități exacte pe
extrageri uniforme (enumerare), pe pool canonic (poziția 1 = cel mai bine
clasat), nu rate observate.

## Concluzie

1. **Configurația utilizatorului e la frontieră.** 6/49 pool 16, garanția 4,
   fără plafon: 152 de variante, coverul complet C(16,6,4). P(3+) și P(4+) pe un
   bilet sunt deja maximul pool-ului (29,81% și 7,96%). Pentru 5+ (0,2514%),
   recoacerea în C (6 setări × 3 milioane de mutări, plus 4 × 10 milioane cu
   reîncălziri) nu găsește alt cover complet de 152 cu mai mult. La fel la
   pool 15 și la 5/40 și Joker pe pool 16 (`refined_covers_2026-10-16.md`).
2. **Unde exista loc, s-a livrat.** Cinci pârghii noi, fiecare certificată la
   fiecare apel: același număr de bilete distincte, numerele jucate rămân
   jucate, niciun prag de la 3 la numărul extras în scădere, câștig strict,
   garanția nu scade.
3. **Auditul**: 6 zone (designuri, limita de consecutive, „Bilet complet”,
   motor și worker, date, UI), fiecare constatare reprodusă și verificată
   adversarial. Toate cele confirmate sunt reparate, fiecare cu test care pică
   pe codul vechi; granița 151/152 e un compromis documentat, nu o eroare.

## Pârghii livrate

### 1. Covere complete rafinate (fără plafon, pool 9..14)

`covering/refined_covers.py`, `covering_designs/refined/R_*.txt`: 12 geometrii,
câștig la g+1 între +0,26% și +7,33% relativ (6/49 pool 13 g3, 21 de bilete:
4+ 1,6129% → 1,7311%). Tabelul complet: `refined_covers_2026-10-16.md`.

### 2. Designuri lotto „g dacă c” rafinate (pool 10..16)

`covering_designs/refined/RL_*.txt`, 66 de geometrii din 84. La celelalte 18,
căutarea n-a dat un rezultat certificat: 9 au numai două bilete, 2 sunt
„3 dacă 6” la pool 15-16, iar 7 sunt designuri mici (4..14 bilete) la pool
10..12. Garanția „g dacă c” rămâne 100%. Designul rafinat intră fără plafon și
la orice plafon care cuprinde designul întreg. Tot acolo, al doilea candidat e
wheel-ul clasic al motorului la același număr de bilete, construit o dată pe
geometrie; rămâne cel mai bun dintre cei certificați. Pe 96 de geometrii
(pool 9..16, garanția 3+) schimbă numai pool 9 „3 dacă 4” la 5/40 (3+ +1,67%)
și Joker (3+ +1,87%), unde nu există design rafinat.

| Joc | Pool | g dacă c | Bilete | 3+ | 4+ | 5+ |
|---|---:|---|---:|---:|---:|---:|
| 6/49 | 16 | 3 dacă 5 | 10 | +12,72% | +12,11% | +4,60% |
| 6/49 | 16 | 3 dacă 4 | 21 | +4,46% | +3,41% | +1,13% |
| 6/49 | 16 | 4 dacă 5 | 65 | 0 | +3,26% | +1,19% |
| 6/49 | 16 | 5 dacă 6 | 281 | 0 | +0,43% | +6,39% |
| 5/40 | 16 | 4 dacă 5 | 170 | 0 | +7,90% | +1,66% |
| Joker | 16 | 4 dacă 5 | 170 | 0 | +10,15% | 0 |

Pe toate cele 66: mediana +4,65% la pragul garanției, maximum +12,72%.
Rezumat pe geometrie: `refined_lotto_2026-10-16.json`.

### 3. Ramura lotto cu plafon: wheel-ul clasic certificat

Cu plafon, motorul trunchia designul lotto. Acum primește universul jocului și
ia wheel-ul clasic al motorului la același buget, numai certificat și numai
dacă acoperirea „g dacă c” nu scade.

| Joc | g dacă c | Bilete | Acoperire lotto | 3+ | 4+ |
|---|---|---:|---|---:|---:|
| 6/49 | 3 dacă 4 | 10 | 83,74% → 86,15% | +2,51% | +1,24% |
| 6/49 | 4 dacă 5 | 20 | 54,60% → 64,79% | +12,45% | +9,33% |
| 6/49 | 5 dacă 6 | 64 | 35,30% → 48,05% | +14,77% | +34,60% |
| 5/40 | 4 dacă 5 | 64 | 54,88% → 71,66% | +25,50% | +18,88% |
| Joker | 4 dacă 5 | 64 | 55,15% → 71,66% | +32,25% | +15,32% |

### 4. Garanția 5 și 6 peste 64 de bilete

Designurile cu buget existau numai pentru garanția 4. Garanția 5 sau 6 cădea pe
căutarea din proces; acum ia secțiunea H a garanției 4, certificată pe garanția
cerută (`budget_designs._stored`). 6/49 pool 16:

| Garanție | Bilete | 3+ | 4+ | 5+ |
|---:|---:|---|---|---|
| 5 | 100 | 29,81% (egal) | 6,06% → 6,73% (+11,1%) | +2,9% |
| 5 | 120 | 29,81% (egal) | 6,63% → 7,52% (+13,4%) | +3,3% |
| 6 | 100 | 24,62% → 29,81% | 2,86% → 6,73% | +79% |

### 5. Garanția egală cu biletul, cu plafon

Garanția 6 la 6/49 (5 la 5/40 și Joker) cu plafon dădea primele N combinații
lexicografice ale pool-ului: P(garanție) e aceeași pentru orice N bilete
distincte, deci pragurile mici rămâneau la voia întâmplării.
`covering/guarantee_portfolio.py` ia biletele unei garanții mai mici
(3..garanția-1), certificate pe garanția cerută, motor și „Bilet complet”.

| Joc | Bilete | 3+ | 4+ |
|---|---:|---|---|
| 6/49 | 30 | 24,10% → 27,00% | +6,4% |
| 6/49 | 64 | 28,72% → 29,73% | +13,2% |
| 5/40 | 100 | 32,99% → 45,56% | 3,27% → 6,23% |
| Joker | 80 | 15,20% → 23,09% | +63,5% |

Efect secundar reparat: la garanția 6, 65 de bilete dădeau 3+ 22,39%, sub
28,72% la 64; acum 29,81% față de 29,73%. Monotonia nu e garantată pe toate
bugetele: 6/49 garanția 6, 57 → 58 de bilete, 3+ 29,65% → 29,61%; 5/40 și
Joker garanția 5, 64 → 65 de bilete, 3+ scade cu ~0,3-0,5% relativ.

### 6. „Bilet complet” cu garanția completă înainte de umplere

Pe lângă căutarea pornită din variantele pasului, al doilea candidat e un
design căutat o dată pe geometrie (poziții canonice), pus pe clasamentul
pasului și certificat față de variantele lui. Rămâne cel mai bun la pragul de
peste garanție. Pe 77 de pași (6/49 pool 8..14 g3/g4, 5/40 pool 8..12 g3,
Joker pool 8..9 g3, 3..10 bilete): 15 câștigă, până la +5,1% la g+1, niciunul
nu pierde.

## Granița 151/152 la pool 16 (compromis, nu eroare)

| Plafon | Bilete | Garanția 4 | 4+ | 5+ |
|---:|---:|---:|---:|---:|
| 151 | 151 (secțiunea H) | 98,74% | 7,873159% | 0,266029% |
| 152 sau fără | 152 (cover complet) | 100% | 7,960002% | 0,251434% |

Bugetul care cuprinde coverul complet îl ia: garanția 100% are prioritate, iar
4+ crește. 5+ scade cu 5,5% relativ. Un cover complet de 152 cu 5+ de cel puțin
0,266% n-a fost găsit (punctul 1 al concluziei). Pe 5/40 și Joker fenomenul nu
apare.

## Reparații din audit

- Designuri: o secțiune H invalidă e sărită singură (înainte strica tot
  fișierul); memoria fișierelor C și H ține cont de dată și de mărime.
- „Bilet complet”: profilul exact se refolosește pe poziții între pașii WF;
  reluarea pe pool 16 scade de la ~0,8-0,9 s la ~0,11 s pe pas (primul pas
  2-5 s). Pe pool 8..14 cu garanția completă rămâne ~1 s pe pas: căutarea
  pasului se păstrează, ca să nu se piardă hituri. Butonul nu mai blochează
  bucla NiceGUI (`run.io_bound`, notificare „⏳”). Scorurile rotunjite egale nu
  mai inversează pozițiile din design față de clasament.
- Motor: biletele ies în ordinea sumei scorurilor după orice etapă, deci
  „Top N bilete simple” sunt cele mai bine clasate; cheia worker-ului semnează
  designul pool-ului jucat după restrângerea bazei.
- UI și mail: walk-forward-ul oprit de buget spune în panou, raport și stare
  ce jocuri au rămas nevalidate; RATING din mail cu o zecimală, cât are sursa;
  5+ mic în reluare cu cifre semnificative („0.00016%”, nu „0.000%”); auditul
  limitei de consecutive în ordinea clasamentului; jurnalul nu mai anunță o
  re-rulare la citirea cache-ului la pornire.
- Date: testele pe extrageri viitoare scriu singularul la 1.

## Chei și cache

- WF: `|rc<amprentă>` (cover rafinat pe `lajolla` și `bf1`), `|rl<amprentă>`
  (lotto, designul rafinat), `|lc1:<amprentă>` (lotto: semnătura wheel-ului
  clasic pe care îl poate adopta, la plafonul care taie designul sau la mărimea
  designului), `|lg1:<amprentă>` (garanția egală
  cu biletul, cu plafon: semnăturile candidaților de la garanțiile mai mici).
  O schimbare într-un fișier C, R sau H adoptabil schimbă exact cheile atinse;
  fără bump global `v31`.
- Worker `v18` (v17 coverele rafinate). Stratul e inert în UI (`use_cache=False`).

## Limite

- Geometrie pură, extrageri uniforme: nicio afirmație predictivă, rata
  pool-ului nu se schimbă.
- Fără optimalitate: căutări locale; lipsa unui rezultat la pool 15-16 nu
  dovedește că nu există covere mai bune de aceeași mărime.

## Reproducere

- Covere complete: `python scripts/analysis/gen_refined_covers.py [mutări] [procese] [pool-uri]`.
- Lotto: `python scripts/analysis/gen_refined_lotto.py [mutări] [procese] [pool-uri]`
  (refăcut octet cu octet pe Joker pool 10, 3 dacă 4, și pe 6/49 pool 12, 4 dacă 5).
- Ambele cer gcc; producția recertifică fișierele la fiecare apel.

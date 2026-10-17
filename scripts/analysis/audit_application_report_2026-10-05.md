# Audit aplicație — 5 octombrie 2026

## Rezultat

Auditul a livrat rândurile „Bilet complet” din pool și dispersat în
„📜 Istoric hits”, pe aceleași extrageri walk-forward, plus trei corecții
găsite la verificare. Scorerul, pool-ul de producție, wheel-ul principal,
bench-ul și schema cozii rămân neschimbate. Nu se promovează metode și nu
se afirmă avantaj predictiv.

## Ce s-a adăugat în Istoric hits

La fiecare pas WF, motorul păstrează `ticket_context`: pool-ul, clasamentul
din audit (`timesfm_predictions`, `consecutive_limit`, `restrict_base`),
numărul Joker și extragerea țintă. Reluarea (`ticket_replay`) reconstruiește
ce ar fi dat butonul „🎟️ Bilet complet” în ziua aceea, cu numărul de bilete
din sidebar și garanția rezultatului afișat, în ambele moduri, același număr
de variante. Joker: numai Urna 1. La 5/40, intersectia e cu toate cele șase
numere extrase.

Câmpul e aditiv (`None` = cache scris înainte). Un cache, complet sau
parțial, cu pași fără context nu mai e servit direct: pașii aceia se refac
(`skip_indices`); intrările vechi rămân până atunci. Fără bump `v30`.

UI: calcul în fundal, memorat pe lista WF afișată; un temporizator de 1 s
umple sau reface numai blocul (și la schimbarea lui „Bilete”), fără
refresh-ul panoului. Coloana 🎲 este P exactă, prin enumerarea tuturor
extragerilor, ca cel puțin o variantă să atingă 3+/4+/5+ pe biletele celei
mai recente extrageri. Nu e rata observată; nu depinde de etichetele
numerelor, ci de cum se suprapun variantele.

Verificare pe UI izolat (port liber, runtime temporar, worker separat;
procesele reale pe 8000 neatinse):

| Joc | Extrageri WF | 3 bilete, pool 3+/4+/5+ | 3 bilete, dispersat | 10 bilete, pool | 10 bilete, dispersat |
|---|---:|---|---|---|---|
| 6/49 | 777 | 77 / 12 / 1 | 128 / 6 / 0 | 78 / 17 / 1 | 360 / 25 / 0 |
| Joker, Urna 1 | 657 | 30 / 1 / 0 | 28 / 0 / 0 | 58 / 3 / 0 | 98 / 1 / 0 |
| 5/40 | 522 | 87 / 10 / 0 | 111 / 5 / 0 | 92 / 14 / 0 | 311 / 15 / 0 |

La 10 bilete, 3+ dispersat urmează 🎲 (6/49 46,33% față de 48,4%; 5/40
59,58% față de 58,7%). La 4+ cu puține evenimente, diferența dintre rânduri
nu arată că un mod e mai bun. Rândul din pool urmează hiturile de pool ale
tabelului WF; metoda a fost aleasă pe același istoric.

10 bilete × trei jocuri: ~90 s, cu „⏳”; secțiunile deschise au rămas
deschise. Reluarea nu enumeră șansele la fiecare pas (`with_chances=False`).

## Corecții din audit

1. **Jurnalul greedy.** `covering.greedy` scria pe loggerul rădăcină, la
   INFO, la fiecare iterație. Reluarea a mii de bilete a umplut logul UI
   izolat (~40 000 de linii `[WHEEL]` după walk-forward). Logger de modul;
   pe durata reluării, nivelul e WARNING, inclusiv în procesele copil.

2. **Pornirea UI din copiii ProcessPoolExecutor.** `app_nicegui.py` pornea
   `ui.run()` și pentru `__mp_main__` (patternul reloaderului NiceGUI).
   Reluarea din procesul UI, cu `reload=False`, ar fi repornit interfața
   în copii. Pornirea rămâne numai din `__main__`. Worker-ul era deja
   protejat.

3. **Nota de sub tabel.** Nu mai lasă un pool peste 🎲 să se citească drept
   validare externă a metodei.

## Ce a rămas intact

- Registry: 53 de metode; curare 53 active; `per_game` 52 / 51 / 51 / 51
  (`interval_extrema_k16` numai la Re-Bench 6/49, exclus din producție).
- 151 de designuri locale (52 clasice + 99 lotto).
- Cache: bench `v21`, WF `v30`, worker `v11` (inerț, UI trimite
  `use_cache: False`). Semantica pool-ului nu s-a schimbat.
- Nicio metodă nouă, niciun filtru structural, nicio afirmație de câștig.

Limite rămase, neschimbate: acoperirea 100% a unui lotto design „t dacă p”
nu e acoperirea clasică t-din-t; walk-forward-ul validează o decizie fixă
curentă, nu un holdout extern al selecției; dispersia e opțiune, implicit
oprită, numai în „Bilet complet”.

## Verificări

Python 3.14.7 / Windows, venv `D:\_BUILD\_LOTO\.venv`:

- `test_full_ticket_replay.py` (27 de teste): context pe fiecare pas
  (6/49, Joker, 5/40), reluarea = butonul, 🎲 = dialogul ultimului pas,
  cache vechi, paralel = în proces, ramura WF paralelă, jurnal mut,
  garda `__main__`.
- Designuri: `test_covering_designs.py`, 116 trece.
- Suita completă: **2100 teste trecute, 17 omise, zero eșecuri**, 282,19 s
  pe Python 3.14.7 / Windows; 94 de fișiere `test_*.py` (fără `scratch/`).
- `git diff --check` pe fișierele intenționate.
- UI + worker izolate, HTTP 200, generare și walk-forward complete;
  aplicația de pe portul 8000 nu a fost oprită.

Nu au fost comise `bench_results/folds.csv`, `report.json`, starea runtime
sau `scratch/`.

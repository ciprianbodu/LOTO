# AGENTS.md - ghid operational si roadmap

## 1. Scop

Aplicatia optimizeaza pool-uri si sisteme de acoperire pentru Loto 6/49, Loto
5/40 si Joker. Fluxul este exclusiv CPU:

1. valideaza istoricul;
2. compara metode de scoring prin walk-forward;
3. alege un scorer sau ensemble pentru fiecare joc si dimensiune de pool;
4. genereaza pool-ul prin top-N canonic (cu limita optionala de consecutive, §6);
5. transforma pool-ul in bilete prin covering design;
6. raporteaza separat hiturile de pool, hiturile pe bilet, acoperirea si costul.

Aplicatia nu prezice extrageri si nu poate garanta mai multe castiguri. Pentru un
pool aleator de aceeasi marime, probabilitatea de a contine cel putin trei numere
depinde de geometria jocului si de marimea pool-ului. Scoring-ul este evaluat ca
ipoteza empirica; wheeling-ul optimizeaza acoperirea numerelor deja selectate.

## 2. Starea curenta

Snapshot verificat la 2026-09-15:

- branch de productie: `main`;
- UI: NiceGUI, `app_nicegui.py` + `ui_runtime.py` / `ui_results.py` / `ui_bench.py` / `ui_hits.py`, port 8000;
- runtime tinta: ultimul Python 3.14.x stabil;
- venv: `D:\_BUILD\_LOTO\.venv`, in afara OneDrive;
- runtime scris frecvent: `D:\_BUILD\_LOTO` (`.wf_cache`, `loto.log`,
  `bench_full.log`, `startup_8000.log`), cu override prin `LOTO_RUNTIME_DIR` si
  `LOTO_WF_CACHE_DIR`;
- registry: 52 de intrari in `METHODS` = `random` (martor structural, interzis
  in productie) + `frequency` (fallback determinist) + 50 de metode noi
  (14.09.2026), grupate in patru module cu utilitare comune in `methods_common`:
  `methods_recency` (16), `methods_relational` (10), `methods_learning` (4),
  `methods_wave2` (20). Cele ~183 de metode vechi (clasice/ml/coverage/graph/
  revived/search_649/top649/math_extra) au fost sterse, impreuna cu mecanismul
  de tombstone (`disabled_methods.json`, `disabled.py`, `prune_methods.py`);
  `METHOD_ALIASES` pastreaza redenumirea `alternating_parity` -> `season_period2`. `neighbor_adjacent` (vecinii numerici ±1/±2 ai
  ultimei extrageri), `repeat_last_draw` (naive last), `rwr_last_draw` (RWR
  semanat din ultima extragere, prefix last-draw) si `haar_multiscale` (pe
  Urna 2 top-1 ≡ repeat) raman in METHODS ca martori de bench, dar sunt in
  `EXCLUDED_FROM_PRODUCTION` — top-K e o clasa geometrica, nu un ranking
  (audit 2026-09-15 / 2026-09-16);
- curare reversibila: toate cele 50 + `frequency` in `active` si in `per_game`
  pe fiecare joc, fara preselectie pe istoric; Re-Bench ruleaza matricea completa
  (52 pe fiecare joc, cu `random` adaugat de runner);
- `EXCLUDED_FROM_PRODUCTION` = `{random, neighbor_adjacent, repeat_last_draw,
  rwr_last_draw, haar_multiscale, interval_extrema_k16}`; pe Urna 2 si
  `EXCLUDED_FROM_SINGLE_PICK` = `{markov_self_state, vlmm_self_k3}` (§4.2);
  vechile filtre de clasa (parity_balance, prime_bias, 649_decade_hot etc.) nu
  mai exista ca metode. Un nume necunoscut sau exclus din `best_methods.json`
  cade determinist pe `frequency` (`_sanitize_production_name`);
- covering designs locale: 52 covere clasice `C_v_pick_t.txt` plus 99 lotto
  designs `L_v_pick_p_t.txt` (pool 6..16, pick 5 si 6), toate validate la 100%
  la ultimul audit;
- cache benchmark: `v22` (`rank_ensemble_core` cu rang mediu la egalitate; v21 Loto 5/40 citeste toate cele 6 numere extrase);
- cache walk-forward: `v31` (sufixele `bf1`, `bc1`, `tg1` din 2026-10-08, fara bump, §8; rang mediu in `rank_ensemble_core`, ordinea scorului la factorul 0 al penalizarii, semnatura designurilor fara cale absoluta; v30 swap-uri cu dominanta exacta a profilului de hituri, sufix `|hp1`; v29 optimizarea 5+ a coverelor complete de 4; v28 candidati hitcover si validare stricta Joker; v27 corecteaza cele 6 numere extrase la 5/40);
- Loto 5/40 = 6 numere extrase din 40, bilet de 5. Istoricul scorerilor,
  hiturile de bench/WF/UI si baseline-ul random folosesc n1..n6 (`draw_n = 6`);
  biletul, garantia, sistemul complet, costul si pool-ul de baza (k5) folosesc
  `pick_n`/`play_n = 5`. Categoria I (5 din primele 5 extrase) nu este modelata
  separat. `folds.csv` scris inainte de v21 are randuri 5/40 pe n1..n5 si este
  marcat `stale` de `check_freshness` pana la Re-Bench;
- cache rezultat worker: `v13` (bugetul care cuprinde designul complet, cautarea exacta peste 64 de bilete, greedy-ul care domina designul la egalitate; v12 aceleasi schimbari de pool si auditul rezervei `frequency`; v11 swap-uri cu dominanta exacta a profilului; v10 optimizarea 5+ a coverelor complete de 4; v9 candidati hitcover; v8 activeaza hitcover implicit la buget pozitiv, v7 corecteaza auditul Joker al numerelor nejucate, v6 aduce identitatea jocului);
- teste: 89 fisiere `test_*.py`, 1977 de teste (renumarat la 2026-10-02). Pe
  Python 3.14.7, Linux cu `pwsh` (`LOTO_PWSH`): 1952 trec, 25 sarite (integrarea
  reala a lansatorului, numai pe Windows), 0 esecuri. Pe Windows, cele 15 teste
  `test_launcher_ensure_git.py` sunt sarite (git-ul simulat e script shell). In
  containerele de audit, `uv` mai vechi de 0.9 stie doar 3.14.0rc2, pe care
  pydantic pica la importul `nicegui` (`prefer_fwd_module` lipseste din
  `typing._eval_type`); un `uv` recent instaleaza 3.14.7. Cele doua
  esecuri raportate anterior ca PRE-EXISTENTE in
  `test_wf_generation_settings` erau un defect al TESTULUI, nu al motorului — vezi
  §Audit global 2026-09-15.

Nu copia aceste numere in cod. Renumara inainte de a le cita:

```powershell
python -c "from loto_enterprise.benchmark.methods import METHODS; print(len(METHODS))"
python -c "from loto_enterprise.benchmark.curated import load_curated,load_per_game; print(len(load_curated()), {k:len(v) for k,v in load_per_game().items()})"
```

### Teste pentru rata de hit — 2026-10-09

- Ecran nou, protocol fixat inainte de rulare
  (`scripts/analysis/hit_screen_2026-10-09.py`, raport `hit_screen_2026-10-09.md`):
  - aleatorism: 131 de teste pe 14 serii (Romania si 10 straine). 7 au
    p < 0,05, cat se asteapta din intamplare (6,6); Holm 1,00. Nicio abatere:
    frecventa, deriva, zi, pozitie, repetari, perechi, serii, legaturi intre
    jocurile romanesti;
  - puterea: un dezechilibru de bile care ar ridica 4+ la pool 16 cu 20-26%
    relativ ar fi fost detectat cu probabilitate de 65-82%;
  - metode noi: semnale intre jocurile romanesti, „urmareste liderul” peste
    cei ~490 de scoreri din 2026-10-05 si frecventa pe era curenta. 492 de
    combinatii, p-ul cautarii 0,28-0,92, Holm minim 0,099 pe confirmare.
    Nimic promovat.
- Joker Urna 1: cele mai frecvente 16 numere pe tot istoricul ating 4+ in
  7,34% din extrageri, peste maximul obtinut pe istorii aleatoare (p 0,002;
  Holm pe 13 serii 0,026). Efectul nu e stabil: pool-ul ales pe prima jumatate
  pierde pe a doua, iar frecventa cauzala sta la nivelul hazardului pana in
  2023 si urca la 8,84% in 2024-2026. Productia joaca deja `frequency` la
  Joker k16 4+; fara schimbare.
- 5/40 -> 6/49 la pool 16 (frecventa 5/40 pe 50 de zile): 10,94% fata de
  7,96% pe confirmare, Holm pe eveniment 0,056, pe hituri 0,795. Nu trece;
  o extragere in plus il muta peste prag. `shared_bias_2026-10-09.py`:
  frecventele jocurilor romanesti nu sunt corelate pe blocuri de timp.
- Rezultatul poarta amprentele CSV-urilor (`data_sha256`) si nu se scrie daca
  istoricul se schimba in timpul rularii. Ecranul complet: 56 min pe 4 nuclee.

### Pool 16 tinta 4+ si audit global — 2026-10-08

- Rata 4+ a pool-ului nu se ridica prin cod: e hipergeometrica (16 numere:
  7,96% la 6/49, 16,03% la 5/40, 4,68% la Joker). La k16, tinta 4+, metoda
  aleasa nu trece Holm pe niciun joc (6/49 `markov_lag2` p 0,435; 5/40
  `pair_lift_last` 1,000; Joker `frequency` 0,125). Nimic promovat.
- Biletele: covere complete mai mici, cautate offline prin recoacere simulata
  (`scripts/analysis/cover_anneal.c`) si validate exhaustiv la 100%:
  C(16,6,4) 198 -> 172, C(16,5,4) 467 -> 416 (5/40 si Joker), plus 14 covere
  de pool 11..16 (tabelul din raport). Garantia ramane; la garantia 3, mai
  putine bilete inseamna 4+ ceva mai mic pe bilete. Rafinarea 5+
  (`higher_hits`) nu mai gaseste schimburi pe C(16,6,4); testele ei verifica
  mecanismul pe un cover greedy.
- Motorul, cu `max_num` (§7): bugetul care cuprinde designul complet il ia
  (6/49 pool 16, garantie 3, buget 45: 99,46% -> 100%); peste 64 de bilete,
  `covering/budget_climb.py` ridica 4+ pe bilete cu 4-23% relativ, fara
  scadere la vreun prag; la egalitate de bilete, greedy-ul care domina exact
  designul il inlocuieste. „Bilet complet” ramane neschimbat.
- Chei: worker v13; WF ramane v31, cu sufixele `bf1`, `bc1`, `tg1` si designul
  pool-ului rotit efectiv (§8). Primul WF dupa actualizare recalculeaza cheile
  fara plafon si pe cele cu buget peste 64 sau cat designul (partial, apoi
  continuat prin `skip_indices`).
- Auditul: 18 constatari confirmate, toate reparate, fiecare cu test care pica
  pe codul vechi. Grava: parserul spaniol pierdea zilele 1-9 ale fiecarei
  luni. Celelalte: actualizatoarele (§4.1), `reset_jobs`, ora locala si
  scrierea unica a deciziei (§4.4), datele in PushHistory (§4.5), tinta
  stampilata si nota Holm (§5), butoanele, data mailului si limita ceruta la
  variantele dispersate (§6), WF sarit cand decizia s-a mutat (§8).
  Reparatiile au trecut prin revizie adversariala: randuri necitibile, chei pe
  data canonica, refuzul vizibil in START_8000 (inclusiv NEACTUALIZATE),
  decizia recalculata dupa deschiderea portului, marcajul WF al rezultatului
  anterior sters, union34 cu `tg1`.
- Verificat pe Python 3.14.7 / Linux cu pwsh 7.6.2: 101 fisiere `test_*.py`,
  2451 teste trecute, 11 sarite (lansatoarele CMD si PATH-ul Windows), zero
  esecuri.
  `audit_application.py`: 13 istorice, 848 verificari de paritate, 151
  designuri, 26 de pipeline-uri, 13 profile de cover complet de 4, worker
  separat pe 13 jocuri si UI HTTP 200, fisierele de productie neatinse.
- Rapoarte: `scripts/analysis/audit_application_report_2026-10-08.md`,
  `scripts/analysis/pool16_4plus_2026-10-08.md`.

### Sincronizarea lansatoarelor — 2026-10-07

- START_8000 si ACTUALIZARI nu mai raspund „origin/main are commit-uri noi.
  Nu le aplic peste modificarile necomise”. Sync (`launcher_git.ps1 -Mode
  Sync`) aplica `origin/main` si peste modificarile necomise, repune
  commit-urile locale netrimise si le trimite (planurile si garantiile in
  §4.5): copie octet cu octet in `.git\loto-sync-backup\`, fuziune
  `git merge-file`, `bench_results/` ramane al statiei, fara `git stash` si
  fara reset fortat. Cand nu poate integra, spune de ce si nu atinge nimic.
- Cinci runde de review adversarial pe Sync si PushHistory; fiecare
  constatare confirmata are test care pica pe versiunea anterioara.
  `test_launcher_git.py` ruleaza si pe Linux, sub PowerShell 7 (`LOTO_PWSH`).
- Prima pornire dupa actualizare ruleaza tot helper-ul vechi: lansatorul il
  copiaza din checkout-ul local inainte de sync. O statie cu modificari
  necomise porneste deci sync-ul nou o data, manual (comanda din PR).
- Verificat pe Python 3.14.7 / Linux cu pwsh 7.6.2: 97 fisiere `test_*.py`,
  2374 teste trecute, 11 sarite (lansatoarele CMD si PATH-ul Windows), zero
  esecuri.

### Validarea WF tinuta minte — 2026-10-07

- Ultimul rezultat reapare la fiecare pornire, cu avertismentul „Rezultate
  RECUPERATE” si validarea WF citita din cache-ul exact (`cache_only`): fara
  pas calculat, fara scriere, fara mail sau oprire, si numai daca decizia de
  acum alege aceeasi metoda ca rezultatul. Pana acum, un job preluat de UI nu
  se mai afisa dupa repornire, deci „📜 Istoric hits” ramanea gol (§4.4, §8).
- La o extragere noua, WF refoloseste pasii validarii anterioare a aceleiasi
  chei si calculeaza numai pasii noi (§8); o corectura in trecut reface tot.
  Fara bump de cache.
- Cost pe 6/49 (2590 de randuri): gasirea prefixului dupa `history_rows`
  ~4 ms, o singura amprenta pe candidat; un cache scris fara camp, ~0,3 s pe
  candidat (cel mult 4).
- Review adversarial pe diff: constatarile confirmate sunt reparate, cu
  teste: validarea altei metode la pornire, eticheta „cele mai recente” cand
  lipsesc tocmai cele mai noi extrageri, raportul rescris fara WF la
  repornire, cautarea inutila a prefixului, amprenta deciziei luata prea
  tarziu. Testele acopera si cheia: alta metoda, garantie, limita de
  consecutive sau interval nu imprumuta pasi (pica daca potrivirea ignora
  semnatura); `test_wf_incremental.py` importa UI-ul la nivel de modul, ca
  izolarea marcajului din `conftest.py` sa se aplice si unui test rulat singur.
- Verificat pe Python 3.14.7 / Linux cu pwsh: 97 fisiere `test_*.py`, 2301
  teste trecute, 38 sarite (lansatorul, numai pe Windows), zero esecuri.
  Cap-coada pe o baza izolata, cu worker si UI reale: jobul preluat intr-o
  sesiune anterioara reapare la doua porniri succesive cu validarea din cache
  (36/36 pasi), fara „Cache miss”, fara pickle rescris; o extragere adaugata
  refoloseste 35 de pasi si calculeaza unul.

### Audit global 2026-10-07, runda 2

- Randul 5/40 din 24-10-2024 a fost corectat separat (PR #144, sectiunea
  urmatoare), iar PushHistory publica numai randuri adaugate din PR #145
  (§4.5); runda 2 trateaza celelalte puncte ramase de decis si nu modifica
  lansatorul.
- Decizie: `multiplicity` pe fiecare celula, test binomial pe fereastra
  completa cu corectia Holm peste candidati (§5, punctul 10). Scorerul ales nu
  se schimba; pe `folds.csv` versionat, 45 din 46 de celule raman cu avantaj
  nedemonstrat.
- Urna 2: `EXCLUDED_FROM_SINGLE_PICK` (`markov_self_state`, `vlmm_self_k3`,
  bila precedenta in 66% si 63% din pasi pe extrageri uniforme, §4.2);
  `per_game.joker_urna2` are 48 de metode, fara cele doua si fara
  `naive_bayes_last` (duplicat al `markov_pairs`). Pe jocurile cu pool nu s-a
  introdus poarta pe extragerea precedenta: ocupa cel mult 6 din K locuri, iar
  restul pool-ului ramane clasamentul metodei.
- Jobul preluat de UI se marcheaza in baza statiei, nu in checkout-ul
  sincronizat (§4.4).
- Swap-urile de profil dau acelasi rezultat de ~7 ori mai repede la garantia
  egala cu biletul (pool 16, plafon 10: 9,41 s -> 1,43 s la pick 6), §7. Fara
  bump de cache.
- Panoul de rezultate ramane neredesenat la esecul recalcularii deciziei,
  intentionat (§5, „Coerenta outputului”).
- Verificat pe Python 3.14.7 / Linux cu pwsh 7.6.2, impreuna cu PR #145: 96
  fisiere `test_*.py`, 2283 teste trecute, 38 sarite (lansatorul, numai pe
  Windows), zero esecuri;
  `audit_application.py`: 13 istorice, 848 verificari de paritate, 151
  designuri, 26 de pipeline-uri, worker separat pe 13 jocuri si UI HTTP 200,
  fisierele de productie neatinse. Marcajul, cap-coada pe o baza izolata:
  worker real, prima pornire UI recupereaza jobul o data si il marcheaza,
  a doua nu-l mai reia, iar cheia veche dispare din `.ui_state.json`.
- Raport: `scripts/analysis/audit_application_report_2026-10-07.md`, „Runda 2”.

### Corectura istoricului 5/40 — 2026-10-07

- `_ISTORIC/loto_5_40.csv`, linia 1538 (antetul = linia 1): 24-10-2024 copia
  extragerea din 27-10-2024 (13,34,11,16,10,39, in aceeasi ordine). Corect:
  14,15,28,10,25,26, in ordinea extragerii, dupa arhiva oficiala loto.ro,
  fanatik.ro si stiripesurse.ro. Pe loto.ro, octombrie 2024 coincide acum
  integral cu CSV-urile la toate trei jocurile (9 extrageri fiecare). Copia
  exista si pe loto49.ro, sursa `update_csv.py`: scraperul adauga numai
  extrageri cu data >= ultima stocata, deci nu o reintroduce; un import
  complet de acolo ar face-o.
- `test_externe_history.py::test_no_draw_repeats_the_previous_one` respinge doua
  randuri consecutive cu aceleasi numere principale in toate cele 13 CSV-uri din
  `_ISTORIC/` si `_ISTORIC/externe/` (§4.1). Era singura pereche.
- Experimentul preinregistrat din 2 octombrie: `ERRATA` in
  `pool_hit_experiment.py` (fisier -> {linie: (rand inregistrat, rand
  corectat)}). `prefix_hash` si `load` refac in memorie randul inregistrat,
  deci amprentele raman valide si `run` evalueaza datele preinregistrate; o
  linie documentata cu alt continut e refuzata. Erata, masurata cu
  `erratum_2026-10-07.py`, e in `RESULTS_2026-10-02.md`: pe fisierul corectat,
  `ro_540_k11` da 17/23/23/21 din 522 (pe cel inregistrat 20/24/26/21, exact
  tabelul publicat), p brut minim 0,28. Verdictul ramane: niciun supravietuitor.
- Bench si WF: cheile includ datele (`draws_2d.tobytes()`, continutul `df`),
  deci foldurile si cache-ul WF 5/40 se refac. Prospetimea (hash pe numere si
  date) vede 5/40 schimbat fara randuri noi (`moderate_drift`), iar UI-ul cere
  Re-Bench. Decizia 5/40 si `bench_results/` raman cele de dinainte pana la un
  Re-Bench complet, neexecutat aici.
- Verificat pe Python 3.14.8 / Windows: 95 fisiere `test_*.py`, 2191 teste
  trecute, 17 sarite, zero esecuri. Sesiunile Claude Code seteaza
  `NoDefaultCurrentDirectoryInExePath=1`; cu ea, 6 cazuri din
  `test_launcher_git.py` nu gasesc lansatorul in directorul curent. Variabila se
  scoate pentru procesul pytest.

### Audit global 2026-10-07

- Sapte zone revizuite in paralel (motor, covering si bilete, bench si
  decizie, metode, walk-forward, worker si actualizatoare, UI); fiecare
  constatare reprodusa, fiecare reparatie cu test care pica pe codul vechi
  (`test_audit_2026_10_07.py`). Nucleul a rezistat: fara look-ahead, fara
  mutatii ale intrarii, WF paralel = serial, hituri recalculate fara
  diferente, 151 designuri la 100%, top-K bench = pool de productie.
- Regresie din PR #142: `ui_bench._pct` inlocuia `ui_results._pct` prin
  `_sync_ui_namespace` (5+ la 5/40, 0,00365%, aparea „0.00%”). Acum
  `_rate_pct`; un test interzice doua definitii diferite sub acelasi nume
  privat in modulele UI.
- Date: `update_csv.py` refuza pagina care nu mai ajunge la ultima extragere
  stocata (gaura permanenta), adauga a doua extragere a unei zile deja
  stocate (deduplicare pe data + numere sortate), iar un fisier blocat nu mai
  opreste celelalte jocuri. `update_externe.py` cere toti anii la Germania.
- Decizie: scorer interzis/necunoscut → `frequency`, nu vechii castigatori
  dupa `avg_hits`; schimbarea tintei cere un `folds.csv` complet si asteapta
  finalul bench-ului; `--block-size` > 1 e rulare redusa; prospetimea
  stampileaza CSV-urile de dinaintea bench-ului; rata pooled nu depinde de
  ordinea randurilor (decizia pe `folds.csv` versionat ramane identica).
- Motor: auditul marcheaza rezerva si la Romania (`no_decision`, `attempted`);
  `rank_ensemble_core` folosea rangul dupa pozitie la egalitate (numerele mari
  castigau, productie la 5/40 k6) — acum rang mediu, echivariant, cu test de
  reetichetare pe toate metodele de productie; factorul 0 al penalizarii
  pastreaza ordinea scorului. Bump bench `v22`, WF `v31`, worker `v12`.
- WF si concurenta: pasii scorati de doua decizii nu se mai salveaza; cursa din
  `_load_config` intre fire; semnatura designurilor fara cale absoluta;
  reluarea „Bilet complet” o singura data; worker-ul altui checkout nu mai
  trece drept al nostru.
- Afisare: acoperirea 99,95% nu mai apare 100.0%; pool-ul jucat in Istoric
  hits; raportul spune PARTIAL; variantele dispersate respecta limita de
  consecutive cand baza o permite, altfel nota spune limita atinsa.
- Ramasele de decis au fost tratate in aceeasi zi: randul 5/40 in „Corectura
  istoricului 5/40”, PushHistory in §4.5 (PR #145), celelalte in runda 2
  (sectiunile de mai sus).
- Verificat pe Python 3.14.7 / Linux, fara PowerShell: 95 fisiere `test_*.py`,
  2152 teste trecute, 40 sarite (lansatorul, numai pe Windows), zero esecuri;
  `audit_application.py`: 13 istorice, 848 verificari de paritate, 151
  designuri, worker separat si UI HTTP 200, fisierele de productie neatinse.
- Raport: `scripts/analysis/audit_application_report_2026-10-07.md`.

### Studiu rate de hit si variante dispersate — 2026-10-05

- Predictie: 491 de scoreri noi (16 familii, inclusiv 288 pe geometria grilei
 biletului) x 4 jocuri x 4 pool-uri = 6.355 de teste, nul = 200 de istorii
 sintetice uniforme cu reluarea completa a cautarii. Pe fiecare joc, cel mai
 bun z de dezvoltare ramane in nulul cautarii (p 0,55-0,67). Cei 20 de
 candidati inghetati: Holm minim 0,228 pe ultimele 30% romanesti, fara
 replicare pe 30.067 extrageri 6/49 si 8.290 extrageri 6/45 si 5/50 straine.
 Nimic promovat. Ecranul `spatial_math_screen_2026-10-05.md` din aceeasi zi
 folosea permutarea etichetelor, care nu e nul pentru scorerii echivarianti.
 Concluzia lui negativa ramane.
- Geometrie, exact prin enumerarea tuturor extragerilor: la acelasi numar de
 variante, variantele dispersate pe tot universul domina la FIECARE prag si
 FIECARE buget testat (41, 1..40 variante) cea mai buna configuratie de
 productie (pool 6..16, garantie 2..4) si „Bilet complet”. Exemplu 6/49,
 9 variante, 3+: 16,54% fata de 8,77% (Bilet complet). Media variantelor
 castigatoare si sansa marelui premiu sunt identice in orice aranjare.
- Implementat ca optiune, implicit OPRITA (`covering/spread.py`), scoasa la
 2026-10-10 (§6). Nu schimba scorerul, pool-ul, wheel-ul principal, bench-ul,
 WF sau coada. Nu necesita bump de cache.
- Teste: 93 fisiere `test_*.py`. Pe Python 3.14.7 / Windows: 2073 trec,
 17 omise, zero esecuri.
- Raport: `scripts/analysis/hit_rate_study_2026-10-05.md`.

### Audit global 2026-10-05

- „📜 Istoric hits” afiseaza, sub tabelul WF, „Bilet complet” din pool si
  dispersat pe aceleasi extrageri: context aditiv pe pas (`ticket_context`,
  fara bump v30), reluare in fundal, coloana 🎲 de sansa exacta uniforma.
  Cache-ul vechi isi pastreaza hiturile; pasii fara context se refac la
  urmatoarea validare (`skip_indices`).
- Corecturi din audit: jurnalul greedy nu mai umple consola la reluare
  (logger de modul, WARNING pe durata calculului); UI-ul nu mai porneste
  din copiii ProcessPoolExecutor (`__mp_main__`); nota de sub tabel nu
  mai prezinta un pool peste hazard drept validare externa.
- Verificat pe UI izolat (HTTP 200, worker separat, runtime temporar):
  3 bilete si 10 bilete pe 6/49, Joker, 5/40; temporizatorul reface
  blocul la schimbarea lui „Bilete”; procesele aplicatiei reale au ramas
  neatinse. 151 designuri locale. Registry: 53 metode; curare 53 active,
  per_game 52/51/51/51 (interval_extrema numai la 6/49).
  Python 3.14.7 / Windows: 94 fisiere `test_*.py`, 2100 teste trecute,
  17 omise, zero esecuri.
- Raport: `scripts/analysis/audit_application_report_2026-10-05.md`.

### Interval adaptiv experimental — 2026-10-03

- `interval_extrema_k16`, in `methods_experimental.py`, este un filtru de
  interval explicit experimental, inregistrat in METHODS si exclus din
  productie prin `EXCLUDED_FROM_PRODUCTION`. Curarea il adauga automat numai
  la Re-Bench pentru Loto 6/49. Setul anterior de metode ramane intact.
- Formula fixa: top16 dupa frecventa simpla pe 50 de extrageri, intr-un
  interval ales din rezultatele 4+ ale ultimelor 300 de extrageri, conditionat
  de minimul/maximul ultimei zile complete; warmup200 si regularizare50.
  Alte pool-uri din benchmark masoara prefixele acestui ranking, nu o
  optimizare pentru fiecare K. Fara tinte interne eligibile, scorul este plat
  si pasul ramane ne-evaluat; nu se contabilizeaza un fallback ca metoda.
- `call_method(..., history_cutoffs=...)` este context optional, transmis
  numai scorerilor marcati `_uses_history_cutoffs`. Runnerul furnizeaza
  inceputul zilei fiecarui rand, taiat la prefixul anterior tintei. Lipsa
  datelor inseamna contractul explicit un rand = un pas. Nu schimba schema
  cozii, decizia sau apelul scorerilor existenti. Nu necesita bump global de
  cache: numele e nou, iar cache-ul bench include deja cutoff-urile.
- UI eticheteaza rezultatele drept experimentale si afiseaza rata4+ separat
  de eligibilitatea pentru productie. Un nume fortat in best_methods nu poate
  reactiva experimentul.
- Studiul retrospectiv pe 791 extrageri 6/49 (2019-01-13..2026-10-01):
  87 evenimente pool4+ si 11 pool5+; p4+ brut0,001556, Holm51=0,07935.
  Nu este confirmare externa, nici rata pe bilete. Rezultatul nu include
  limita de consecutive a utilizatorului si nu se promoveaza automat.
- Replay-ul integrat reproduce toate791 pool-uri si clasamentele studiului.
  Rangurile16/17 sunt egale in481/791 pasi (60,81%); egalitatile nu sunt
  mascate. Se pastreaza poarta existenta de dependenta de tie-break.
- Inventar renumarat: 53 metode in registry/active; matricea efectiva
  Re-Bench are 53 la6/49 si52 la fiecare dintre celelalte trei jocuri.
  Integrare verificata pe Python3.14.7/Windows:2038 teste trecute,17 omise,
  zero esecuri. Benchmark real in procese separate:32/32 pasi evaluati.
- Detalii: `scripts/analysis/interval_extrema_method.md`.

### Audit global 2026-10-03

- Reverificare dupa sincronizarea PR #133-135: istorice, scoreri, designuri,
  pipeline-uri, worker separat si UI, pe copie locala izolata.
- Bilet complet 5/40 rafineaza acum locurile suplimentare pentru toate cele
  sase numere extrase. Pastreaza intai constructia livrata, apoi accepta numai
  dominanta exacta; baza garantiei si numarul variantelor raman neschimbate.
  Nu necesita bump worker/WF: actiunea este calculata la cerere, iar cache-ul
  geometric include deja draw_n.
- Nota bruta a selectiei pool-ului nu mai afirma tinta fixa 3+.
- Runnerul experimentului de pool respinge scorurile plate prin validatorul
  comun, numara fallback-urile si semneaza checkpointurile cu datele/codul/
  registry-ul. Protocolul si JSON-ul original raman intacte; rezultatele din
  2 octombrie NU au fost recalculate cu runnerul corectat. Nu se promoveaza
  metode. Rerularile scriu implicit un rezultat separat.
- Python 3.14.7 / Windows: 1978 teste trecute, 17 omise, zero esecuri.
  Starea de productie si sursele verificate sunt intacte.
- Raport: `scripts/analysis/audit_application_report_2026-10-03.md`.

### Audit global 2026-10-01, runda 3

- Corectate explicatiile UI: tinta 3+/4+ per joc, minimum 4+ la 5/40,
  top-1 la Joker Urna 2, cinci numere JUCATE la 5/40, comparatia pe ultima
  extragere dupa antrenare si Wilson z=1 ca scor euristic de selectie.
- UI si raportul afiseaza probabilitatile uniforme exacte 5+ pentru pool si
  pentru cel putin un bilet. WF numara 5+ pe EXTRAGERI distincte si arata
  separat datele/intervalele 4+/5+ pe bilet. Garantia 4 nu este garantia 5.
- Coverele clasice complete de 4, fara plafon, primesc automat schimburi
  geometrice pentru 5+: acelasi pool, acelasi numar de bilete distincte,
  aceeasi acoperire de 4 si profil exact nedescrescator la FIECARE prag si
  FIECARE marime a intersectiei. Limite: pool<=16, pick5/6, draw_n=6,
  cel mult 512 bilete, doua treceri si 16384 candidati, cache-uri limitate.
  Joker draw5/pick5 este exceptat: la acelasi numar de bilete distincte,
  probabilitatea de 5 este invarianta. Explicit greedy si lotto conditional
  raman neschimbate. Dispatch primeste draw_n optional, fara schema noua.
- Worker v10 si WF v29 invalideaza geometria veche. Activarea este automata;
  pornirea normala si o generare noua sunt suficiente.
- Replay temporal retrospectiv: scorerul vede numai extragerile de dinaintea
  zilei tintei; selectia vede numai rezultate de dinaintea blocului evaluat.
  Compara tinta 3/4 si limita de consecutive pe o perioada ulterioara selectiei.
  Nu constituie holdout extern al formulelor dezvoltate pe istoricul disponibil.
  Nicio metoda nu este promovata pe rezultatele perioadei de evaluare.
- Verificare finala: Python 3.14.7 / Windows, 1932 teste trecute, 17 omise,
  zero esecuri. Copie locala stabila; scrierile UI si reset din teste sunt izolate.
- Raport si limite: `scripts/analysis/audit_application_report_2026-10-01_round3.md`.

### Audit global 2026-10-01, runda 2

- Decizia si clasamentul UI calculeaza toate statisticile numai pe ferestrele
  comune de evaluare (`common_window_percentiles` / `filter_window_percentiles`).
  Ferestrele suplimentare ale unui candidat nu-i pot creste rata, Wilson,
  consistenta sau sansele de selectie; nici fallback-ul si ensemble-ul.
- UI, raportul si WF folosesc snapshot-ul dataset-ului din config_json al
  jobului terminat, inclusiv la recuperare. Un upload ulterior nu schimba
  istoricul rezultatului afisat; identitatea tara/joc ramane cea a jobului.
  Snapshot corupt => indisponibil, fara substitutie tacita cu dataset-ul live.
  Decodarea foloseste convert_dates=False, identic workerului, ca ZZ-LL sa
  nu se transforme tacit in LL-ZZ pentru datele ambigue.
- Auditorul WF recalculeaza hiturile pe aceeasi cronologie validata si acelasi
  index dupa eliminarea randurilor invalide; sursa originala pentru hash ramane
  intacta. Ultima extragere UI este ultima cronologic si valida, nu ultimul rand CSV.
  Hitul Joker Urna 2 valideaza strict intregii 1..20, fara trunchiere.
- Hitcover adauga candidati geometrici deterministi, cu 4-subset prioritar si
  3-subset secundar, trei ordini locale si trei treceri de schimburi. Acceptarea
  verifica profilul COMPLET dupa repararea pool-ului: aceleasi bilete distincte,
  fara scadere la vreun prag/intersectie si crestere stricta la 3+/4+.
  Candidatii vechi sunt evaluati primii; 15 din 32 configuratii masurate cresc,
  restul raman identice. Scorerul si pool-ul nu se schimba. Memoizare limitata;
  prima constructie pool 16/plafon 64 a durat pana la 6,752 s local.
- Worker v9 si WF v28 invalideaza geometria si semantica vechi. Activarea
  ramane automata la buget pozitiv, fara comanda PowerShell.
- Raport si limite: `scripts/analysis/audit_application_report_2026-10-01_round2.md`.
  Auditul ruleaza pe o copie izolata cand un Re-Bench scrie in paralel; starea
  si fisierele benchmarkului utilizatorului sunt excluse din commit.

### Audit global 2026-09-15

Audit de cod si de logica, cu accent pe cerinta „niciun filtru deghizat in
metoda". Ce s-a masurat si ce s-a reparat:

- **Metodele nu contin filtre.** Cele 52 de intrari au fost verificate si prin
  citire, si prin masurare, pe 6/49 si 5/40, pe patru ferestre de istoric
  fiecare: 0 din 52 suspecte. Detectorul a ramas in suita ca
  `test_no_structural_filters.py`, deci o metoda noua scrisa neatent nu mai poate
  intra tacut.
- **`alternating_parity` -> `season_period2`.** Metoda lucreaza pe paritatea
  INDEXULUI extragerii (sezonalitate de perioada 2), nu pe paritatea numarului;
  numele vechi se citea ca filtru. Alias in `METHOD_ALIASES`, iar
  `_sanitize_production_name` rezolva alias-ul INAINTE de a verifica registry-ul,
  deci o decizie salvata sub numele vechi nu cade pe `frequency`.
- **Tie-break la cele doua k-NN.** Media a `k` tinte binare are doar `k+1`
  nivele: pe 6/49, `knn_pattern_self` producea 6-8 nivele distincte si 6 din cele
  12 locuri ale pool-ului cadeau pe regula canonica „numarul cel mai mare dintre
  cele egale", nu pe metoda. Cu un termen de frecventa plafonat la un sfert din
  pasul `1/k` (deci incapabil sa rearanjeze doua nivele vecine): 33-43 nivele si
  1 din 12 locuri. Acelasi tratament ca la bump-ul v12, pe vechile metode de
  clasa. Bump `v18 -> v19` (bench) si `v24 -> v25` (WF).
- **Cele doua esecuri „pre-existente" erau ale testului, nu ale motorului.**
  `training_cutoffs` scoate din istoric toate extragerile din ziua tintei —
  istoricul nu retine ordinea intrazilnica. Coada CSV-ului are doua extrageri pe
  13-09-2026, iar testul compara walk-forward-ul cu o generare directa pe
  `df.iloc[:index]`, care dadea pipeline-ului exact extragerea pe care WF n-are
  voie s-o vada; de acolo penalizarea recenta lovea alte numere si pool-ul
  diverga. Testul foloseste acum aceeasi regula de taiere. Comportamentul
  motorului era deja corect si ramane aparat de
  `test_budget_cover.test_same_day_draws_are_not_visible_to_scorer`.
- **Pragul random se realiniaza la metrica pe care se judeca efectiv.** Cand
  `folds.csv` nu are coloana 3+, decizia cade pe 4+, dar `baseline_rate` ramanea
  P(>=3) ~ 0,11 fata de rate de 4+ de ~0,02: nicio metoda nu putea trece poarta,
  toata matricea iesea `low_confidence`, iar `lift` scadea doua marimi diferite.
  Latent pe un folds.csv curent, viu pe unul vechi. Blocat de
  `test_decision_baseline.test_rate_column_fallback_realigns_the_random_threshold`.
- **Acoperirea nu mai poate raporta 100% fals.** Doua gauri in poarta de
  validare: (a) `round(coverage, 2)` ducea 53129/53130 la exact `100.0`, iar
  portile compara `coverage < 100.0`, deci un design cu gaura trecea drept
  complet — sub 20000 de tinte pragul nu se atinge, adica geometriile livrate
  scapau din noroc geometric, nu prin constructie; acum o acoperire incompleta se
  opreste la 99,99; (b) multimea de tinte goala (pool gol, pool mai mic decat
  garantia, conditie peste pool) raporta `100.0` — acum `0.0`, fiindca „nicio
  tinta" nu inseamna „garantie indeplinita". Pool-ul se dedupliceaza inainte de
  numarare.
- **Numerele din pool care nu ajung pe niciun bilet sunt raportate.** 18 din cele
  99 de designuri lotto nu folosesc toate cele `v` pozitii; garantia ramane
  matematic adevarata, dar `hits_union` (hit de POOL) numara numere care nu se
  joaca. Apar acum in `audit["pool_numbers_not_on_tickets"]` si in UI. NU se
  forteaza pe bilete: o substitutie ar strica exact garantia pentru care a fost
  ales designul.
- **UI-ul descrie metodele care ruleaza.** `ui_results._METHOD_DESC` era un
  dictionar paralel ramas cu ~20 de metode sterse — printre care vechile filtre
  `parity_balance` („echilibru par/impar") si `sum_affinity` — si fara niciuna
  dintre cele 50 curente. Descrierea se citeste acum din registry
  (`method_meta`), deci nu mai poate ramane in urma.
- **`get_ensemble_for_game` respecta plafonul de productie.** Implicitul era 3,
  desi `ENSEMBLE_MAX_METHODS` e 1; `ui_bench` explica diferenta 3 -> 1 prin
  „decorelare pe scoruri", ceea ce nu era adevarat (plafonul se aplica in
  `engine/scoring.py`). Implicit e acum chiar `ENSEMBLE_MAX_METHODS`; testele
  care verifica mecanismul de blend cer explicit plafonul.
- **Documentatie aliniata la cod** in `decision.py` si `method_selector.py`:
  „decizia GARANTEAZA ..." (fals pe `low_confidence`, pe celula degeneratii, pe
  substitutia de pool si pe ramura fara intrare), garda de esantion descrisa ca
  „inerta" desi se declanseaza practic la fiecare celula, experimentul de
  reproducere pentru `MIN_CONSISTENCY_WINDOWS` valabil doar pe ramura
  `empirical_random`, si calea gresita pentru `blacklist = set()` (e in
  `engine/pipeline.py`). `avg_hits` e marcat explicit ca statistica selectata
  prin maxim.

Ramase de decis (nu s-au schimbat):

- Acoperirea 100% a unui lotto design „t daca p" nu e acelasi lucru cu 100%
  clasic t-din-t (masurat: L(12,6,4,3) da 54,09% pe metrica clasica). Panoul de
  rezultate, sumarul WF si nota de hituri o spun explicit.

### Audit global 2026-09-13

- `core/history.py` normalizeaza coloanele de numere/date, respinge numele
  ambigue si datele invalide, apoi ordoneaza stabil istoricul. CSV fara date:
  ordinea randurilor ramane contractul cronologic al apelantului.
- Benchmark si WF exclud TOATE extragerile din ziua tintei. Istoricul nu
  inregistreaza ordinea intrazilnica. Cutoff-urile intra in cache-ul de bench;
  datele intra in hash-ul WF si in verificarea de prospetime.
- O metoda cu `n_eval < n_test` pe oricare fereastra nu poate castiga nici
  poarta principala, nici fallback-ul deciziei (`unevaluated_draws`).
- Frecventa este vectorizata cu aceleasi ponderi float32 si aceleasi sume
  float64. Scorurile sunt identice bit cu bit cu referinta; fallback-ul din
  engine apeleaza aceeasi functie. Masurare locala pe istoricul complet:
  30-34x mai rapid pentru aceasta functie, nu pentru intreaga aplicatie.
- Clasamentul fiecarui scorer se calculeaza o singura data pe bloc, apoi se
  folosesc prefixele pentru toate dimensiunile de pool.
- Fara suficiente numere pentru un bilet, wheeling-ul returneaza `([], 0.0)`;
  pipeline-ul refuza un pool mai mic decat biletul. Nu raporta 100% pentru
  bilete goale sau cu numere lipsa.
- Cache-ul WF distinge adancimi si lookback-uri fractionare. Numarul asteptat
  de simulari exclude inceputul fara cinci extrageri anterioare, ca validarea
  de 100% sa poata deveni completa. Cache-ul worker include configuratia,
  decizia, metoda wheel si continutul designului efectiv.
- Testele izoleaza istoricul pool-urilor prin `LOTO_POOL_HISTORY_FILE`;
  scriptul de integrare dezactiveaza explicit tracking-ul.
- `analiza_patterns_last10.py` emite UTF-8 si foloseste universul jocului
  cunoscut, nu maximul observat accidental. Logurile CRLF se citesc ca LF.
- Verificat: 83 combinatii active metoda-joc cu acceptare si top-N identice
  bench/productie; 151 designuri validate exhaustiv; 6 pipeline-uri identice
  inainte/dupa (trei jocuri, buget 0/7); UI HTTP 200 si coada-worker-decodare,
  inclusiv cache-hit, intr-o baza izolata.
- Re-Bench de audit: toate cele 332 folduri reale ale matricei curate,
  percentile 10/30/60/100, block_size=1, fara erori totale; 12 folduri partial
  evaluate sunt explicit excluse din selectie. Rezultatele sunt livrate
  separat: fisierele bench locale deja modificate la inceput nu se suprascriu.
- WF valideaza o decizie fixa curenta. Daca metoda a fost aleasa folosind
  perioada testata, aceasta nu este o validare externa a selectiei metodei.
  `selection_validation="fixed_current_decision"` documenteaza limita.

## 3. Arhitectura

```text
app_nicegui.py
  -> submit_job(config_json)
  -> job_queue.py / loto_jobs.db
  -> worker.py, proces separat
  -> loto_engine.run_institutional_pipeline()
  -> scoring -> pool -> wheeling
  -> rezultat comprimat in coada
  -> ui_shared.decode_queue_result()
  -> UI + raport + walk-forward
```

Worker-ul este independent de UI si poate termina jobul dupa inchiderea paginii.
UI-ul face polling la o secunda, fara reload complet.

### Module cu responsabilitate unica

| Modul | Responsabilitate |
|---|---|
| `app_nicegui.py` | UI facade: configurare, submit, polling, orchestrare WF |
| `ui_runtime.py` / `ui_results.py` / `ui_bench.py` / `ui_hits.py` | Panouri NiceGUI extrase; `_sync_ui_namespace` leaga numele `_` |
| `worker.py` | consumator SQLite, executie pipeline, cache rezultat, requeue la oprire |
| `job_queue.py` | contractul persistent UI-worker |
| `loto_engine.py` | validare productie, scoring, pool unic, wheeling si audit |
| `covering/` + `wheeling_methods.py` | algoritmi de covering design; fatada publica `wheeling_methods` |
| `ui_shared.py` | I/O atomic, lock-uri, payload queue, worker si loguri |
| `loto_enterprise/benchmark/runner.py` | folduri walk-forward si metrici per pool |
| `loto_enterprise/benchmark/methods*.py` | registry (`methods.py`) + `methods_common` si cele 4 module de metode (`methods_recency`, `methods_relational`, `methods_learning`, `methods_wave2`) |
| `loto_enterprise/benchmark/decision.py` | gate vs random, Wilson, ensemble si decizie per pool |
| `loto_enterprise/core/method_selector.py` | citire decizie, sanitizare, decorelare si blend runtime |
| `loto_enterprise/core/ranking.py` | singurul tie-break acceptat pentru top-N |
| `loto_enterprise/core/score_validation.py` | validarea comuna a scorurilor bench/productie |
| `loto_enterprise/core/walk_forward_adapter.py` | WF onest, cache, agregare si acoperire |
| `loto_enterprise/core/draw_validation.py` | contract comun pentru extrageri valide |
| `loto_enterprise/core/lotteries.py` | registrul loteriilor: identitate (tara, `game_id`, cheia de bench), geometrii, tarife, cai de decizie/bench per tara |
| `_ISTORIC/` | sursa versionata a datelor de benchmark |

## 4. Contracte care nu se negociaza

### 4.1 Date

- O extragere valida are exact `draw_n` valori intregi, distincte si in interval.
- Doua randuri consecutive cu aceleasi numere principale sunt o copie, nu o
  extragere: `test_externe_history.py::test_no_draw_repeats_the_previous_one`
  le respinge in toate CSV-urile din `_ISTORIC/` si `_ISTORIC/externe/`. O
  repetare reala (sub 1e-6 pe rand la orice joc de acolo) se confirma intai pe
  arhiva oficiala.
- Engine, benchmark si walk-forward folosesc `draw_validation.py`.
- `update_csv.py` (loto49.ro, la fiecare pornire) nu scrie nimic pentru un joc,
  cu linia `EROARE verificare: <motiv>. NU scriu nimic.` si jocul la
  NEACTUALIZATE (ACTUALIZARI si START_8000 avertizeaza in consola), cand: un
  rand de pe site datat la sau dupa ultima extragere stocata e invalid
  (numere repetate, in afara intervalului, Joker gresit, data imposibila sau
  in viitor) ori necitibil (o data fara rand potrivit dupa ea: cifra in plus,
  litera, celula lipsa; fiecare numar se termina inaintea unei cifre sau
  cratime, deci o celula lipsa nu mai imprumuta „20” din anul urmator); zilele
  comune site-CSV din ultimele 60 de zile (`CHECK_DAYS`) difera (comparate pe
  data ZZ-LL-AAAA si Joker numeric; in ziua ultimei extrageri stocate site-ul
  poate avea in plus a doua extragere a zilei, dar un rand stocat schimbat pe
  site nu mai intra drept a doua extragere); un rand nou repeta numerele
  principale ale randului anterior (ultimul stocat sau randul nou dinainte);
  pagina nu contine nicio extragere (nu mai e raportata „la zi”). Diferentele
  mai vechi de 60 de zile nu blocheaza (pagina 5/40 merge pana in 1995 si are
  inca 24-10-2024 gresit). O corectura locala din ultimele 60 de zile,
  nepreluata de loto49.ro, opreste update-ul jocului pana la corectura
  site-ului sau iesirea zilei din fereastra; mesajul spune sa se adauge manual
  extragerile noi.
- `update_externe.py`: parserul spaniol accepta ziua si luna fara zero in fata
  (`Jue-1-10-2026`); inainte pierdea zilele 1-9 ale fiecarei luni (CSV-ul
  versionat nu are 01, 03 si 05-10-2026; prima rulare ACTUALIZARI le adauga).
  `plan_update` refuza si o zi stocata, din fereastra de 60 de zile si din
  intervalul acoperit de sursa, pe care sursa n-o mai listeaza.
- Joker Urna 2 accepta numai valori intregi 1..20.
- `_ISTORIC/` este versionat; fisierele de stare si cache nu sunt surse de adevar.
- `_ISTORIC/externe/` tine istorice ale altor loterii din UE, in acelasi format
  (`date,n1..n6`, ZZ-LL-AAAA), cu sursa si verificarea fiecarui fisier in
  README-ul folderului. Nu pune un CSV strain direct in `_ISTORIC/`:
  `discover_games` ia primul fisier cu „649” in nume, in ordine alfabetica, si
  ar inlocui pe tacute Loto 6/49 in bench. `test_externe_history.py` permite la
  nivelul de sus numai cele trei fisiere romanesti, iar numele straine nu contin
  „joker”, „649”, „6_49”, „5_40”, dupa care codul vechi ghiceste jocul.
- Identitatea unui joc (tara ISO, `game_id`, cheia de bench) vine NUMAI din
  registrul `loto_enterprise/core/lotteries.py`; `game_type` ramane GEOMETRIA
  („6/49”, „5/40”, „joker”, „6/45”, „5/50”). Jocurile romanesti pastreaza
  id-urile si cheile de dinainte (`6/49` → `loto_6_49` etc.). Un task fara
  `country` este Romania, exact ca inainte; un task strain are `game_label` =
  id-ul jocului si `country`, iar o tara/un joc necunoscut face jobul FAILED.
  Decizia unei alte tari se citeste numai din `decisions/<CC>/best_methods.json`
  (cu `_meta.country` = tara; altfel e tratata ca lipsa → `frequency`, marcat
  in audit cu `fallback`/`no_decision`). Cheile pool_history/adaptive primesc
  prefixul `<CC>_<game_id>_` numai in afara Romaniei.
  `test_lotteries_registry.py` verifica fiecare tabel romanesc vechi fata de
  registru.

### 4.2 Scoruri si ranking

- Scorer: `fn(draws_2d, max_num) -> {numar: scor}`.
- Scorurile goale, plate, ne-numerice sau ne-finite sunt inutilizabile.
- Validarea trece prin `has_usable_score_variance`.
- Orice top-N dupa scor trece prin `core.ranking.rank_by_score`. Limita de
  consecutive (`limit_consecutive_run`) parcurge iesirea lui, fara sortare proprie.
- Maparea pozițiilor unui covering design pe pool folosește aceeași ordine,
  fără frecvență: scor descrescător, la egalitate numărul mai mare
  (`covering.common._sorted_pool`).
- Nu adauga sortari locale care pot schimba tie-break-ul dintre bench si productie.
  Nici in interiorul unei metode: o metoda de productie urmeaza numerele, nu
  etichetele lor (reetichetarea istoricului muta scorul odata cu numarul,
  `test_no_structural_filters.test_production_method_follows_the_numbers_not_their_labels`).
- Fallback-ul de productie este `frequency`, determinist. O intrare Auto-Pilot
  al carei scorer e interzis sau necunoscut cade pe `frequency`, nu pe vechii
  castigatori dupa `avg_hits` (`winners_per_pool_best`, `overall_winner`), care
  n-au trecut poarta fata de random; acestia raman rezerva numai fara
  `auto_pilot_per_pool`. Auditul (`bench_winner`) marcheaza rezerva: `fallback`,
  `no_decision` (si la Romania fara decizie) sau `attempted` (numele respins).
- `random` este baseline structural pentru benchmark si este interzis in productie.
- `neighbor_adjacent`, `repeat_last_draw`, `rwr_last_draw` si `haar_multiscale`
  raman in registry pentru bench si sunt interzise in productie
  (`EXCLUDED_FROM_PRODUCTION`).
- Pe o urna cu o singura bila (Joker Urna 2 si echivalentele straine),
  `markov_self_state` si `vlmm_self_k3` aleg bila precedenta in 66%, respectiv
  63% din pasi chiar pe extrageri uniforme (5% la intamplare). Decizia le sare
  la `draw_n == 1`, iar productia le respinge pe Urna 2
  (`EXCLUDED_FROM_SINGLE_PICK`). Pe jocurile cu pool raman metode de recenta,
  judecate de bench. `test_no_structural_filters` masoara rata pe extrageri
  uniforme si opreste orice alta metoda care ar repeta bila in majoritatea
  pasilor.

### 4.3 Metode active si curate

- Registry-ul (`methods.py`) incarca cele patru module de metode prin
  `methods_common.make_registry`; o coliziune de nume intre module se logheaza,
  nu se ascunde. Nu mai exista mecanism de tombstone (`disabled_methods.json`):
  o metoda stearsa dispare din cod, iar un nume necunoscut cade pe `frequency`.
- Nu reintroduce metode GPU/neural, nici filtre structurale (paritate, sume,
  decade, pozitie, secvente) deghizate in metode: acelea constrang combinatia,
  nu prezic un numar. Daca un astfel de filtru intra in METHODS, intra si in
  `EXCLUDED_FROM_PRODUCTION` (ramane martor de bench, nu scorer). Azi:
  `neighbor_adjacent`, `repeat_last_draw`, `rwr_last_draw`, `haar_multiscale`.
  Limita de consecutive (§6) nu e o metoda: e o optiune explicita a
  utilizatorului pe compozitia pool-ului, aplicata dupa scor, absenta din bench,
  fara afirmatie predictiva.
- `test_no_structural_filters.py` masoara scorurile pentru clase statice;
  nu constituie dovada unui avantaj predictiv.
- `curated_methods.json` este reversibil si controleaza costul benchmarkului;
  azi contine toate metodele de productie + `frequency` pe fiecare joc (fara
  preselectie pe istoric). Exceptie: `per_game.joker_urna2` nu are
  `markov_self_state`, `vlmm_self_k3` (bila precedenta, §4.2) si
  `naive_bayes_last`, care dadea pe Urna 2 acelasi clasament ca `markov_pairs`
  (658 din 658 de pasi). `random` si `frequency` trebuie sa ramana in lista activa.
- Un run CLI cu `--quick`, `--methods`, sub trei ferestre (`--percentiles`),
  pe alt `--istoric` sau cu `--block-size` diferit de 1 nu trebuie sa rescrie
  decizia de productie fara `--force-decision` (`reduced_run_reason`).

### 4.4 Persistenta si concurenta

- JSON-urile de stare se scriu numai atomic, prin `ui_shared`.
- Progresul cozii se actualizeaza numai pentru un job `RUNNING`, printr-un
  singur UPDATE atomic; `CANCELLED`, `PENDING` reprogramat sau jobul disparut
  cer workerului sa se opreasca si nu au voie sa-si piarda logul de stare.
- Nu scrie `pool_history.json` din pasi WF/backtest.
- Ultimul job COMPLETED se reafiseaza la fiecare pornire a UI-ului
  (`_recover_completed_job`), cu avertismentul „Rezultate RECUPERATE” si
  validarea WF din cache (§8); finalizarea (WF calculat, mail, shutdown) se
  face cel mult o data. Jobul preluat de UI se marcheaza pe randul lui din
  baza statiei (`jobs.ui_finalized_at`, `mark_job_finalized`), nu in
  `.ui_state.json`: checkout-ul se poate sincroniza intre statii, iar id-urile
  pornesc de la 1 pe fiecare. Un job marcat se reafiseaza fara un nou marcaj
  si fara a rescrie `raport_complet.txt` (raportul sesiunii care l-a
  finalizat, cu WF-ul de atunci, ramane).
  Golirea cozii sterge marcajul odata cu jobul.
  Cheia veche `last_finalized_job_id` se migreaza o data la pornirea UI
  (ultimul job COMPLETED cu acel id) si dispare din fisier numai dupa ce
  marcajul a ajuns in baza; daca baza refuza scrierea, cheia ramane, iar
  recuperarea din pornirea curenta trateaza jobul ca preluat (numai afisare).
  In teste, `conftest.py` redirectioneaza marcarea spre o baza temporara.
- `reset_jobs.py --force` (START_8000, pasul [2b/4]) pastreaza ultimul job
  COMPLETED, preluat sau nu de UI: marcajul `ui_finalized_at` opreste deja un
  al doilea mail, WF calculat sau oprire. Sterge joburile COMPLETED mai vechi
  si resturile PENDING/RUNNING; numerotarea reincepe de la #1 numai fara niciun
  job COMPLETED. Pana la 2026-10-08 il stergea dupa prima afisare, deci a doua
  zi START_8000 pornea fara pool si cu „📜 Istoric hits” gol.
- Avertismentul „Rezultate RECUPERATE” arata ora locala a finalizarii
  (`completed_at` e UTC, CURRENT_TIMESTAMP), ZZ-LL-AAAA HH:MM
  (`_completed_local_text`).
- Bench-ul scrie `best_methods.json` o singura data (`write_bench_decision`):
  matricea Auto-Pilot se construieste in memorie, apoi castigatorii, matricea
  si semnaturile (`freshness.stamp_signatures`) intra intr-o singura scriere
  atomica sub `file_lock`. Inainte fisierul aparea intai fara
  `auto_pilot_per_pool` (productia juca `winners_per_pool_best`, fara poarta
  fata de random), cu semnaturile deja stampilate. Daca matricea esueaza,
  decizia anterioara ramane neatinsa, fara semnaturi noi, iar bench-ul iese cu
  1. Regulile de rulare redusa (§4.3) raman neschimbate.
- Nu schimba schema `config_json` sau payload-ul queue fara migrare si teste E2E.
- Nu folosi fisiere temporare cu nume fix pentru scrieri concurente.
- Pasii walk-forward paraleli primesc setarile pe NUME (`_wf_worker_step` ia un
  dict). Nu reintroduce tuplul pozitional: o setare noua ajungea in parametrul
  vecin daca unul dintre cele trei locuri care il consumau ramanea nesincronizat.
  Ramura paralela reala (`_stateless`, adica `use_feedback=False` si
  `enable_hard_inversion=False`) e activata in teste numai explicit; restul
  suitei forteaza `_wf_max_workers` la 1, deci o regresie acolo nu apare de la
  sine. Mai rau, handler-ul exterior prinde exceptia si reia TOT secvential, cu
  rezultate corecte — paralelizarea dispare in tacere. Un test pe ramura aceea
  trebuie sa verifice ca avertismentul „WF rapid indisponibil" LIPSESTE, nu doar
  ca rezultatele sunt bune. O eroare per pas (parametru lipsa in semnatura) nu
  declanseaza fallback-ul: pasul e prins si sarit, iar WF iese cu zero
  predictii. Testul cere deci si predictii nenule, plus invariantul setarii
  (ex. `max_consecutive_run`: cel mult 2 consecutive in `hard_core`).

### 4.5 Git

- Lucrul de productie se integreaza pe `main`.
- Utilizatorul a cerut (2026-09-28) ca orice schimbare terminata si verificata
  (suita completa verde) sa fie integrata pe `main` fara sa astepte confirmare:
  PR, apoi merge imediat.
- `scripts/git-hooks/post-commit` face push pe `origin/main` dupa fiecare commit
  pe `main` (fara force; `LOTO_SKIP_AUTO_PUSH=1` il opreste; nu retrimite
  commit-uri retrase de pe `origin/main`). `START_8000.bat` si
  `ACTUALIZARI.bat` setea `core.hooksPath` la `scripts/git-hooks`.
- Lansatoarele transfera executia FARA CALL intr-o copie temporara imuabila.
  `scripts/launcher_git.ps1 -Mode Sync`, copiat impreuna cu lansatorul, face un
  sync, apoi ruleaza lansatorul din repo actualizat. Codul si lansatoarele se
  actualizeaza impreuna; nu se descarca fragmente cu curl. Sync si PushHistory
  ruleaza sub un lacat exclusiv (`.git\loto-sync.lock`, deschis fara partajare,
  eliberat de sistem si la oprirea brusca); un al doilea lansator nu atinge
  nimic. Sync alege un plan:
  - `ff`: numai commit-uri noi pe `origin/main` -> `merge --ff-only`;
  - `push`: numai commit-uri locale -> push (si cu un commit de merge);
  - `rebase`: ambele -> commit-urile locale se repun peste `origin/main`, numai
    daca nu contin merge-uri si niciun commit nou de pe `origin/main` n-a fost
    varful lui `main` local (reflog-ul `refs/heads/main`: amend sau reset al
    unui commit trimis); altfel integrare manuala, cu motivul; un conflict
    lasa codul neschimbat si spune pasii (`git rebase origin/main`, apoi
    `--continue` si push);
  - `drop`: `main` trece pe `origin/main` (`reset --keep`, vechiul `main` in
    `refs/loto-sync/main-<data-ora>`) cand toate commit-urile locale au fost
    retrase de pe `origin/main` (force-push) sau numai adauga la
    `_ISTORIC/*.csv` randuri deja prezente acolo (doua statii au trimis
    aceleasi extrageri). Un rand sters, modificat sau mutat e o corectura si
    merge la `rebase`. Commit-urile retrase se recunosc din reflog-ul
    `refs/remotes/origin/main` si se tin minte in `refs/loto/withdrawn/`, ca
    regula sa nu expire; amestecate cu commit-uri proprii raman manuale. Nici
    Sync, nici PushHistory, nici hook-ul post-commit nu le retrimit;
    PushHistory nu comite extragerile noi peste ele (verificare inaintea
    commit-ului automat si inaintea rebase-ului).
  Modificarile necomise nu blocheaza si nu se pierd. La `ff`, fisierele pe
  care actualizarea nu le atinge raman pe loc; cele schimbate si local, si pe
  `origin/main` (la `rebase`/`drop`, toate cele modificate) se copiaza octet
  cu octet in `.git\loto-sync-backup\<data-ora>-<pid>\local\`, cu versiunea
  din HEAD in `base\`, revin la HEAD, apoi revin peste actualizare: un fisier
  neatins de ea isi ia copia exacta (si binar); celelalte se combina cu
  `git merge-file` pe copii temporare, cu sfarsitul de linie normalizat (CRLF
  din checkout fata de LF salvat de editor nu e conflict). Niciun marker de
  conflict nu ajunge pe disc. La conflict real codul ia `origin/main`, copia
  locala ramane in `local\`, iar marcajul `CONFLICTS` o reaminteste la
  fiecare pornire pana la stergerea folderului; `bench_results/` (Re-Bench-ul
  statiei) nu se combina, ramane cel local. La pornire, fisierele lui apar
  separat („Re-Bench-ul statiei ramane local”), nu printre „Modificari locale
  necomise”, si nu intra in numaratoarea de la final („celelalte N fisiere”). Fisierele noi din index revin cu
  `git add -f`; starea din index a celor modificate nu se pastreaza
  (continutul, da). Sync-ul se opreste, cu motivul si fara sa atinga ceva,
  pentru: un folder in locul unui fisier urmarit sau un fisier in locul unui
  folder din cale, o versiune in index diferita de disc, un fisier scos din
  index cu `git rm --cached`, o redenumire doar de majuscule, un fisier
  neurmarit sau ignorat pe care `origin/main` il aduce cu alt continut (la
  orice plan si inaintea rebase-ului PushHistory: rebase-ul scrie tacut peste
  cele ignorate; cel identic, ramas dintr-o actualizare oprita, se sterge si
  revine daca actualizarea nu se face; un fisier urmarit sub alte majuscule nu
  se numara), un fisier scos din urmarire de commit-urile locale si pastrat pe
  disc (la `rebase`, repunerea stergerii l-ar lua). Dupa integrare se verifica
  starea reala: un rebase oprit (conflict, limita de timp) se anuleaza intai;
  fisierele se pun la loc numai cu HEAD pe `main`, la commit-ul de start sau
  la cel nou. Altfel copia ramane cu `PENDING`, iar mesajul spune pasii
  (`git rebase --abort`). Un `ff`/`drop` refuzat la jumatate (un CSV tinut
  deschis in Excel) readuce la HEAD numai ce a scris git (continutul exact de
  pe `origin/main`; o salvare facuta intre timp de Re-Bench sau de editor
  ramane), scoate fisierele aduse si o spune. La
  pornirea urmatoare, inaintea verificarii ramurii, un rebase neterminat se
  anunta, iar o copie `PENDING` (fereastra inchisa la jumatate) se pune la loc
  automat prin aceeasi fuziune; commit-ul de start, scris in `HEAD` inaintea
  oricarei atingeri, recunoaste si fisierul readus la HEAD inainte de copia
  `base\`; cel inca nereadus are deja continutul local. `RESTORE-FAILED` se
  reaminteste. Iesirea git se citeste in UTF-8 (diacritice
  in numele fisierelor). `LOTO_GIT_TIMEOUT_SECONDS` scade limita de timp numai
  in teste. Alta ramura decat `main` nu se atinge. Nu se sterg fisierele .bat
  personale si nu exista reset fortat sau `git stash`.
- Pe un folder sincronizat in cloud (Google Drive, OneDrive, Dropbox),
  `launcher_git.ps1` cere o data fixarea offline (`attrib +P /S /D`, marcaj in
  `.git\loto-offline-pin`) si ridica limita pe comanda git de la 45 la 180 s.
  Fixarea esuata se raporteaza cu pasul manual si nu opreste sincronizarea.
  Recomandarea ramane repository-ul pe disc local.
- `ACTUALIZARI.bat` verifica Git for Windows inainte de sincronizare
  (`launcher_git.ps1 -Mode EnsureGit`): lipsa -> `winget install --id Git.Git`,
  prezent -> `winget upgrade`, apoi raporteaza versiunea. Git-ul portabil din
  Codex nu conteaza ca instalare. Un Git ales prin `LOTO_GIT_EXE` sau aflat in
  PATH, instalat altfel (scoop, alt folder), se pastreaza, fara un al doilea Git.
  Un cod winget necunoscut cu versiunea neschimbata se raporteaza neutru
  (verificare esuata, confirmare de administrator refuzata sau instalare
  esuata), nu drept „nu a putut verifica". Pasul nu blocheaza niciodata (iese
  cu 0); fara winget indica instalarea manuala. `Resolve-LotoGit` (Sync,
  PushHistory) pastreaza ordinea `LOTO_GIT_EXE`, PATH, Git for Windows, dar
  git-ul Codex din PATH-ul unui editor trece dupa Git for Windows. Prima rulare dupa
  actualizarea care aduce pasul porneste din versiunea veche, deci faza de dupa
  sync il face atunci; marcajul `git-checked` din copia temporara impiedica
  dublarea, iar Cleanup il sterge odata cu copia. START_8000 nu verifica Git.
  Testele de lansator folosesc un winget simulat (`LOTO_WINGET_EXE`), ca sa nu
  actualizeze Git-ul statiei, si numara rularile EnsureGit dupa linia de antet
  „[GIT] Verific Git for Windows", nu dupa apelurile winget.
- Auto-commit-ul de istoric (`launcher_git.ps1 -Mode PushHistory`) publica numai
  extrageri ADAUGATE. Pregateste doar fisierele urmarite (`git add -u -- _ISTORIC`):
  o copie de conflict din cloud (`loto_6_49 (1).csv`) sau orice fisier nou ramane
  local. Apoi `git diff --cached --numstat --no-renames -z` refuza, per fisier,
  calea care nu e CSV, continutul binar, fisierul sters si orice linie stearsa sau
  modificata (rand sters ori trunchiat, CSV rescris de Excel cu `;` si date
  `10.09.2026`): actualizatoarele doar adauga randuri. CSV-urile ramase trec prin
  `verifica_istoric.py` (registrul loteriilor, antetul geometriei, date
  ZZ-LL-AAAA scrise canonic, nedescrescatoare si cel mult cu o zi dupa azi (un
  an tastat gresit, 2062, devenea ultima extragere si oprea `update_csv` pe
  toate statiile), `valid_draw_matrix` pe tot fisierul, inclusiv a doua urna, fara
  rand care repeta numerele celui anterior, §4.1), cu Python-ul venv-ului primit
  de la lansatoare prin `-PythonExe`. Fara venv raman
  verificarile git, cu mesaj; o validare care nu ruleaza pana la capat (cod de
  iesire nenul, timeout) nu accepta nimic. Fiecare refuz apare ca
  `[GIT] [REFUZAT] <fisier> - <motiv>`, iese din index (`reset -q`; fisierul
  ramane neatins pe disc, iar un commit manual nu-l preia pe tacute) si nu
  opreste pornirea (exit 0); refuzul se repeta la fiecare pornire, pana la
  rezolvare. O corectura voita (un rand gresit) se comite manual.
  Fisierele acceptate se comit cu `commit --only -- <fisiere>`: verifica `main`,
  nu include cod deja staged, iar caile explicite tin afara fisierele refuzate
  (`--only` citeste arborele de lucru). Push-ul esuat se reincearca chiar fara
  extrageri noi, inclusiv langa o schimbare refuzata. Inainte de push face
  `fetch`. Daca doar `_ISTORIC` a divergat si arborele e curat, commit-ul este
  repus peste `origin/main`; la conflict, `rebase --abort`. Modificarile
  necomise, inclusiv o schimbare refuzata a unui fisier urmarit, opresc numai
  acest rebase al PushHistory; Sync le pune deoparte si integreaza oricum. Un
  `packed-refs.lock` fara proces `git` este sters. Hook-ul de auto-push este
  oprit pentru acest commit: push-ul este executat o data. Testele
  (`test_launcher_git.py`) ruleaza validarea cu Python-ul suitei, pe un
  `loto_6_49.csv` din registru. Pe Linux/macOS helper-ul ruleaza sub PowerShell
  7 (`pwsh` in PATH sau `LOTO_PWSH`), cu Git dat prin `LOTO_GIT_EXE`; numai
  lansatoarele CMD si modelul PATH-ului Windows raman teste de Windows.
- Nu include in commit stari locale sau cache-uri fara cerere explicita.
- `best_methods.json`, `pool_history.json`, `raport_complet.txt`, logurile,
  baza SQLite si pickle-urile WF sunt runtime state.
- `bench_results/folds.csv` si `bench_results/report.json` sunt tracked, dar se
  comit numai cand reprezinta un Re-Bench complet si intentionat.
- Nu suprascrie modificarile locale ale utilizatorului.

## 5. Benchmark si decizie

### Jocuri si tinte

| Cheie | Geometrie | Tinta deciziei |
|---|---:|---|
| `loto_6_49` | 6/49 | 3+ implicit sau 4+ |
| `loto_5_40` | 6 extrase din 40, bilet de 5 | mereu 4+ (3 numere nu aduc premiu) |
| `joker_urna1` | 5/45 | 3+ implicit sau 4+ |
| `joker_urna2` | 1/20 | top-1, independent de tinta globala |

`LOTO_BENCH_TARGET` accepta numai 3 sau 4 pentru jocurile de pool. Orice alta
valoare este clampata. Tinta efectiva per joc vine din
`hit_target.game_hit_target`: 5/40 are minim 4 (`GAME_MIN_HIT_TARGET`), deci
selectorul global 3 nu il coboara, iar 4 il lasa pe 4. Geometria
(extrase, bilet) per joc este `hit_target.GAME_DRAW_PICK`; decizia o ia de acolo
pentru jocurile cunoscute, chiar daca un `best_methods.json` vechi are
`draw_n = 5` la 5/40. Urna 2 scrie si consuma `rate_1plus_k1`; baseline-ul
aleator exact este 5%.

### Selectia unei metode

Pentru fiecare joc si pool:

1. se folosesc numai folduri reale, valide si metode existente;
2. toti candidatii se judeca pe acelasi set de ferestre; o metoda cu o fereastra
   lipsa (fold `failed`) iese din decizie si apare in `incomplete_methods`;
3. o metoda a carei taietura top-K cade intr-un grup de scoruri egale in cel
   putin 50% din blocuri (`tiebreak_kN`, bench v17) este exclusa ca dependenta
   de tie-break si apare in `tiebreak_dependent`; pe folds vechi fara coloana,
   poarta nu se aplica si `tiebreak_gate_applied` este `false`;
4. metoda trebuie sa bata referinta in cel putin 60% din ferestre pe aceeasi
   rata folosita la decizie; referinta este rata asteptata hipergeometric a
   unui pool aleator (`expected_random_rate`, 5% pentru Urna 2), nu o singura
   realizare `random`; randul `random` ramane in folds ca verificare de
   sanitate (`random_empirical_rate`) si este seedat determinist din istoric;
5. rata este pooled pe `n_eval`, cu fallback per rand pe `n_test`;
6. incertitudinea este evaluata prin limita Wilson cu n efectiv Kish;
7. metodele calificate sunt ordonate dupa Wilson, lift, consistenta si nume,
   deci decizia nu depinde de ordinea randurilor din `folds.csv`;
8. productia foloseste doar castigatorul unic (`ENSEMBLE_MAX_METHODS = 1`):
   ratele individuale din `folds.csv` nu dovedesc performanta unui blend
   nevalidat separat. Masurat pe Joker k11 (Re-Bench walk-forward real, 654
   extrageri): membri calificati separat, combinati, au dat 6.73% sub random
   8.53%, desi primul membru bătea random clar (11.16%). Schema `ensemble`
   din `best_methods.json` ramane cu un singur element, pentru compatibilitate
   engine/UI/cache — plafonul creste numai daca benchmarkul ajunge sa evalueze
   blendul direct (scoruri/pool per pas), nu doar ratele individuale;
9. lipsa metricei sau lipsa metodelor calificate produce `low_confidence` si
   fallback conservator, nu o afirmatie de avantaj statistic;
10. poarta de consistenta pe ferestre imbricate spune putin: un singur
   eveniment in ultimele 10% conteaza in 3 din 4 ferestre, iar dintre ~50 de
   candidati unul trece aproape sigur din noroc. Fiecare celula primeste deci
   `multiplicity`: test binomial unilateral pe fereastra completa fata de rata
   aleatoare (`excess_p_value`), cu corectia Holm peste candidatii celulei
   (`holm_adjusted`, `MULTIPLICITY_ALPHA = 0.05`). Scorerul ales si
   `low_confidence` nu se schimba; `proven = false` apare in rationale, in
   clasament, in panoul de rezultate, in mailul de rezultate si in notificarea
   Auto-Pilot („avantaj
   nedemonstrat”). Corectia nu se face si peste celulele unui joc: pe
   `folds.csv` versionat, 45 din 46 de celule sunt nedemonstrate, iar singura
   sub prag (6/49 k11, Holm 0,029) nu ar trece una.

### Coerenta outputului

- Textele din Control Executie urmeaza tinta selectata fara reincarcarea paginii.
  Mesajele de prospetime se recalculeaza dupa Re-Bench, inclusiv cu Auto-Pilot
  oprit, dupa schimbarea tintei si dupa reincarcarea manuala a istoricului.
  Se redeseneaza numai aceste mesaje; controalele si sectiunile deschise
  de rezultate raman intacte. O nepotrivire fara extrageri noi poate veni
  si din metode, deci nu este descrisa automat drept CSV schimbat.
- Schimbarea tintei recalculeaza decizia numai pe un `folds.csv` care acopera
  Re-Bench-ul deciziei (`_meta.methods_tested_per_game` × `_meta.percentiles`,
  `decision.missing_decision_folds`, `require_complete=True`). Un flush partial
  al runner-ului, un bench oprit sau `--quick` lasa decizia neatinsa, cu
  avertisment. Cat ruleaza un bench, recalcularea se amana pana la
  `_on_bench_finished`.
- Decizia salvata isi poarta tinta: fiecare celula are `hit_target`, iar
  `attach_auto_pilot_matrix` (folosit de `update_best_methods_with_auto_pilot`
  si de bench) stampileaza `_meta.bench_hit_target`.
  `decision.decision_target_mismatch` compara celulele (fara `hit_target`,
  stampila) cu tinta efectiva a jocului (5/40 ramane 4+, Urna 2 top-1). Un
  bench din consola pe alta tinta, o schimbare de tinta refuzata (folds
  incomplet) sau UI-ul oprit cat rula bench-ul lasau decizia pe 3+ cu 4+
  selectat, iar panoul spunea „Benchmark la zi”. La pornire, verificarea e
  imediata, iar recalcularea (`_reconcile_decision_target`, ~10 s) ruleaza
  dupa deschiderea portului (`_after_server_start`); cat ruleaza un bench, se
  amana la `_on_bench_finished`, care o face si fara marcajul din memorie. Pe
  un `folds.csv` incomplet decizia ramane, iar panoul de prospetime afiseaza
  „Decizia Auto-Pilot salvata nu e pe tinta selectata” in locul lui „Benchmark
  la zi”. `bench_all_methods.py` fara `LOTO_BENCH_TARGET` ia tinta din
  `.ui_state.json` (`bench_hit_target`), altfel 3, si o afiseaza.
- Nota Holm / low_confidence din panoul de rezultate si linia RATING din mail
  citesc intrarea deciziei la pool-ul cu care s-a ales metoda
  (`bench_winner[...].pool_hint`, nu pool-ul efectiv, pe care restrangerea
  bazei il poate micsora) si apar numai cand decizia de acum alege metoda din
  auditul rezultatului (`_decision_entry_method`, aceeasi curatare ca
  productia). Altfel panoul spune „Decizia de bench s-a schimbat dupa
  generare”, iar mailul „RATING: indisponibil”. La rezerva motorului
  (`fallback`) nota lipseste: motivul e afisat deja.
- Clasamentul preia `ranked_methods` din decizia recalculata pe snapshot-ul
  afisat. Metodele excluse pentru egalitati la limita top-K sau ferestre lipsa
  raman vizibile cu motiv, fara rang. Trofeul arata primul eligibil; tinta arata
  metoda din ultima generare (numai la acelasi pool) sau din decizia salvata.
- Analiza foloseste pool-ul rezultatului afisat, chiar daca setarea pentru
  generarea urmatoare s-a schimbat. Urna 2 arata doar top-1, fara coloana 4+.
- Penalizarea recenta/lookback-ul/limita de consecutive sunt explicate separat:
  bench-ul masoara scorerul brut, WF masoara configuratia ajustata. Scorurile nu
  sunt probabilitati de castig.
- WF afiseaza distinct hiturile pool-ului si extragerile cu cel putin un bilet
  care atinge 3+/4+. Metadatele indica geometria interna WF, care poate diferi
  de garantia/bugetul productiei; adaugarea nu invalideaza cache-ul.
- Istoricul WF (`📜 Istoric hits`) foloseste ACELASI `restrict_base_min/max` care
  a produs pool-ul afisat: `run_honest_walk_forward` primeste intervalul prin
  `_wf_generation_options(data)`, citit din audit-ul rezultatului, nu din
  setarea live din sidebar (care se poate schimba intre timp). Sectiunea de
  istoric afiseaza explicit intervalul aplicat (`_restrict_base_text` pe
  `data.get("audit")`), langa tabel, nu doar in nota de sub clasamentul bench —
  fara restrictie, sectiunea nu arata nicio mentiune. La fel limita de
  consecutive: `_wf_generation_options` o ia din ecoul worker-ului
  (`data["max_consecutive_run"]`), apoi din limita CERUTA din audit, apoi 0,
  niciodata din bifa din sidebar; istoricul o numeste (`_consecutive_limit_text`).
- Costurile folosesc tarife standard (sursa loto.ro/info-loto/preturi, verificata
  2026-09-04). Primele 10 variante nu mostenesc garantia intregului wheel;
  minimalitatea numarului de bilete nu este afirmata fara dovada.
- Raportul afiseaza acoperirea productiei si foloseste `ranking_scores_top25`
  pentru cheia istorica `timesfm_predictions`, fara a modifica payload-ul.

Audit reproductibil read-only, cu randare capturata din functiile NiceGUI:
`python scripts/analysis/audit_output.py` (92 clasamente la tintele 3/4, variante
si acoperire, hituri WF recalculate direct din CSV). `--report CALE` salveaza
explicit o copie randata a raportului. Ultima verificare: 103734 intrari WF,
zero discrepante de hituri, pool-uri k11 cu 34/66/66 variante si acoperire 100%.

### Doua filtre de redundanta

- In `decision.py`: Pearson semnat pe semnaturi de performanta, prag 0.99.
- La runtime: Spearman in modul pe vectorii de scor, prag 0.95.

Ele masoara lucruri diferite. Cu productia limitata la un singur castigator
(punctul 8 de mai sus), niciun filtru nu mai are ce elimina in practica; raman
in cod pentru cazul in care plafonul creste pe baza unei dovezi ca un blend
evaluat direct bate castigatorul unic.

### Re-Bench onest (per extragere)

Re-Bench-ul din UI ruleaza implicit cu `--block-size 1`: scorerul se
recalculeaza inaintea FIECAREI extrageri testate, la fel ca validarea
walk-forward afisata dupa generare. Varianta veche (`block_size=99999`, un
singur scor per fereastra) putea alege un castigator care trecea poarta din
bench dar pierdea fata de random in WF real — cazul masurat mai sus la punctul
8. Costul e un Re-Bench mult mai lent; ETA-ul afisat langa butonul RE-BENCH se
auto-calibreaza din `runtime_sec`-ul ultimei rulari (`_estimate_bench_eta`),
deci prima estimare dupa schimbarea de default e optimista. Tinta de folduri
(`_target_bench_folds`) reproduce exact poarta din `bench_all_methods.py`:
matricea restransa per joc (`resolve_methods_per_game`) se aplica DOAR cu
curarea activa; altfel Re-Bench-ul ruleaza toata lista pe fiecare din cele 4
jocuri, si estimarea trebuie sa reflecte asta, nu doar un numar plauzibil —
verificat explicit ca „fara curare" da o tinta mai mare, nu aceeasi.

Controlul pe istoric amestecat (`is_random=True`, ~50% din timpul de bench)
este sarit implicit in UI (`--no-shuffled-control`): alimenteaza doar
diagnosticul `lift_vs_shuffle` si tie-break-ul legacy `winners_per_pool`, nu
decizia de productie (`decision.py` filtreaza peste tot `is_random == False`).
Baseline-ul `random` (metoda, nu controlul amestecat) ramane obligatoriu si
prezent in folds. Cand controlul amestecat lipseste, campurile lui din raport
sunt `null` (indisponibile), nu zero; mediile agregate sunt ponderate cu
`n_eval`/`n_test`, identic cu decizia si clasamentul UI.

Lista `active` = toate cele 50 de metode + `frequency`, aceleasi pe fiecare joc
(fara preselectie pe istoric la 14.09.2026). Re-Bench aplica `per_game` inainte de
construirea task-urilor si adauga `random`. La configuratia curenta, cu patru
ferestre si fara controlul amestecat, matricea e 52 × 4 × 4 = 832 folduri.
Override-urile explicite `--methods` si `--quick` continua sa ruleze metodele
cerute pe toate jocurile.

Cheia de cache a foldurilor primeste sufixul `bs1` de indata ce `block_size`
difera de sentinel-ul istoric (99999) — care ramane in cod ca valoare implicita
a cheii, desi runner-ul si CLI-ul folosesc acum implicit 1. Niciun fold
cache-uit sub schema veche (score-once-per-fold) nu poate fi servit tacit sub
noua semantica.

### Joker Urna 2

Urna 2 are benchmark propriu, scorer/ensemble propriu si pool fix de un numar
(top-1, baseline aleator exact 5%). Metodele vechi selectate pentru Urna 2
(`ml_knn_5`, `649_decade_hot` etc.) au fost sterse odata cu intreg setul la
14.09.2026; decizia se reface la primul Re-Bench pe cele 50 de metode noi.
Poarta `tiebreak_k1` ramane: o metoda cu doar cateva niveluri de scor pe 1..20,
care alege mereu acelasi numar prin tie-break, este exclusa ca dependenta de
tie-break, nu transformata artificial in castigator. `markov_self_state` si
`vlmm_self_k3` nu intra in decizia si nici in curarea Urnei 2: top-1 e acolo
bila precedenta (§4.2). Pana la un Re-Bench pe
extrageri viitoare, un rezultat de clasament NU e dovada de avantaj (vezi
limita de validitate din §5).

## 6. Pool unic

- Selectorul de tara (sidebar „1. Date”, `country_val`, implicit România) si
  jocurile tarii (`games_val`). „📂 Incarca istoricul <tara>” citeste CSV-urile
  din registru si leaga fisierul de joc (`STATE['dataset_game']`). La deschiderea
  paginii, `_autoload_histories` incarca singur CSV-urile tarii selectate care
  lipsesc sau s-au schimbat pe disc (mtime in `STATE['dataset_mtime']`);
  butonul ramane pentru reincarcare manuala. Se trimit
  numai seturile tarii selectate; cu România selectata, config_json si hash-ul
  sunt identice cu cele de dinainte. Rezultatele se identifica din ecoul
  worker-ului (`_game_spec_for`): titlu „Tara · Joc”, tarif in moneda tarii sau
  „tarif necunoscut”, scheme reduse LR numai la România, bilet fizic din
  registru sau „bilet nemodelat”, iar jocurile care nu se joaca online din
  România poarta „doar antrenament”, afara de cele cu `play_note` in registru
  (azi Bulgaria · Toto 2 6/49, „se joaca in agentie in Bulgaria”): acestea nu
  sunt `training_only`, iar nota lor ia locul celei de antrenament
  (`ui_runtime._training_only_note`); fara `play_note`, iesirea ramane identica. Re-Bench, clasamentul, ETA, curarea si
  prospetimea citesc caile tarii selectate. Ramase: alte taburi deschise se
  actualizeaza abia la reincarcare; cateva texte romanesti (nota de garantie,
  ordinea WF) apar si la alte tari; tariful strain nu include taxa pe bilet.

- Pool-ul UI este limitat la 6..16.
- Butoanele „🚀 Genereaza (metoda din decizia bench)” si „⚡ Auto-Pilot (arata
  metoda pe joc + genereaza)” dau acelasi pool si aceleasi bilete: motorul
  citeste scorerul din `best_methods.json` la orice job. Auto-Pilot doar
  notifica metoda per joc si trimite `sim_depth_pct` de telemetrie. Nu exista
  generare care ocoleste decizia.
- Mailul de rezultate dateaza extragerea strict dupa ultima extragere din
  istoricul rezultatului (de la max(azi, ultima extragere + 1 zi)); la Romania
  conteaza cea mai noua dintre jocuri, un joc strain se dateaza dupa propriul
  istoric. Generat joi seara, cu extragerea de joi in CSV, subiectul spune
  duminica.
- Sub pool-ul fiecarui joc, mailul numeste metoda din decizia bench si
  ratingul ei: rata pe tinta, rata la intamplare, scorul Wilson (z=1),
  ferestrele batute si nota Holm (`_mail_method_lines`). La Joker urmeaza
  aceleasi linii pentru numarul Joker (`METODĂ JOKER`, `RATING JOKER`: Urna 2,
  top-1 fata de 5%), din 2026-10-08. Pe rezerva `frequency` sau dupa o
  decizie mutata, ratingul lipseste, cu motivul.
- Lista de variante simple din rezultate arata `simple_variants_val` variante
  (campul „Variante simple afisate in rezultate”, 1..500, implicit 10); costul
  „Top N bilete simple” urmeaza acelasi numar. „Arata toate” ramane.
- Butonul „🎟️ Bilet complet" din sidebar face 1-10 bilete fizice per joc
  (campul „Bilete", `full_ticket_count_val`, implicit 1) din pool-ul
  rezultatului afisat: pe bilet 3 variante la 6/49, 4 la 5/40, 2 la Joker (cu
  numarul Joker pe fiecare). Variantele vin din wheel-ul cu buget
  (`core/full_ticket.py`, `generate_wheel` cu `max_variants`), iar acoperirea
  afisata e a acestor variante, nu a wheel-ului complet. Cand garantia e
  completa inainte de a umple biletele, locurile ramase acopera grupe mai mari
  din acelasi pool (g+1, apoi pana la sistemul complet); rezumatul de sub
  variante spune cu cate variante s-a completat garantia. Fiecare bloc si
  fiecare bilet poarta tara si jocul (`core/lotteries.display_name`, ex.
  „România · Loto 6/49"), si in textul copiat; la fel titlurile rezultatelor,
  ale raportului, ale istoricului de hituri si ale mailului (`_game_title`). Nu trimite job si nu
  schimba rezultatul afisat. Pool-ul biletului se potriveste automat pe
  clasamentul metodei (`audit["timesfm_predictions"]`, deci cu restrangerea
  bazei si penalizarea recenta deja aplicate): prea mic pentru variantele
  distincte cerute (6/49 cu pool 6: 7 numere la un bilet, 9 la zece) -> se
  adauga urmatoarele numere din clasament, cu aceeasi verificare de completare
  ca `limit_consecutive_run` (pool-ul afisat ramane intreg; limita creste numai
  daca altfel s-ar pierde un numar din el; pe o baza ingusta se ia tot ce are
  clasamentul); mai mare decat locurile de pe bilete
  (Joker: 10 numere pe bilet) -> raman cele mai bine clasate numere. Clasamentul este ORDINEA cheilor din audit, scrisa de
  `rank_by_score` pe scorurile exacte; valorile sunt rotunjite la 6 zecimale si
  nu se reordoneaza (doua scoruri apropiate devin egale, iar tie-break-ul ar
  alege alt numar decat metoda). Fereastra spune ce s-a adaugat sau ce a ramas
  afara, iar acoperirea numeste pe cate numere ale biletului e calculata.
  Extinderea respecta limita de consecutive a rezultatului
  (`audit.consecutive_limit.applied`) si spune ce numar a sarit; un rezultat
  fara limita ramane pe regula veche.
  „📋 Copiaza numerele" copiaza in browser, chiar in click: Safari/iOS scriu in
  clipboard numai in timpul gestului, nu dupa un drum pana la server.
- „Bilet complet” ia variantele numai din pool. Bifa „🎯 Variante dispersate
  pe bilet” a fost scoasa la 2026-10-10, la cererea utilizatorului: variantele
  dispersate ieseau din pool (6/49, 2 bilete: 36 de numere, ultimele doua
  variante din numere nealese de metoda), iar avantajul lor la 3+ (11,14% fata
  de 10,05% la 6 variante, extrageri uniforme) nu se vede pe 778 de extrageri
  WF (diferenta asteptata 8-9 extrageri, abaterea standard 11). Cheia
  `full_ticket_spread_val` din `.ui_state.json` nu se mai citeste si dispare
  la urmatoarea salvare. Dialogul afiseaza, pentru pragurile cu premiu, sansa
  EXACTA ca cel putin o varianta sa castige (`ticket_hit_probabilities`).
- „📜 Istoric hits” are sub tabelul principal „Bilet complet” pe ACELEASI
  extrageri WF (`ticket_replay.replay_full_tickets`,
  `ui_hits._render_full_ticket_replay`): la fiecare pas, biletele pe care le-ar
  fi dat butonul in ziua aceea, din `ticket_context` (§8), cu numarul de
  bilete din sidebar si garantia rezultatului afisat; Joker numai Urna 1;
  la 5/40 intersectia e cu toate cele sase numere extrase. Un pas cu mai
  putine variante decat biletele cerute (pool din fallback-ul de frecventa,
  fara clasament de extins) se numara indisponibil. Pool-ul si
  referinta aleatoare a tabelului principal folosesc marimea jucata la pasi
  (`_wf_pool_size`), nu pe cea ceruta. Coloana 🎲 = P exacta (enumerare, extragere
  uniforma) ca cel putin o varianta sa atinga 3+/4+/5+, calculata pe
  biletele celei mai recente extrageri; nu e rata observata si nu depinde
  de etichetele numerelor. Calculul ruleaza in fundal (pana la 4 procese,
  `with_chances=False`, jurnalul greedy pe WARNING), memorat pe lista WF
  afisata; un temporizator de 1 s umple sau reface numai acel bloc (si cand
  se schimba „Bilete”), fara refresh-ul panoului, si nu repune in coada
  reluarea aflata in calcul (`_REPLAY_THREAD["current"]`). Pasii fara context sunt
  numarati separat. Copiii ProcessPoolExecutor sunt `__mp_main__`:
  `app_nicegui.py` porneste UI-ul numai din `__main__` (`reload=False`).
  Masurat pe istoricul complet, UI izolat: 3 bilete instant dupa WF;
  10 bilete × 3 jocuri ~90 s, cu „⏳”, sectiunile deschise neschimbate.
- Selectia este top-N pura dupa scorul validat, cu o singura exceptie, limita
  de consecutive de mai jos.
- Limita de consecutive este o OPTIUNE de utilizator (`max_consecutive_run` in
  `config_json`; bifa „🔗 Fara 3 numere consecutive in pool"), PORNITA implicit
  in UI la cererea explicita a utilizatorului (2026-09-26), spre deosebire de
  penalizare si restrangere. Motorul si worker-ul au implicit 0 (oprit): un task
  vechi fara cheie ruleaza fara limita, iar `filter_consecutives` ramane ignorat.
  UI-ul trimite 2 (int, nu bool). Clasamentul (dupa penalizare si restrangerea
  bazei) se parcurge in ordine; numarul care ar forma a treia secventa e sarit,
  iar locul lui il ia urmatorul care incape. Verificarea de completare
  (`limit_consecutive_run`) alege cel mai bun set dupa rang care respecta limita,
  si pe o baza restransa unde parcurgerea simpla ramane fara numere. Daca
  intervalul e prea ingust pentru pool, se relaxeaza limita, nu pool-ul si nici
  intervalul. Se aplica si pe completarea defensiva si pe fallback-ul pe
  frecventa; nu atinge Urna 2 Joker. Auditul `consecutive_limit` retine limita
  ceruta si aplicata, numerele scoase din pool si puse in loc, cu locul lor in
  clasament; `timesfm_predictions` coboara atunci sub locul 25 cat e nevoie. Panoul,
  raportul, nota de bench si istoricul WF il descriu prin `_consecutive_limit_text`.
  Nota de bench numeste limita oricand e ceruta, ca restrangerea: bench-ul masoara
  top-K brut la fiecare pas istoric, iar limita schimba pool-ul jucat la multi
  dintre ei, chiar cand pool-ul de azi n-a avut nevoie de inlocuire. Antetul
  pool-ului spune ca paranteza e frecventa pe istoricul folosit (cu lookback,
  numai pe fereastra lui).
  Pe datele curente, 6/49 are secvente de 3-4 la fiecare pool 6..16, deci pool-ul
  jucat difera de top-K validat de bench; numai WF masoara pool-ul jucat. FARA
  avantaj statistic demonstrat (hipergeometric, ca la restrangere).
- Fiecare joc genereaza si afiseaza un singur pool; configuratia, worker-ul,
  raportul, emailul si walk-forward-ul nu mai au Pool 2/auto-invert.
- Payload-urile vechi cu doua faze sunt citite compatibil folosind numai pool-ul
  normal din prima faza.
- Penalizarea dupa ultimele extrageri este o OPTIUNE de utilizator
  (`recent_penalty_draws`, `recent_penalty_factor`), implicit OPRITA
  (`recent_penalty_draws_val = 0` in UI) — nu schimba pool-ul fara nicio actiune
  a utilizatorului. Odata setat un numar de extrageri > 0 (factor implicit 0.5),
  scorul unui numar extras de k ori in ultimele N extrageri se inmulteste cu
  factor^k, inainte de top-N. Se aplica identic in productie si in walk-forward
  (intra in cheia de cache WF cand e activa) si este raportata in
  `audit.recent_penalty`. Este o preferinta de compozitie a pool-ului, neutra ca
  valoare asteptata: analizele din `scripts/analysis/` nu au demonstrat
  predictibilitate pentru paritate, decade, sume sau tipare recente, iar
  penalizarea nu trebuie prezentata drept avantaj statistic.
- Restrangerea bazei de numere este o OPTIUNE de utilizator, un INTERVAL
  (`restrict_base_min`, `restrict_base_max`), SEPARAT per joc (6/49, 5/40, Joker
  Urna 1) — universurile difera (49/40/45), deci un singur interval global nu are
  sens pentru toate trei. In UI: o singura bifa de activare
  (`restrict_base_enabled_val`, implicit OPRITA) urmata de cate un rand de praguri
  pentru fiecare joc; bifa oprita anuleaza toate cele trei intervale, indiferent
  de ce mai e tastat in campuri (`_active_restrict_base(game_label)`,
  `app_nicegui.py`). La prima activare, campurile goale ale unui joc primesc un
  punct de plecare 10..(max_n - 9) — NU o recomandare, doar ca bifa sa nu se
  deschida pe campuri goale; utilizatorul schimba liber. Un capat lasat pe 0
  ramane liber; intervalul inversat (min > max) si cel mai ingust decat un bilet
  (span < numerele de pe bilet, `play_n`) sunt IGNORATE si consemnate in `audit.restrict_base.ignored`, nu
  aplicate tacit. Fara a doua garda, 47-49 la 6/49 lasa trei candidati, iar
  wheeling-ul trateaza `len(pool) < pick` drept sistem complet cu un bilet:
  pipeline-ul raporta `[47, 48, 49]` ca bilet 6/49 cu acoperire 100%. FARA
  avantaj statistic demonstrat: probabilitatea de hit a unui pool de dimensiune
  fixa e identica matematic (hipergeometric) indiferent de care numere il compun,
  confirmat si empiric pe istoricul aplicatiei
  (`scripts/analysis/pattern_base_reduction.py`). Se aplica identic in productie
  si in walk-forward (intra in cheia de cache WF cand e activa, prin
  `_restrict_base_sig`, care separa 1..40 de 10..40) si este raportata in
  `audit.restrict_base`. Cele trei suprafete care o afiseaza — panoul, raportul
  si nota de bench — folosesc `_restrict_base_text`, ca intervalul sa nu apara
  altfel in raport decat pe ecran.
- Submeniul „Cel mai bun interval de fiecare latime, per joc" din sidebar
  afiseaza, pentru
  fiecare latime de interval, intervalul cu cea mai buna rata pe istoric, ratele
  lui pe cele doua jumatati SI cel mai bun interval de aceeasi latime gasit pe
  extrageri sintetice uniforme. Coloana „Toata extragerea" masoara ALTCEVA: de
  cate ori au incaput TOATE numerele extrase in interval, langa valoarea
  asteptata pentru acea latime (`theoretical_full_draw_rate`, care nu depinde de
  unde e fereastra, doar de cat e de lata). Distinctia nu e cosmetica: 10.31%
  pentru 2-11 inseamna „cel putin 3 numere au cazut acolo", nu „a fost o
  extragere intreaga in 2-11" — asta nu s-a intamplat niciodata in 2581 de
  extrageri 6/49, maximul fiind 5 numere, o data. Ratele sunt exacte
  (hipergeometric per extragere, `loto_enterprise/core/base_threshold.py`),
  nu Monte Carlo. Coloana de control
  nu este optionala: acelasi calcul „gaseste" un campion si acolo unde nu exista
  nimic de gasit, iar fara ea un varf de 10.31% s-ar citi ca descoperire.
  Submeniul NU seteaza si NU recomanda niciun interval. Se calculeaza abia la
  deschidere (inchis costa ~1 ms, deschis ~0.6 s pentru trei jocuri) si e
  memoizat pe fisier + geometrie + pool, cu plafon peste toate combinatiile
  joc × pool, ca o plimbare peste dimensiunile de pool sa nu recalculeze tot.
  Pool-ul este plafonat la 6..16 inainte de calcul: `ui.number` isi aplica
  min/max abia la blur, iar o valoare tastata intermediar producea o geometrie
  invalida raportata drept „istoric indisponibil".
  Nu adauga niciodata o varianta "bench calculeaza intervalul optim" — ar prezenta
  zgomot statistic drept semnal (orice interval da aceeasi rata teoretica).
  Bench-ul a fost construit si rulat ca diagnostic, nu ca functie de productie:
  `scripts/analysis/bench_base_threshold.py` alege pragul pe primele 70% din
  istoric si il masoara pe ultimele 30%, apoi compara castigul cu distributia
  nula obtinuta prin permutarea etichetelor numerelor pe tot istoricul. Rezultat
  la 2026-09-08: 6/49 +0.67pp (p = 0.075), 5/40 +0.35pp (p = 0.200), praguri
  alese instabile intre seed-uri (20/23/25, respectiv 24/25). Castigul aparent
  cade in distributia nula, deci nu justifica un prag automat.

## 7. Wheeling si covering design

`wheeling_methods.py` este sursa unica pentru metodele disponibile:

- `greedy`;
- `hitcover`;
- `maxcover`;
- `ilp`;
- `annealing`;
- `genetic`;
- `lajolla`;
- `union34`.

Metoda opt-in `maxcover` adauga greedy pe castig marginal exhaustiv si doua
treceri de schimburi locale, pe geometrie pozitionala memoizata. Este limitata
la pool <=16, pick <=6, 1..64 bilete si garantie < pick; in afara limitelor
revine la greedy. Compara acoperirea EXACTA dupa completarea numerelor lipsa
si pastreaza candidatul numai daca acopera mai multe tinte, cu cel mult
acelasi numar de bilete. Nu demonstreaza optimalitate sau avantaj predictiv.
Nu modifica default-urile. Activare explicita: `LOTO_WHEEL_METHOD=maxcover`.
Cheia WF distinge deja numele metodei; cheia de pipeline a worker-ului primeste
sufixul `:wheel=<metoda>` (implicit `auto`) pentru orice metoda.

Swap-uri de profil (2026-10-02, `covering/profile_swap.improve_hit_profile`):
dupa orice metoda in afara de `greedy` explicit — cover clasic fara plafon (orice
garantie), lotto design „t daca p", buget (inclusiv >64, unde hitcover ramane
greedy) — si pe locurile ramase ale „Bilet complet" (baza `n_base` inghetata),
se incearca schimbarea unui bilet cu oricare alt bloc din pool. Se accepta numai
daca profilul EXACT `wheel_hit_profile` nu scade la NICIUN prag si NICIO marime
a intersectiei si creste strict la 3+/4+/5+ posibile. Dominanta pastreaza singura
garantiile: counts[t][t] (cover clasic), counts[p][t] (t daca p), counts[1][1]
(numerele jucate). Acelasi numar de bilete distincte, acelasi pool. Limite:
pool 5..16, pick 3..6, cel mult 512 bilete, doua treceri, 25000 candidati;
memoizare limitata. Evenimentele unui candidat se construiesc dintr-o singura
trecere peste pozitiile biletului, cu prefixul comun al blocurilor consecutive
pastrat; dominanta se verifica numai pe straturile unde biletul scos pierde
ceva, iar randurile complete se calculeaza numai la castig egal. Rezultatul e
identic cu cautarea dinainte (`test_profile_swap.py` pastreaza cautarea de
referinta si o compara). Pool 16, plafon 10, Linux: garantia egala cu biletul
1,4 s la pick 6 si 1,3 s la pick 5 (inainte 9,4 s si 5,5 s), garantia 4 ~0,5 s,
generarea completa a wheel-ului. Castiguri masurate
in `scripts/analysis/hit_opt_report_2026-10-02.md` (`bench_hit_profile.py`);
cele mai mari la lotto designs, mici la coverele clasice. Fara afirmatie
predictiva: probabilitatea de hit a POOL-ului nu se schimba.

Metoda `hitcover` (audit 2026-10-01) optimizeaza sansele pe bilete
la acelasi buget. Accepta un candidat numai daca numarul de bilete este egal,
acoperirea exacta si numarul intersectiilor favorabile nu scad pentru NICIUN
prag de hituri si NICIO marime a intersectiei pool-extragere; cere un castig
strict la 3+/4+. `covering.probability.wheel_hit_profile` compara numaratori
intregi. Limite: pool <=16, pick <=6, garantie < pick, 1..64 variante,
acoperire initiala incompleta; in afara lor ramane greedy. La cererea
utilizatorului din 2026-10-01, este alegerea AUTOMATA la buget pozitiv, inclusiv
pentru baza din „Bilet complet”. Nu necesita o comanda PowerShell sau o setare
noua. Fara plafon ramane La Jolla; conditia > garantie ramane lotto design.
`covering.dispatch.resolve_wheel_method` este comun motorului, biletelor fizice
si semnaturii WF. `LOTO_WHEEL_METHOD` ramane override optional; `greedy` reface
constructia anterioara. Baza cu garantie completa si completarea pe grupe mai
mari a biletelor fizice raman identice. Worker v8 invalideaza payload-urile vechi,
inclusiv cheile fara config. Nu afirma avantaj predictiv sau optimalitate.
Raport: `scripts/analysis/audit_application_report_2026-10-01.md`.

Bugetul automat in motor (2026-10-08). Pana atunci un buget egal cu designul
complet dadea sub 100% sau bilete in plus (6/49 pool 16, garantie 3: buget 45
-> 99,46%, buget 50 -> 47 de bilete), iar peste 64 de bilete hitcover cadea pe
greedy si un bilet in plus putea scadea sansele (5/40 pool 16, garantie 4: 64
bilete 4+ 4,46%, 65 bilete 3,67%). Cu `max_num`, pe care il da numai motorul
(„Bilet complet” ramane neschimbat), doua ramuri pe alegerea automata:
- bugetul cel putin cat designul complet validat
  (`covering.designs.complete_design_size`) ia exact biletele fara plafon
  (`dispatch.budget_buys_complete_design`): garantie 100%, cel mult atatea
  bilete;
- altfel, peste 64 de bilete, `covering/budget_climb.py` muta cate un
  numar dintr-un bilet. Mutarea trece numai daca P exacta (extragere
  uniforma) de cel putin t hituri pe un bilet nu scade la NICIUN t, biletele
  raman distincte si fiecare numar din pool ramane pe un bilet; mutarile
  neutre trec si ele. Rezultatul se certifica la fel ca hitcover: profil exact
  nedescrescator la fiecare prag si marime, castig strict la 3+/4+/5+. Pornirea
  nu depinde de scoruri (wheel-ul pe pozitii, memorat o data pe proces); daca
  rezultatul ei nu domina wheel-ul pasului, se cauta din wheel-ul pasului.
  Limite: pool <=16, 65..512 bilete, 12000 de mutari, generator seedat din
  geometrie (determinist). Ramurile urmeaza metoda hitcover (aleasa automat
  la buget pozitiv sau ceruta prin `LOTO_WHEEL_METHOD=hitcover`); `greedy` si
  celelalte metode explicite nu trec prin ele. Cheile WF sunt in §8.
  Masurare: `scripts/analysis/bench_budget_climb.py`; raport
  `scripts/analysis/pool16_4plus_2026-10-08.md`.

Variante dispersate (2026-10-05, `covering/spread.py`, scoase din aplicatie la
2026-10-10, §6; raman pentru studiul `ticket_geometry_2026-10-05.py`): cautare locala
determinista pe probabilitatea EXACTA ca doua variante sa castige impreuna
(`pair_joint_probability`; pondere 1 la tinta, 1e-3 la pragurile mai mari,
1e-9 la cele mai mici). Prefera numerele mai bine clasate la egalitate si
respecta `max_run` per varianta. Plafonul `B x P(o varianta >= t)` este atins
exact cand doua variante au cel mult `2t - extrase - 1` numere comune (6/49 si
5/40 4+: 1; Joker 3+: 0). `ticket_hit_probabilities` enumereaza toate
extragerile (max_num <= 64). Pe extrageri uniforme, dispersia domina la
fiecare prag cea mai buna configuratie de productie masurata (pool 6..16,
garantie 2..4) la acelasi numar de variante. Aplicatia foloseste din modul
numai `ticket_hit_probabilities` (sansele din „Bilet complet” si coloana 🎲).

Experimentul reproductibil `scripts/analysis/bench_budget_cover.py` compara
greedy, La Jolla, bilete aleatoare si maxcover: pool identic dupa frequency
canonic, bugete 7/10, tinte 3/4, ultimele 30% din istoric. Exclude inclusiv
extragerile din aceeasi zi din scorare. Raportul din
`scripts/analysis/budget_cover_report_2026-09-12.md` include toate configurarile,
intervale Wilson, test pereche si corectie Holm pentru 24 de comparatii.
Acoperirea creste, dar avantajul istoric nu trece pragul ajustat de 5%;
metoda ramane experimentala. Joker masoara numai Urna 1, iar la 5/40 raportul
din 2026-09-12 a folosit n1..n5 (contractul de atunci); aplicatia numara acum
hiturile 5/40 pe toate cele 6 numere extrase.

Reguli:

- fara plafon de bilete, productia prefera La Jolla; greedy-ul cu scoruri il
  inlocuieste cand are mai putine bilete sau, la acelasi numar, profilul lui
  exact il domina (audit 2026-10-08; cheia WF `tg1`);
- cu `max_variants > 0`, implicit hitcover incearca dominanta exacta fata de
  greedy la acelasi numar de variante; se recalculeaza
  acoperirea dupa completarea numerelor lipsa; in motor, designul complet
  cand incape in buget, altfel peste 64 de bilete `budget_climb`;
- designurile locale sunt validate la 100% inainte de utilizare;
- fallback: La Jolla -> ILP -> greedy;
- `guarantee == pick` inseamna sistem complet, nu trebuie clampat;
- `union34` foloseste un singur cover g4: acoperirea 4-din-4 implica 3-din-3;
- lotto design „t daca p" (`wheel_condition` > garantie): orice p numere din pool
  au cel putin t pe un bilet; mult mai ieftin (Joker pool 11: 3-daca-4 in 10
  bilete fata de 20), dar garantia se declanseaza doar cand cad p numere din
  pool. Designuri locale `L_v_pick_p_t.txt` validate la 100% inainte de
  folosire, altfel greedy pozitional + ILP; regenerare offline cu
  `scripts/analysis/gen_lotto_designs.py`. Walk-forward-ul ramane pe coverul
  clasic intern; hiturile de pool nu depind de wheel;
- nu compara algoritmi numai dupa numarul de bilete; ordinea este acoperire,
  apoi cost;
- orice filtru post-wheel trebuie sa foloseasca
  `filter_preserving_coverage` si sa recalculeze `compute_coverage_pct`.

Un wheel cu acoperire sub 100% trebuie raportat explicit. La acoperire 100% si
garantie suficienta, 3+ in pool implica 3+ pe cel putin un bilet; la acoperire
partiala, hitul de pool este doar plafon pentru hitul pe bilet.

Generatorul greedy pastreaza un cursor peste tintele deja acoperite si opreste
cautarea la primul bilet cu `C(pick, guarantee)` tinte noi: niciun candidat nu
poate depasi acest maxim, iar egalitatile pastreaza primul candidat. Sistemul
complet cu buget materializeaza numai variantele cerute. Completarea numerelor
lipsa actualizeaza incremental frecventele dupa fiecare schimbare de bilet.
Ordinea biletelor si acoperirea raman identice cu baseline-ul `67effec`,
verificate prin cele 54 de cazuri din `test_wheel_golden.json` (scoruri absente,
egale si variate, pool-uri mici/mari, buget liber/1/7). Nu necesita bump de
cache: rezultatul serializat si semantica raman aceleasi.

Masurare reproductibila, fara scrieri de stare:
`python scripts/analysis/bench_wheel_generation.py`.
Pe Linux/Python 3.14.7, mediana a 9 repetari: pool 16/pick 5/g4, 20.585 ->
8.241 ms; sistem complet pool 16/pick 6 plafonat la 7 bilete, 1.594 -> 0.019 ms,
cu varful alocarilor Python redus de la 895920 la 3309 octeti. Sunt timpi ai
generatorului pe scorurile fixe din script, nu accelerarea intregii aplicatii;
castigul depinde de geometrie, scoruri si hardware.

## 8. Walk-forward

- WF foloseste numai date anterioare extragerii validate.
- UI valideaza onest pool-ul unic.
- O decizie rescrisa in timpul rularii (Re-Bench terminat, tinta schimbata)
  inseamna pasi scorati de doi scoreri: WF nu mai salveaza cache-ul
  (`meta["decision_changed"]`), iar UI-ul si raportul o spun.
- Inaintea fiecarui joc, WF-ul compara decizia de acum cu metoda din auditul
  rezultatului (`_decision_moved_since_generation`): daca alta metoda ar scora
  pasii, jocul e sarit (`retro_meta[...]["decision_moved"]`), iar panoul si
  raportul spun ca decizia s-a schimbat dupa generare. Dupa rulare, aceeasi
  verificare marcheaza `decision_changed`. Un rezultat vechi fara metoda in
  audit ruleaza ca inainte.
- `hits` = maximul pe un singur bilet.
- `hits_union` = intersectia pool-ului cu extragerea (la 5/40, cu toate cele
  6 numere extrase; biletul ramane de 5).
- Joker: `joker_hit` = numarul din urna 2 de pe bilete a iesit la acea extragere
  (`None` = alt joc sau cache vechi). Sumarul WF il afiseaza fata de aleator 5%.
- `wheel_coverage=None` inseamna necunoscut, nu 100%.
- `ticket_context` (aditiv, fara bump) = pool-ul pasului, auditul citit de
  „Bilet complet” (`timesfm_predictions`, `consecutive_limit`,
  `restrict_base`), numarul Joker si extragerea tinta; acelasi obiect pe toate
  intrarile unei extrageri. None = intrare dintr-un cache scris inainte de
  camp; {} = context indisponibil la pas. Un cache, complet sau partial, cu
  pasi fara context nu mai e servit direct: pasii aceia se refac pe calea
  normala (`skip_indices` = pasii cu context), iar intrarile vechi raman prin
  reuniunea `_merge_partial_coverage` pana la refacere. Prima validare dupa
  actualizare reface deci pasii vechi, in bugetul WF.
- Adancimea UI este 30% din istoric.
- Bugetul implicit este 90 minute si permite rezultat partial; la urmatoarea
  rulare pasii deja validati din cache-ul partial sunt sariti (`skip_indices`),
  deci acoperirea creste in loc sa se refaca de la zero.
- O extragere noua schimba amprenta istoricului din cheie. Pasii fara stare
  vad numai extragerile dinaintea zilei tintei, deci cand istoricul nou doar
  adauga randuri la coada celui vechi, pasii cache-ului anterior al ACELEIASI
  chei (joc, pool, adancime, decizie, setari; difera numai amprenta) raman
  valabili: `_previous_history_steps` ii refoloseste, iar rularea calculeaza
  numai pasii lipsa (`meta["reused_previous_history"]`). Conditii: istoricul
  intern al backtester-ului (sortat, fara randurile invalide) incepe exact cu
  cel vechi (extrageri, date, cutoff-uri, amprenta), iar data tinta a fiecarui
  pas refolosit coincide. O corectura in trecut, ori o extragere veche adaugata
  la coada CSV-ului si mutata de sortare in interior, reface tot.
  Amprenta include lungimea, deci `meta["history_rows"]` e singura lungime
  incercata; un cache scris fara camp se cauta pe ultimele 60. Amprenta
  fisierului de decizie se ia inaintea semnaturii, ca o rescriere din timpul
  cautarii sa marcheze `decision_changed`. Rezultatul refolosit e identic cu
  o rulare completa (`test_wf_incremental.py`). Fara bump: cheia si structura
  raman.
- O validare partiala are de regula cele mai noi extrageri (pasii merg
  recent→vechi). Cand tocmai cea mai noua lipseste (pas refolosit fara pasul
  nou, pas recent crapat), `meta["newest_missing"]` o marcheaza, iar panoul si
  raportul spun „lipsesc cele mai noi”, nu „cele mai recente”.
- `cache_only=True` numai citeste cache-ul exact (complet sau partial), fara
  pas calculat si fara scriere; pasii refolosibili nu se arata acolo, fiindca
  le lipsesc tocmai extragerile cele mai noi. Rezultatul reafisat la pornire
  isi incarca astfel validarea (`_load_cached_walk_forward`), numai daca
  decizia de acum alege aceeasi metoda (si la Joker aceeasi Urna 2) ca auditul
  rezultatului (`_result_scorers_match_decision`): cheia WF citeste decizia
  curenta, iar dupa un Re-Bench sau o schimbare a tintei ar valida alta
  metoda. Altfel, „📜 Istoric hits” ramane gol pana la generarea urmatoare.
- Ordinea jocurilor este Joker, 5/40, 6/49.
- Paralelizarea foloseste aproximativ 75% din nuclee, cu BLAS single-thread per
  proces.

Cache-ul WF sta in `D:\_BUILD\_LOTO\.wf_cache`, in afara OneDrive; override-ul
este `LOTO_WF_CACHE_DIR`. `ACTUALIZARI.bat` migreaza idempotent fisierele legacy
`bench_results/walk_forward_*.pkl`, fara sa suprascrie o destinatie existenta.
Cheia include istoricul complet, lookback, scorer, ensemble, tinta, wheel,
hash-ul designului, limita de consecutive (numai activa) si, pentru Joker,
decizia Urnei 2. Semnatura wheel-ului cu buget poarta `bf1` cu hash-ul
designului cand bugetul ia coverul complet si `bc1` peste 64 de bilete
(cautarea exacta); celelalte bugete pastreaza cheia veche. La La Jolla si
union34 (fara plafon) semnatura poarta `tg1`, regula la egalitate de bilete.
Designul semnat e al pool-ului rotit efectiv (`_effective_pool_size`): un
interval restrans mai ingust decat pool-ul, dar cel putin cat un bilet, il
taie.

## 9. Cache si invalidare

| Strat | Versiune | Bump obligatoriu cand |
|---|---:|---|
| benchmark fold | `v22` | se schimba output-ul scorerului, `FoldResult`, validarea sau denominatoarele |
| walk-forward | `v31` | se schimba pool-ul, wheel-ul, structura flat sau semantica hiturilor |
| worker pipeline | `v13` | se schimba rezultatul serializat al pipeline-ului |

⚠️ Worker pipeline e INERT azi: UI-ul trimite `use_cache: False` la fiecare job
(`app_nicegui._build_config_json`), deci stratul nu se atinge in productie.
Randul ramane ca sa se stie ce s-ar bumpa daca se reactiveaza. v6: fiecare
rezultat de joc poarta `country`, `game_id`, `bench_key`, `geometry`; cheia
unui job cu jocuri din alte tari include si fisierul de decizie al tarii.

Un bump WF schimba numele fisierului, dar nu sterge cache-urile vechi. Foloseste
API-urile de inventariere/curatare, nu stergeri recursive oarbe.

Un bump de bench NU mai poate trece neobservat pe langa decizie: semnatura din
`_meta.engine_signature` (versiunea cache-ului + hash-ul registry-ului de metode)
se stampileaza la fiecare bench, iar `freshness.check_freshness` o compara
INAINTE de datele CSV. Cu CSV-urile neatinse dar alt motor, raspunsul e
`stale` / `full_rebench`, nu `fresh` / `use_cache` ca inainte.

Cand se schimba ce pool produce o SETARE data, nu structura rezultatului, un bump
global ar arunca si cache-urile pe care schimbarea nu le atinge — la WF, 90 de
minute de rulare fiecare. Pentru restrangerea bazei exista in schimb
`_RESTRICT_SEMANTICS`, un marcaj care intra in cheie DOAR cand restrictia e
activa: `walk_forward_adapter._restrict_base_sig` pentru WF si hash-ul din
`_build_config_json` pentru cache-ul de pipeline al worker-ului. Cele doua
constante se tin sincron (exista un test pentru asta) si se incrementeaza
impreuna. v2: intervalele mai inguste decat un bilet sunt ignorate, nu aplicate.
Limita de consecutive urmeaza acelasi contract cu `_CONSECUTIVE_SEMANTICS`
(`walk_forward_adapter._consecutive_sig` si hash-ul din `_build_config_json`),
fara bump global de WF. Cum bifa e pornita implicit, primul WF dupa actualizare
calculeaza o cheie noua (poate iesi partial si continua prin `skip_indices`).

## 10. Mediu si rulare

Instalarea canonica este:

1. `ACTUALIZARI.bat` - instaleaza/actualizeaza Git for Windows prin winget,
   sincronizeaza `main` din copia temporara, instaleaza/actualizeaza Python
   3.14, recreeaza venv-ul daca patch-ul difera si instaleaza
   `requirements_base.txt`; dupa `update_csv.py` ruleaza `update_externe.py`
   (in acelasi log, inainte de auto-commit-ul `_ISTORIC`): cate o sursa per joc
   strain din registru (sursele in `_ISTORIC/externe/README.md`), timeout 15 s,
   verifica extragerile deja stocate din ultimele 60 de zile si, la orice
   nepotrivire sau rand invalid, nu scrie nimic pentru jocul acela; adauga numai
   extragerile mai noi, atomic, LF; iese mereu cu 0. START_8000 nu il ruleaza;
   dupa curatarea cache-ului WF ruleaza `cleanup_residual.py` (pasul [2d/4]):
   sterge numai o lista explicita de artefacte pe care codul nu le mai citeste
   (venv-uri/cache-uri vechi GPU/numba, `disabled_methods.json`, backup-uri
   `*.backup_overnight`/`*.pre_autopilot`/`best_methods.json.pre_*`, copii ramase
   ale modulelor sterse, `__pycache__`, loguri vechi din radacina cand runtime-ul
   e mutat, WF legacy deja migrat, foldurile bench de alte versiuni prin
   `purge_stale_fold_cache`); sare orice fisier urmarit de git, iar fara git
   sare si fisierele care ar putea fi urmarite; `--dry-run` doar listeaza; iese
   mereu cu 0;
2. `START_8000.bat` - sincronizeaza `main` din copia temporara daca e in urma, verifica mediul, curata procese vechi,
   porneste worker-ul si UI-ul.

`requirements_snapshot.txt` este arhiva si nu se instaleaza. Stack-ul este CPU;
nu adauga Torch, CUDA, TimesFM, NeuralForecast sau Streamlit.

Pe statia curenta, auditul din 2026-09-01 a eliminat dependenta de shim-ul
Chocolatey ramas spre `C:\Python314\python.exe`. Python 3.14.7 este instalat in
`%LOCALAPPDATA%\Programs\Python\Python314`, iar venv-ul proiectului este functional.
`ACTUALIZARI.bat` detecteaza executabilul real chiar daca launcherul `py` lipseste.
Verificarea canonica este:

```powershell
%LOCALAPPDATA%\Programs\Python\Python314\python.exe --version
D:\_BUILD\_LOTO\.venv\Scripts\python.exe --version
D:\_BUILD\_LOTO\.venv\Scripts\python.exe -m pip check
```

Stilul de cod e uniformizat cu `ruff format` (config in `pyproject.toml`,
`line-length = 88`); nu e instalat in venv-ul de productie, ruleaza separat,
doar la nevoie: `ruff format .`.

## 11. Verificare obligatorie

### Pentru orice schimbare

```powershell
D:\_BUILD\_LOTO\.venv\Scripts\python.exe -m py_compile <fisiere_modificate>
D:\_BUILD\_LOTO\.venv\Scripts\python.exe -m pytest -q
git diff --check
```

### Pentru UI

Porneste pe un port liber, asteapta importurile si verifica HTTP 200 plus fluxul
submit -> worker -> rezultat. Nu considera simplul import suficient.

### Pentru engine/scoring

- compara pool-ul si variantele cu un baseline inainte/dupa;
- verifica auditul pentru fallback, scorer activ si membri eliminati;
- testeaza scor gol, plat, NaN si inf;
- confirma aceeasi selectie in bench si productie.

### Pentru wheeling

- ruleaza `test_wheeling.py` si `test_covering_designs.py`;
- valideaza toate fisierele din `covering_designs/` la 100%;
- verifica geometria cu si fara `max_variants`;
- compara acoperirea inainte de numarul de bilete.

### Pentru benchmark

- confirma prezenta `random` si `frequency`;
- confirma cele patru jocuri, inclusiv `joker_urna2`;
- verifica `n_eval`, coloana tintei si metodele failed;
- nu actualiza `best_methods.json` dintr-un run partial;
- dupa un Re-Bench complet, inspecteaza `low_confidence`, mismatch-urile de
  coloana si ensemble-ul activ.

## 12. Roadmap

### P0 - restaurare operationala

- [x] Instaleaza Python 3.14.7 si recreeaza venv-ul CPU din requirements.
- [x] Ruleaza `pip check` si `verify_imports.py`: toate modulele sunt verzi.
- [x] Intareste `ACTUALIZARI.bat`: detector fara `py`, semnatura installer,
  erori fatale la pip/import si snapshot extern arhivei versionate.
- [ ] Porneste UI + worker si executa un job E2E pe fiecare tip de joc.

Criteriu de iesire: Python 3.14 si venv functionale, toate importurile obligatorii
verzi, pytest verde, UI HTTP 200 si payload worker decodat corect.

### P0 - recalibrare dupa Urna 2 top-1

- [x] Ruleaza Re-Bench pe toate cele 56 metode curate si cele patru jocuri.
- [x] Genereaza 448 folduri Urna 2 (real + shuffled), `rate_1plus_k1` si decizia
  `joker_urna2.k1`.
- [x] Verifica daca vreo metoda depaseste random 5% prin poarta de consistenta si
  Wilson; pastreaza `low_confidence` daca dovada nu exista.
- [x] Revizuieste `folds.csv`, `report.json` si `best_methods.json` impreuna,
  apoi comite numai output-urile complete care trebuie versionate.

Rezultat 2026-09-01: la acel Re-Bench, Urna 2 avea doua metode calificate în
decizia robustă, `649_decade_hot` si `ml_knn_5`, cu `low_confidence=false`.
Metodele plate pe geometria single-pick rămân `failed` si excluse, nu sunt
transformate artificial în ranking prin tie-break. De la introducerea
`ENSEMBLE_MAX_METHODS = 1` (vezi §5, „productia foloseste doar castigatorul
unic"), productia Urnei 2 foloseste — la fel ca toate jocurile — un singur
castigator validat direct, nu un blend cu doi membri.

Criteriu de iesire: toate cele patru jocuri au folduri curente, nicio metoda cu
scor inutilizabil nu intra in decizie, iar productia consuma exact decizia afisata.

### P1 - selectie Urna 2 si paritate bench/productie

- [x] Adauga `per_game.joker_urna2` dupa test extern si pre-screening oficial pe
  toate cele 111 metode; pastreaza numai semnale peste baseline si distincte.
- [ ] Automatizeaza testul care ruleaza fiecare metoda activa pe toate geometriile
  si compara acceptarea benchmarkului cu acceptarea engine-ului.
- [x] Raporteaza separat metodele incomplete (fereastra lipsa) si dependente de
  tie-break in decizie; raman de raportat unavailable, plate si corelate.
- [ ] Adauga un test de regresie pentru fallback-ul top-1 fara coloana
  `rate_1plus_k1`.

Criteriu de iesire: zero diferente de acceptare bench/productie si curation
Urna 2 explicabila, reversibila si reproductibila. Comparatia top-K
bench/productie se face cu limita de consecutive oprita: pornita, pool-ul jucat
difera de top-K prin constructie (§6).

### P1 - acoperire si cost

- [ ] Pastreaza manifest pentru cele 151 de designuri (52 clasice + 99 lotto):
  geometrie, hash, bilete si acoperire.
- [ ] Compara La Jolla, ILP si greedy pe aceeasi geometrie prin
  `(coverage, ticket_count, runtime)`.
- [ ] Cauta designuri cu mai putine bilete numai offline; promoveaza un fisier
  doar dupa validare exhaustiva la 100%.
- [ ] Adauga teste explicite pentru bugete imposibile si raportarea acoperirii
  partiale.

Criteriu de iesire: nicio regresie de acoperire, costul scade numai cu dovada
reproductibila, iar UI nu confunda pool-hit cu ticket-hit.

### P2 - reproductibilitate si operare

- [ ] Adauga CI Windows pe Python 3.14 cu pytest si smoke test NiceGUI.
- [x] Muta cache-ul WF in `D:\_BUILD\_LOTO` si adauga override prin env,
  cu migrare si inventariere a cache-urilor vechi.
- [ ] Adauga o comanda unica de diagnostic pentru versiuni, metode, curare,
  cache-uri, designuri si dependinte.
- [ ] Masoara timpii pe etape inainte de orice optimizare de performanta.

Criteriu de iesire: un checkout curat se instaleaza si se verifica automat, iar
OneDrive nu mai este pe traseul cache-ului greu.

### P2 - modularizare

- [ ] Extrage din `app_nicegui.py` serviciile de benchmark, WF, raport si mail.
- [ ] Extrage din `loto_engine.py` orchestration, scoring si wheeling adapters.
- [ ] Pastreaza contractele publice si adauga teste de caracterizare inaintea
  fiecarei extrageri.

Criteriu de iesire: module cu responsabilitate clara, fara schimbarea output-ului
pipeline-ului sau a contractului UI-worker.

### P3 - cercetare statistica

- [ ] Evalueaza metode noi numai prin protocol predefinit, ferestre externe si
  baseline hipergeometric.
- [ ] Raporteaza intervale de incredere, stabilitate intre perioade si cost CPU.
- [ ] Respinge metodele care castiga doar prin tie-break, leakage, selectie dupa
  rezultat sau multiple testing necontrolat.
- [ ] Nu promova filtre de paritate, decade, sume ori `numere datorate` fara
  dovada externa repetabila.
- [ ] Test preinregistrat pe extrageri viitoare, pornit 2026-09-27:
  `dmd_forecast`, 6/49, pool 6 si 12, 3+, SPRT (`scripts/analysis/forward_test/`,
  protocolul in `PREREGISTRATION.md`). Regula e copia inghetata `frozen_dmd.py`,
  cu hash-ul in `preregistration_2026-09-27.json`; se evalueaza numai extragerile
  cu data dupa 2026-09-27. `test_forward_test.py` cade daca regula sau istoricul
  de dinaintea inregistrarii se schimba; parametrii deciziei sunt fixati si in
  test. Caracteristicile de operare sunt calculate exact, PE FIECARE POOL
  (`sprt_operating.py`): fara avantaj real, jumatate din rulari resping un pool
  in ~2 ani; cu avantajul afirmat real, jumatate confirma un pool abia in 6-7
  ani, iar plafonul de 1.000 de extrageri coboara puterea la 70-76%. Verdictul
  pe metoda (confirmata daca un pool confirma) are alte cifre. Testul nu atinge productia. Pana la
  decizie, metoda nu are avantaj demonstrat si nu se promoveaza. Amendamentele
  (numai inainte de prima extragere evaluata) sunt in `PREREGISTRATION.md`.
  Replicarea preinregistrata pe Germania, Canada, Polonia si Spania (21.821 de
  extrageri nevazute, `external_replication.md`): niciun avantaj, pool 6 1,96%
  fata de 1,86% aleator (p = 0,16), pool 12 14,49% fata de 14,80% (p = 0,90).
- [x] Experiment preinregistrat 2026-10-02 pentru mai multe hituri IN POOL
  (`scripts/analysis/pool_hit_experiment/`): selector meta pe ferestre
  anterioare, Borda pe toate cele 47 de metode de productie, metoda castigatoare
  pe dezvoltare (primele 70% romanesti), Borda pe top-5 recent. Test pe
  Austria/Belgia/Ungaria (6/45), Cehia/Slovacia/Bulgaria (6/49) si ultimele 30%
  romanesti; 32 de teste binomiale exacte, Holm alpha 0,05. Niciun candidat nu
  supravietuieste (cel mai mic p Holm 0,56). Nimic promovat.
  `test_pool_hit_experiment.py` fixeaza amprentele datelor si parametrii.
  Erata 2026-10-07: randul 5/40 din 24-10-2024, corectat in `_ISTORIC`, e
  refacut in memorie din `ERRATA`, deci amprentele raman valide; pe datele
  corectate verdictul ramane acelasi (`RESULTS_2026-10-02.md`).
- [x] Ecran predictiv 2026-10-05 (`scripts/analysis/prediction_screen_2026-10-05.py`):
  491 de scoreri, 6.355 de teste, nul sintetic iid cu reluarea cautarii,
  confirmare pe 30% romanesti si replicare externa. Nimic promovat (§2).
  Geometria biletelor (`ticket_geometry_2026-10-05.py`) a dat variantele
  dispersate, optiune in „Bilet complet” pana la 2026-10-10 (§6).
- [x] Ecran 2026-10-09 (`scripts/analysis/hit_screen_2026-10-09.py`): bateria
  de aleatorism pe 14 serii, puterea unui dezechilibru de bile, semnale intre
  jocuri, „urmareste liderul”, era curenta si plafonul retrospectiv. Nimic
  promovat (§2). Ramas deschis: Joker Urna 1, `frequency` la k16 4+, numai pe
  extrageri viitoare.

Criteriu de iesire: orice schimbare de metoda vine cu experiment reproductibil si
nu este descrisa drept garantie de castig.

## 13. Definitia de gata

O schimbare este gata numai cand:

1. contractele de date, ranking si queue raman coerente;
2. cache-urile relevante au fost invalidate;
3. testele specifice si suita completa trec pe Python 3.14;
4. UI si worker au fost verificate cand schimbarea le atinge;
5. acoperirea a fost recalculata cand se schimba biletele;
6. documentatia descrie starea curenta, nu istoricul incidentului;
7. commitul contine numai fisierele intentionate;
8. `main` si `origin/main` sunt sincronizate.

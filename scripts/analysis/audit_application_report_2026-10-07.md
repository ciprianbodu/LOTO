# Audit aplicație — 7 octombrie 2026

## Rezultat

Codul a fost revizuit pe șapte zone, în paralel: motor, covering și bilete,
bench și decizie, metode, walk-forward, worker și actualizatoare, UI. Fiecare
constatare a fost reprodusă înainte de reparație, iar fiecare reparație are un
test care pică pe codul vechi și trece pe cel nou
(`test_audit_2026_10_07.py`, plus testul de reetichetare din
`test_no_structural_filters.py`).

Nucleul a rezistat la verificare: nicio metodă nu folosește extrageri viitoare
și nu își modifică intrarea; walk-forward-ul paralel dă aceleași intrări ca cel
serial; hiturile recalculate direct din CSV nu au diferențe; cele 151 de
designuri locale acoperă 100%; pool-ul de producție coincide cu top-K-ul din
bench pentru toate metodele de producție. Defectele găsite stau în jurul
nucleului: date, decizie, audit, afișare.

Nu se promovează nicio metodă și nu se afirmă avantaj predictiv.

## Defecte reparate

### Afișare

1. **Regresie din PR #142.** Helperul nou `ui_bench._pct` purta același nume
   cu `ui_results._pct`. `_sync_ui_namespace` copiază numele private în toate
   modulele UI, iar ultimul definit le înlocuiește pe celelalte. Dialogul
   „Bilet complet” afișa 5+ la 5/40 (0,00365%) ca „0.00%” și 5+ la 6/49
   (0,00556%) ca „0.01%”. Helperul se numește acum `_rate_pct`; un test
   interzice două definiții diferite sub același nume privat în modulele UI.
2. **Acoperire incompletă afișată 100%.** Motorul plafonează o acoperire
   incompletă la 99,99, dar trei mesaje o formatau cu o zecimală: 99,95%
   (pool 16, garanție 4, plafon 195 din 196) apărea „100.0%” chiar în
   avertismentul roșu. Acum două zecimale.
3. **„Istoric hits” cu pool-ul cerut, nu cu cel jucat.** Cu o bază restrânsă
   mai îngustă decât pool-ul, tabelul spunea „din 12” și calcula referința
   aleatoare pe 12 numere, deși fiecare pas juca 11. Mărimea vine acum din
   pașii WF.
4. **Raportul integral** nu spunea că validarea WF e parțială; acum o spune,
   ca panoul.
5. **Mailul.** Linia „cel mai bun rezultat” folosea ordinea rândurilor și
   extrageri nevalidate: un CSV încărcat descrescător dădea „ultima oară” o
   dată veche. Acum cronologie și extrageri valide, ca în UI. Rezerva
   `frequency` nu mai e numită „câștigătoarea bench-ului”.

### Date

6. **`update_csv.py` putea lăsa o gaură permanentă în istoric.** Scriptul
   adăuga extragerile de pe pagina recentă mai noi decât ultima dată din CSV,
   fără să verifice că pagina mai ajunge până la ea. După câteva luni fără
   pornire, extragerile dintre ele lipseau definitiv, iar PushHistory publica
   fișierul. Acum refuză pagina fără suprapunere și o spune.
7. **A doua extragere a unei zile deja stocate se pierdea** (filtrul `>`).
   Acum `>=`, cu deduplicare pe (dată, numere sortate, Joker).
8. **Un fișier blocat (Excel) oprea actualizarea tuturor jocurilor** și ieșea
   cu 1. Acum eroarea rămâne la jocul ei, iar mesajul final numește jocurile
   neactualizate.
9. **`update_externe.py` sărea ani întregi la Germania**: cerea anii
   `{start, azi}`, nu intervalul. Acum toți anii.

### Decizie și bench

10. **Decizia de producție se putea reconstrui dintr-un `folds.csv` parțial.**
    Runner-ul îl rescrie la fiecare 100 de folduri, intenționat, pentru
    clasamentul live. Schimbarea țintei 3+/4+ recalcula decizia din el, fără
    verificare. Pe un flush parțial, 9 din 15 celule 6/49 schimbau scorerul,
    fără niciun `low_confidence`. Acum recalcularea cere ca `folds.csv` să
    acopere Re-Bench-ul deciziei (`missing_decision_folds`); altfel decizia
    rămâne, cu avertisment. Cât rulează un bench, recalcularea se amână până
    la final.
11. **Un scorer interzis sau necunoscut cădea pe vechiul câștigător după
    `avg_hits`** (`winners_per_pool_best`, `overall_winner`), care n-a trecut
    poarta față de random: `croston_interval` la 6/49 k12, `season_period2`
    la Joker k6, deși decizia îl exclusese ca dependent de tie-break. Acum
    `frequency`, cum cere AGENTS.md.
12. **`--block-size` > 1 rescria decizia** și prospețimea o declara la zi.
    Acum e rulare redusă, ca `--quick` (`reduced_run_reason`).
13. **Prospețimea ștampila CSV-urile de la finalul bench-ului.** O extragere
    adăugată de ACTUALIZARI.bat în orele de bench apărea „la zi”. Acum se
    ștampilează semnăturile luate înainte de rulare.
14. **Departajarea după nume depindea de ordinea rândurilor**: suma rată × n
    în altă ordine diferea în ultima zecimală (39 de diferențe la 5 permutări).
    Perechile se sumează acum în ordine fixă; pe `folds.csv` canonic decizia
    rămâne identică octet cu octet, la ambele ținte.

### Motor și metode

15. **Rezerva `frequency` era raportată ca decizie de bench.** La România fără
    `best_methods.json`, fără intrarea jocului sau cu un nume respins, auditul
    scria doar `{"method": "frequency"}`. Acum `fallback` / `no_decision` /
    `attempted`, ca la celelalte țări. Pool-ul e același (aceeași funcție).
    Urna 2 fără coloana joker nu mai apare ca scorată.
16. **`rank_ensemble_core` favoriza numerele mari.** Rangul membrilor se
    calcula după poziție (argsort stabil), deci la egalitate numărul mai mare
    primea rang mai bun. `freq_window_200` are zeci de egalități pe apel.
    Metoda era câștigătoarea de producție la 5/40 k6. Pe extrageri uniforme,
    numărul 20 al Urnei 2 ieșea primul cu 6,2% (aștept 5%). Acum rang mediu,
    cu zgomotul de 1e-15 tratat ca egalitate: echivariantă exact. Testul nou de
    reetichetare cere asta tuturor metodelor de producție. Bump bench `v22`,
    WF `v31`, worker `v12`.
17. **Penalizarea recentă cu factorul 0** ducea numerele penalizate la scorul
    0; locurile rămase în pool se umpleau după „numărul mai mare întâi”
    (N=30 la 6/49: pool-ul 38..49). Acum penalizatele coboară sub toate
    celelalte, dar își păstrează ordinea scorului. Nivelurile de scor din
    audit se numără cu rotunjire relativă.
18. **Avertismentul „scorer degenerat”** apărea și când blocul consecutiv
    venea din intervalul restrâns de utilizator, egal cu pool-ul. Acum nu.
19. Candidații Joker din audit treceau pe lângă `rank_by_score`; acum nu.

### Walk-forward și concurență

20. **Pași scorați de două decizii, salvați sub cheia primei.** Pașii recitesc
    decizia la fiecare extragere; o țintă schimbată sau un Re-Bench terminat în
    timpul WF amesteca scorerii, iar cache-ul păstra rezultatul sub cheia
    vechii metode (23 din 28 de pași greșiți în reproducere). Acum WF nu mai
    salvează cache-ul și UI-ul o spune.
21. **Cursă în `_load_config`** între firul WF și firul UI: calea, mtime-ul și
    conținutul se publicau separat (cale DE, conținut RO). Acum împreună, sub
    lock.
22. **Reluarea „Bilet complet” se calcula de două ori**: temporizatorul de 1 s
    repunea în coadă cererea aflată în calcul. Ultimul bloc apărea la 5T în loc
    de 3T.
23. **Semnătura designurilor includea calea absolută** a checkout-ului:
    mutarea repo-ului (recomandată, în afara cloud-ului) arunca tot cache-ul
    WF. Acum poziția în ordinea de căutare, numele și conținutul.
24. **Detecția worker-ului** rezolva argumentul relativ `worker.py` al
    oricărui proces față de directorul UI-ului: un worker din alt checkout
    trecea drept al nostru, iar jobul rămânea PENDING. Acum față de directorul
    procesului.

### Bilete

25. **Variantele dispersate** încălcau limita de consecutive pe baze înguste,
    iar nota spunea că o respectă (2 variante din 9 cu 3 consecutive, deși
    existau 10 combinații conforme). Acum o reparare prin enumerare le
    înlocuiește; când combinațiile conforme nu ajung, nota spune limita atinsă.
26. **Reluarea compara număr inegal de variante** pe pașii din fallback-ul de
    frecvență (1 față de 3). Pașii aceștia se numără acum indisponibili.

## Rămase de decis după runda 1

Punctul 1 a fost rezolvat separat, în PR #144, iar punctul 5 în PR #145;
celelalte, în runda 2 (secțiunea de la final).

1. **`_ISTORIC/loto_5_40.csv`, rândul 1538.** `24-10-2024,13,34,11,16,10,39`
   copiază extragerea din 27-10-2024. Extragerea reală din 24-10-2024 este
   `14,15,28,10,25,26` (fanatik.ro și stiripesurse.ro; aceleași surse coincid
   cu CSV-ul la 6/49 și Joker pentru ambele date). Corectura nu s-a aplicat:
   modificarea fișierului de istoric a fost refuzată de permisiunile sesiunii.
   Corectura invalidează bench-ul și WF-ul 5/40 și atinge amprenta
   experimentului preînregistrat din 2 octombrie (prefixul de 1740 de rânduri
   conține rândul; ținta e în fereastra de test a `ro_540_k11`, una din 522).
   Testul `test_pool_hit_experiment.py` va cere o erată documentată.
   *Rezolvat ulterior, în aceeași zi:* rândul a fost corectat după arhiva
   oficială loto.ro, iar erata experimentului este în
   `pool_hit_experiment/RESULTS_2026-10-02.md`.
2. **Poarta de consistență pe ferestre imbricate** spune puțin: un singur
   eveniment 4+ în ultimele 10% contează în 3 din 4 ferestre. Pe 5/40 k5,
   trei metode se califică cu exact un eveniment în 1661 de extrageri. O
   metodă fără semnal trece poarta cu probabilitatea 0,27–0,45; cu ~50 de
   candidați, una trece aproape sigur, iar `low_confidence` nu apare (0 din 46
   de celule). Schimbarea porții e o decizie de metodologie.
3. **Metode care reiau ultima extragere.** `ridge_pooled_feats` (producție la
   Joker k14) are toată extragerea precedentă în top-14 la 72,5% din pași;
   `markov_self_state` alege numărul Joker precedent la 45,7% (5% la
   întâmplare). Pe date sintetice uniforme efectul rămâne, deci vine din
   metode. Este exact comportamentul pentru care `repeat_last_draw` și
   `haar_multiscale` sunt excluse. Propunere: o coloană `lastdraw_kN` cu
   poartă, ca `tiebreak_kN`.
4. **Duplicatul Urnei 2.** `naive_bayes_last` și `markov_pairs` dau același
   clasament pe Urna 2 (658 din 658 de pași); propunere: scoaterea primului
   din `per_game.joker_urna2`.
5. **PushHistory publică orice schimbare din `_ISTORIC`** (`git add -A`):
   ștergeri, fișiere reformatate de Excel, copii de conflict din cloud.
   Propunere: `git add -u -- _ISTORIC` și refuz dacă `--numstat` arată linii
   șterse. Nemodificat aici: lansatorul nu poate fi rulat în container, iar o
   eroare în el ar bloca fiecare pornire.
6. **Marcajul `last_finalized_job_id`** stă în `.ui_state.json` din checkout,
   sincronizat între stații, iar id-urile de job pornesc de la 1 pe fiecare
   stație. Un job neafișat al altei stații poate fi considerat finalizat.
7. Neschimbate, din alegere: la eșecul recalculării deciziei, panoul de
   rezultate nu se redesenează (secțiunile deschise rămân, AGENTS.md §5);
   swap-ul de profil la garanție egală cu biletul durează ~9 s la pool 16,
   pick 6 (~6 s la pick 5, 1,9 s la garanția 4), la fiecare pas WF;
   documentat, fiindcă un cache pe `_event` ar ține zeci până la sute de MB.

## Cache

| Strat | Înainte | Acum | Motiv |
|---|---|---|---|
| bench fold | `v21` | `v22` | `rank_ensemble_core` schimbă scorurile |
| walk-forward | `v30` | `v31` | metoda, penalizarea cu factorul 0, semnătura designurilor |
| worker pipeline | `v11` | `v12` | aceleași pool-uri; stratul rămâne inert |

Bump-ul de bench face prospețimea să ceară un Re-Bench complet, cum o face
oricum orice extragere nouă (hash-ul istoricului e în cheia foldurilor).

## Verificări

- Suita completă pe Python 3.14.7 / Linux, fără PowerShell: 95 de fișiere
  `test_*.py`, **2152 de teste trecute, 40 omise** (integrarea lansatorului,
  numai pe Windows), zero eșecuri, 244,9 s. Înainte de reparații: 2077
  trecute, 40 omise, zero eșecuri.
- `scripts/analysis/audit_application.py`, înainte și după reparații: 13
  istorice, 848 de verificări de paritate pe scoreri, 151 de designuri, 26 de
  pipeline-uri de producție, worker separat cu payload decodat, UI HTTP 200,
  fișierele de producție neatinse.
- Decizia pe `folds.csv` versionat: identică octet cu octet înainte și după
  sumarea în ordine fixă, la țintele 3 și 4.
- Reproducerile agenților rerulate după reparații: rang mediu echivariant
  (0 din 90 de cazuri), reluare o singură dată (3 calcule, nu 6), 0 diferențe
  de decizie la permutarea rândurilor, auditul rezervei pe toate cele șase
  scenarii.

## Runda 2

La cererea utilizatorului, punctele rămase au fost tratate în aceeași zi.
Panoul de rezultate rămâne neredesenat la eșecul recalculării deciziei:
secțiunile deschise se păstrează intenționat (AGENTS.md §5, commit `52b9523`).

1. **Rândul 1538 din `_ISTORIC/loto_5_40.csv`** a fost corectat în PR #144,
   integrat pe `main` înaintea rundei 2, împreună cu erata experimentului și
   testul care respinge două rânduri consecutive identice
   (AGENTS.md, „Corectura istoricului 5/40”). Runda 2 nu îl mai atinge.
2. **Poarta de consistență.** Fiecare celulă a deciziei primește
   `multiplicity`: test binomial unilateral pe fereastra completă, excesul de
   evenimente față de rata aleatoare, cu corecția Holm peste candidații
   celulei. Ferestrele sunt sufixe ale aceluiași walk-forward; fereastra
   completă le conține pe celelalte, deci niciun eveniment nu se numără de două
   ori. Scorerul ales și `low_confidence` nu se schimbă: o poartă mai strictă
   ar trimite aproape toate celulele pe `frequency`, schimbare de metodologie
   care nu s-a decis aici. Verdictul apare în rationale, în clasament, în
   panoul de rezultate și în notificarea Auto-Pilot. Pe `folds.csv` versionat,
   45 din 46 de celule rămân cu avantaj nedemonstrat. Singura sub prag, 6/49
   k11 (`croston_interval`, p = 0,00068, Holm 0,029 pe 43 de candidați), nu ar
   trece o corecție peste cele 46 de celule; corecția aceasta nu se aplică.
3. **Metodele care reiau ultima extragere.** Pe Urna 2, top-1 este bila
   precedentă la `markov_self_state` (66% din pași pe extrageri uniforme) și
   la `vlmm_self_k3` (63%), față de 5% la întâmplare: clasa „ultima bilă”, ca
   la `haar_multiscale`. Ambele intră în `EXCLUDED_FROM_SINGLE_PICK`. Decizia
   le sare la `draw_n == 1`, iar producția le respinge pe Urna 2 și cade pe
   `frequency` când un `best_methods.json` vechi le numește.
   `test_no_structural_filters` măsoară rata pe 100 de istorii uniforme și
   oprește orice altă metodă care ar alege bila precedentă în majoritatea
   pașilor. Pe jocurile cu pool nu s-a introdus poartă `lastdraw_kN`:
   extragerea precedentă ocupă cel mult 6 din cele K locuri, restul pool-ului
   rămâne clasamentul metodei, iar bench-ul o compară cu rata
   hipergeometrică. Poarta ar cere o coloană nouă în bench (bump și Re-Bench
   complet) și ar exclude metode de recență pentru o proprietate care nu
   constrânge combinația.
4. **Urna 2 fără duplicat.** `per_game.joker_urna2` are 48 de metode: fără
   `naive_bayes_last` (același clasament ca `markov_pairs` în 658 din 658 de
   pași) și fără cele două metode de la punctul 3.
5. **PushHistory** este tratat separat, în PR #145: `git add -u`, refuz per
   fișier pentru rândurile șterse sau modificate, fișierele non-CSV și cele
   noi, plus validarea rândurilor adăugate cu `verifica_istoric.py`, testate pe
   Windows. Runda 2 nu modifică lansatorul, ca să nu existe două implementări.
6. **Marcajul jobului preluat de UI** stă pe rândul jobului din baza stației
   (`jobs.ui_finalized_at`, migrare aditivă, `mark_job_finalized`), nu în
   `.ui_state.json` din checkout-ul sincronizat. Golirea cozii șterge marcajul
   odată cu jobul, deci un job nou cu același id pornește nemarcat. Cheia
   veche se migrează o dată la pornirea UI, cu regula veche (ultimul job
   COMPLETED cu acel id), și dispare din fișier numai după ce marcajul a
   ajuns în bază; cu baza blocată rămâne, iar recuperarea din aceeași pornire
   nu reia jobul (semnalat de review-ul Codex pe PR #146). `reset_jobs.py` păstrează
   ultimul COMPLETED nemarcat. `conftest.py` redirecționează marcarea din teste
   spre o bază temporară.
7. **Swap-urile de profil**, mai rapide cu rezultat identic. Evenimentele unui
   candidat se construiesc dintr-o singură trecere peste pozițiile biletului,
   iar prefixul comun al blocurilor consecutive (ordine lexicografică) se
   păstrează; dominanța se verifică numai pe straturile unde biletul scos
   pierde ceva; rândurile complete se calculează numai la câștig egal.
   Comparat cu căutarea veche pe 591 de cazuri (81 capturate din
   `generate_wheel`, 400 aleatoare, 110 din designuri lotto și dispatch):
   aceleași bilete, aceleași schimburi, același număr de candidați evaluați.
   `test_profile_swap.py` păstrează căutarea de referință și o compară; testul
   prinde mutațiile în departajare, în verificarea dominanței și în recurența
   evenimentelor. Generarea completă, pool 16, plafon 10:

   | Bilet / garanție | Înainte | Acum |
   |---|---:|---:|
   | 6 / 6 | 9,41 s | 1,43 s |
   | 5 / 5 | 5,50 s | 1,27 s |
   | 6 / 4 | 1,05 s | 0,51 s |
   | 5 / 4 | 0,97 s | 0,40 s |

   Fără bump de cache: rezultatul serializat nu se schimbă.

Verificare, runda 2:

- Suita completă pe Python 3.14.7 / Linux cu pwsh 7.6.2, împreună cu PR #145:
  96 de fișiere `test_*.py`, **2283 de teste trecute, 38 omise** (lansatorul,
  numai pe Windows), zero eșecuri, 273 s.
- `scripts/analysis/audit_application.py`: 53 de metode, curare 52/51/51/48,
  13 istorice, 848 de verificări de paritate, 151 de designuri, 26 de
  pipeline-uri, worker separat pe 13 jocuri, UI HTTP 200, fișierele de
  producție neatinse.
- Marcajul, cap-coadă pe o bază izolată: worker-ul real termină jobul, prima
  pornire a UI-ului îl recuperează o singură dată și îl marchează, a doua nu-l
  mai reia; cheia veche a altei stații dispare din `.ui_state.json`, iar
  checkout-ul nu primește `raport_complet.txt` sau `.ui_state.json`.

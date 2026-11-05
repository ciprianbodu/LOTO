# Audit pe module și mai multe hituri 4+ — 2026-10-22

Cerința: verificare globală, pe module, și optimizare pentru mai multe hituri
4+. Toate șansele de mai jos sunt exacte, pe extrageri uniforme (enumerare,
fără Monte Carlo). Rata pool-ului rămâne hipergeometrică: nicio metodă nu are
avantaj predictiv demonstrat (§2, §5), deci singura pârghie e așezarea
numerelor pe variante, la același cost.

## Cum s-a lucrat

- Zece auditori, câte unul pe zonă: motor, covering de bază, etapele de
  covering, benchmark, walk-forward, „Bilet complet”, fațada UI, panourile UI,
  worker și coadă, date și scripturi. Fiecare constatare a fost reprodusă de
  auditor, apoi verificată separat de un al doilea agent (reproducere pe HEAD)
  și de un arbitru pe contractele din AGENTS.md.
- Cinci cercetători pe pârghiile pentru 4+, apoi un judecător. Cele cinci cifre
  pe care se sprijină concluzia au fost recalculate independent, cu alte
  metode (enumerarea tuturor extragerilor): toate se confirmă.

## Audit: 14 defecte reparate, fiecare cu test care pică pe codul vechi

Testele: `test_audit_2026_10_22.py` (18 pică pe HEAD, 2 sunt gărzi de
neschimbare).

| Zonă | Defect | Gravitate |
|---|---|---|
| benchmark | Scrierea finală a `folds.csv` refuzată (fișier ținut deschis) se loga la DEBUG; decizia de producție se construia din `folds.csv` vechi, ștampilată „proaspătă”. Acum se reîncearcă, apoi bench-ul se oprește. | majoră |
| benchmark | La 5/40 consola scria „hits@k6” pentru media pe k5, iar tabelul regresiv arăta k6, nu pool-ul de bază k5. | măruntă |
| benchmark | Pe Windows `_ISTORIC` și `_istoric` sunt același folder: fiecare bench scria trei avertismente false „nume nerecunoscut”. | măruntă |
| motor | Factorul 0 al penalizării recente punea numerele penalizate peste cele nepenalizate cu scor 0. Acum sub toate; cu scoruri pozitive rezultatul e identic bit cu bit. Cheia WF `|rpz1` numai la factorul 0; worker `v22`. | minoră |
| covering | Un scor lipsă valora 0.0 la așezarea pe poziții, nu coada promisă. | măruntă |
| covering | Un spațiu neseparabil sau o cifră Unicode într-o secțiune H arunca tot fișierul. Acum numai secțiunea ei; amprentele secțiunilor valide rămân. | minoră |
| date | `update_csv`: un rând de pe site cu o celulă în plus trecea; la Joker al șaselea număr devenea Joker-ul. Acum rândul e „necitibil” și jocul nu se scrie. Pe paginile reale: 3.155 de rânduri citite la fel, zero respinse. | minoră |
| date | `update_externe`: o sursă care repeta ultima extragere sub o dată nouă scria copia (§4.1). | minoră |
| date | `analiza_4plus` și `analiza_escalada` cădeau cu UnicodeEncodeError la ieșire redirecționată. | măruntă |
| UI | Legenda clasamentului live spunea „+3, +4 și +5”; Istoric hits arată numai +4 și +5. | minoră |
| UI | „Cod X față de wheel-ul nostru” putea scrie două șanse diferite la fel. | măruntă |
| worker | O eroare SQLite trecătoare la scrierea rezultatului marca jobul FAILED și pierdea rezultatul. Acum se reîncearcă, apoi rezultatul se salvează pe disc. La progres, eroarea nu mai oprește rularea. | minoră |
| worker | Curățarea de la START_8000 urmărea ppid-ul brut: un PID reciclat putea „adopta” un proces mai vechi (editor) și îl omora. | minoră |
| worker | Calea proiectului se potrivea ca prefix de text (un folder frate `_LOTO_old`). | măruntă |

Respinse (15): intrare imposibilă din producție, comportament documentat sau
deja cunoscut. Lista și motivele sunt în jurnalul workflow-ului.

Revizia adversarială a diff-ului (4 zone, fiecare constatare verificată
separat) a mai găsit 5, toate reparate, cu teste: un bench fără niciun fold
reconstruia decizia din `folds.csv` vechi; la factorul 0, un penalizat cu scor 0
trecea sub unul cu două apariții; Ctrl+C în pauza dintre încercări lăsa copia
tmp; corecția UTF-8 n-avea test; două cifre din acest raport.

Testele lansatorului (8 eșecuri pe suita inițială) nu erau defecte ale
aplicației: terminalul de agent setează `NoDefaultCurrentDirectoryInExePath=1`,
iar Git Bash pune binarul MSYS `ucrt64\bin\git.exe` primul în PATH. Testele
modelează acum mediul Explorer-ului.

## Pârghiile pentru 4+

Configurația utilizatorului: pool 16, garanția 4, fără plafon (6/49: 152 de
variante; 5/40 și Joker: 404), „Bilet complet” cu 2 bilete, Cod 59 la agenție.

### E. Contabilizarea hiturilor: corectă, configurația e la plafon

Recalculare independentă pe ultimele 60 de extrageri ale fiecărui joc: zero
diferențe față de `run_honest_walk_forward` și `ui_hits` (pool, extragere,
bilete, 4+ și 5+). La pool 16 cu coverul complet, șansa 4+ pe o variantă e
exact rata pool-ului, plafonul oricărei așezări pe 16 numere:

| Joc | Variante | 4+ pe o variantă |
|---|---:|---:|
| 6/49 | 152 | 1807/22701 = 7,960002% |
| 5/40 | 404 | 338/2109 = 16,026553% |
| Joker | 404 | 8164/174537 = 4,677518% |

Un câștig de 1% relativ înseamnă o extragere în plus cu 4+ la 12, 6 și 20 de
ani (6/49, 5/40, Joker; ~105 extrageri pe an).

### C. „Bilet complet”: nimic la 2 bilete

La 2 bilete pe joc, 4+ e dovedit optim la toate trei jocurile (6/49 căutare
exhaustivă peste 225.837.241 de așezări; 5/40 prin numărare; Joker exhaustiv).
Câștiguri certificate apar abia de la 3-4 bilete, cel mult +1,5% la 6/49 cu 7
bilete. Nelivrat.

### D. Designuri cu buget pentru biletul de 5: livrat

Cu plafon între 270 și 403 variante, motorul rămânea la 4+ până la 3,9% sub o
tăiere greedy a coverului complet de 404 (Joker, 388 de variante; la 5/40 cel
mult 3,1%, la 379). La 403 variante motorul era cu 3,5% sub coverul complet de
404 (Joker: 4,516% față de 4,678%). `covering_designs/budget/H_16_5_4.txt` are
92 de secțiuni, în principal 321..403 (zece între 270 și 319), numai
unde designul e certificat pe AMBELE jocuri, cu cel puțin +1% la 4+, și nu la
mărimea Cod 37 (348). Generator: `gen_budget_designs_pick5.py`; cifrele exacte
pe fiecare buget: `budget_designs_pick5_2026-10-22.json`. La 6/49 nicio
secțiune H nu se mai poate îmbunătăți certificat (B112 e optimă la fiecare
prag).

Fără plafon (configurația de azi) fișierul nu se citește.

### A. Pool de 17-20 numere pe același număr de variante: necesită decizie

La același număr de variante, un pool mai mare cu un cover parțial bate ORICE
așezare pe 16 numere la 4+ (dovedit prin marginea superioară, nu prin căutare).
Niciun prag nu scade, iar numărul absolut de grupe de 4 acoperite crește.

| Joc, variante | 16 numere | 17 | 18 | 20 |
|---|---:|---:|---:|---:|
| 6/49, 152 | 7,960% | 8,499% | 9,320% | 10,010% |
| 6/49, 112 (Cod 59) | 7,431% | 7,462% | 7,588% | 8,134% |
| 5/40, 404 | 16,027% | 17,220% | 17,551% | 18,944% |
| Joker, 404 | 4,678% | 5,095% | 5,165% | 5,381% |

Prețul: garanția „4 sigur dacă ies 4 din pool” devine parțială (la 152 de
variante, 83% din grupe la 17 numere, 72% la 18, 47% la 20). Numărul așteptat de
variante câștigătoare nu se schimbă: câștigurile se împart pe mai multe
extrageri. Pe 778 de extrageri WF diferența nu s-ar vedea. Cere ridicarea
limitei UI 6..16 și un evaluator exact pentru 17-20 numere; nelivrat fără
decizia utilizatorului.

### B. Portofolii de coduri LR pe pool-uri disjuncte: contrazice §6

La 4+ șansa unui portofoliu e suma șanselor membrilor (două pool-uri disjuncte
nu pot avea amândouă 4 din cele 6 extrase). Exemple jucabile la agenție, numai
cu coduri: 6/49 Cod 49 + Cod 57 + Cod 59 (38 de numere, 149 de variante,
1.192 lei): 10,32% față de 7,96%; 5/40 2×Cod 25 + Cod 35 (375 de variante): 18,86%
față de 16,03%, dar 5 mai mic. Variantele ies din primele 16 ale metodei, iar
garanția coboară la 1-4% din grupe: exact ce a scos utilizatorul la 2026-10-10.
Nelivrat.

## Reproducere

```
python scripts/analysis/gen_budget_designs_pick5.py
python -m pytest -q test_audit_2026_10_22.py test_budget_designs.py
```

@AGENTS.md

Lucrare curentă: optimizare 5/40 la 21 variante (15.11.2026).
Design pe 40 de numere, cu P(4+) global maxim 1,646788489% la exact 21
variante; comparația C(7,5) avea 0,499689973%. Nu este selecție predictivă.
Acțiunea este explicită pe rezultatul C(7,5); păstrează setările celorlalte
jocuri și arată schimbarea garanției și taxele pe bilete.
Validare: 7.257 teste trecute. 25 omise. 0 eșecuri și 0 erori; 219 fișiere. Python 3.15.2. trei copii/runtime-uri izolate. Include enumerarea celor 3.838.380 extrageri,
WF, worker/cache, UI în browser și integrarea tuturor celor 13 jocuri.
Raport: scripts/analysis/fixed_budget_540_2026-11-15.txt.
UI și worker vor încărca împreună sursele noi la lansarea normală.

Lucrări anterioare:
Lucrare curenta: pool pana la universul jocului (30.10.2026), finalizata.
Maxim 49 / 40 / 45, implicit pastrat 16 / 19 / 15; setari, worker, generare,
WF si note UI adaptate. Validare cumulata: 3896 teste distincte trecute,
20 sarite; asertiunea veche a tabelului de intervale actualizata si reluata.
Auditul general, proba worker pe pool-urile maxime si verificarea in browser
au trecut in runtime-uri izolate. Detalii si contractele de resurse/cache:
scripts/analysis/full_pool_2026-10-30.md. Aplicatia activa nu a fost repornita.
Inregistrarea orfana Python 3.11 ramane, conform raspunsului utilizatorului.

Lucrare incheiata: premii oficiale in Istoric hits (30.10.2026).
Suita completa: 3801 trecute, 20 sarite; dupa fixul de actualizare a tabelelor,
94 de teste UI/regresii trecute. Auditul aplicatiei si browserul sunt verificate.
Sursele, schema cache si limitele sunt in scripts/analysis/official_prizes_2026-10-30.md.
Baza oficial_prizes.db este locala statiei, in RUNTIME_ROOT, si nu se comite.
Interfata se activeaza la repornirea normala; datele/scorerii nu se modifica.

Lucrare incheiata (30.10.2026): optimizari masurate pe CPython 3.14.10 pentru
scoring, profilul exact al biletelor si incarcarea cache-ului walk-forward.
Sursele si contractele sunt descrise in AGENTS.md; masuratorile si comenzile de
reproducere sunt in scripts/analysis/python314_optimization_2026-10-30.md.
Codul si verificarea sunt finalizate: 3695 teste distincte trecute, 20 sarite
(modulul Git reluat pe o cale temporara scurta), audit UI/worker reusit.
Runtime tinta: D:\_BUILD\_LOTO\.venv, ultimul Python 3.15.x stabil x64. Aplicatia activa
se reporneste normal pentru a incarca sursele noi; formatele cache nu se schimba.

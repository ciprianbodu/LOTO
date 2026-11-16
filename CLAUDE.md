@AGENTS.md

Lucrare curentă: glisoare pe scheme LR și generare fizică (16.11.2026).
Fiecare poziție RO este un cod oficial, inclusiv două coduri cu același pool;
helperul afișează codul, numerele, variantele, costul și garanțiile exacte.
Selecția după pool alege minimum de variante; selecția explicită păstrează
codul. Pornirea rămâne la codurile 48/16/24, pooluri 9/8/10, pentru
6/49/5/40/Joker.
Configurația UI RO transmite lr_scheme_code; motorul și simularea folosesc
exact rândurile LR în ordine numerică. Joburile legacy fără flag și jocurile
străine rămân compatibile. Cache-urile și istoricul păstrează identitatea
codului; sidebarul mutat ulterior nu schimbă sistemul evaluat. Garanțiile
oficiale se afișează separat de metrica auxiliară la pragul 4.
Testele noi/adaptate rămân locale. Validare finală: 7.312 teste trecute, 25 omise, zero eșecuri/erori, 220 fișiere, Python 3.15.2.
  UI/worker real și verificare în browser trecute; testele rămân locale.
Raport: scripts/analysis/lr_scheme_sliders_2026-11-16.md.

Lucrare anterioară: verificare 4+/5+/6+ exclusiv pe scheme reduse LR (15.11.2026).
Fluxul LR existent este păstrat pentru toate cele trei jocuri. Acțiunea separată
cu 21 de variante simple și etapa specială non-LR sunt retrase integral.
Pornirea folosește codurile confirmate: 6/49 Cod 48, pool 9, 12 variante;
5/40 Cod 16, pool 8, 21 variante; Joker Cod 24, pool 10, 14 variante.
Bugetul se raportează la codul LR, nu la sistemul complet intern. Catalogul
și toate partițiile acestor bugete în coduri LR nu oferă dominanță simultană
4+/5+/6+ la același număr de variante ori mai puține, la extrageri uniforme.
Nu este demonstrată o creștere a hiturilor; studiile LR existente nu justifică
un scorer nou. Schemele oficiale, istoricele și deciziile sunt păstrate.
Validare: 7.223 teste trecute, 25 omise, zero eșecuri și erori, 218 fișiere, Python 3.15.2. UI/worker real pe 9/8/10, izolat.
Corecția numerică Hedge și cache-urile sale au fost verificate
independent; nu se afirmă o creștere a hiturilor.
Testele noi/adaptate rămân locale, în afara commitului.
Raport: scripts/analysis/lr_only_2026-11-15.md.

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

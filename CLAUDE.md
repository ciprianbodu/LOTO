@AGENTS.md

Lucrare curenta (30.10.2026): optimizari masurate pe CPython 3.14.10 pentru
scoring, profilul exact al biletelor si incarcarea cache-ului walk-forward.
Sursele si contractele sunt descrise in AGENTS.md; masuratorile si comenzile de
reproducere sunt in scripts/analysis/python314_optimization_2026-10-30.md.
Codul si verificarea sunt finalizate: 3695 teste distincte trecute, 20 sarite
(modulul Git reluat pe o cale temporara scurta), audit UI/worker reusit.
Runtime tinta: D:\_BUILD\_LOTO\.venv, Python 3.14.10 x64. Aplicatia activa
se reporneste normal pentru a incarca sursele noi; formatele cache nu se schimba.

# Ecran de aranjamente geometrice / cover design, 2026-11-01

## Concluzie

22 de configurații (3 jocuri × {buget peste designul complet, buget sub complet
g=3, rafinare peste complet v=14..16} + diagnostic ciclic), toate măsurate EXACT
(numărători întregi, extragere uniformă) și certificate cu regula de producție
(`covering.anneal_hits._certify`: aceleași bilete distincte, numerele jucate
păstrate, niciun prag 3..draw_n în scădere, câștig strict, acoperirea garanției
nedescrescătoare).

1. **Buget peste designul complet — da, mai multe bilete cresc 5+ la aceeași
   acoperire 4 = 100%, dar costă mai mult, nu e gratuit.** Greedy la 6/49
   pool 16 g4 se oprește singur la 198 de bilete (nu umple cei 200 ceruți) cu
   5+ 0,3292%, față de designul complet 152 bilete cu 5+ 0,2514% (+31% relativ la
   +46 de bilete ≈ +368 lei). 5/40: 467 bilete 5+ 0,4106% vs 404 complete 0,3631%.
   Joker: 467 vs 404 → 5+ 0,0382% vs 0,0331%. Recoacerea adaugă peste greedy doar
   +0,0001..0,0005 pp (certificat, dar neglijabil). Atenție: motorul, la plafon
   ≥ designul complet, ia exact designul complet — deci „cheltuitul în plus”
   nu e disponibil azi prin plafon; e o decizie de cost, nu un câștig liber.
2. **Buget sub complet, g=3, țintă 4+:** îmbunătățiri certificate mici la
   același număr de bilete — 6/49 B=64/100 (greedy se oprește la 45): 4+
   3,4774% → 3,4899% (+0,0125 pp); 5/40 B=100 (77 bilete): 5,0423% → 5,0567%
   (+0,0144 pp); Joker B=100: 1,1786% → 1,1844% (+0,0057 pp). B=64 la 5/40 și
   Joker nu a ajuns la acoperire 100% a lui g=3 (90,5%) și nu s-a certificat.
3. **Rafinare peste coverul complet v=14/15/16 g=4 pentru 5+:** nicio
   îmbunătățire certificată — confirmă frontiera din 2026-10-16 (la pool 16 g4
   3+/4+ sunt deja maximul; pentru 5+ nu s-a găsit nicio rearanjare
   certificată nici cu buget mai mare de căutare).
4. **Design ciclic (blocuri de bază + shifturi mod 16), diagnostic:** 192 de
   bilete cu acoperirea 4 doar 51,9% și 4+ 4,53%, față de greedy la același
   număr de bilete: acoperire 100%, 4+ 7,90%. Ciclicul e dominat clar.

**Nimic nu se promovează automat în motor** — câștigurile certificate sunt ≤
0,015 pp la același cost, iar efectul „buget peste complet” e o decizie de cost.
Rata pool-ului rămâne hipergeometrică; toate cifrele sunt geometria biletelor,
nu avantaj predictiv.

## Protocol

Fixat în docstring-ul scriptului înainte de rulare. Geometrii ca în aplicație
(6/49 pick6/draw6/max49; 5/40 pick5/draw6/max40; Joker Urna 1 pick5/draw5/max45),
pool canonic 1..v. Baseline = wheel-ul motorului pe poziții canonice
(`dispatch.generate_wheel`, metoda auto) sau greedy la același buget.
Candidanți = recoacere simulată (2 seed-uri × 3000 de mutări, obiectiv exact
`wheel_hit_probabilities`), păstrați doar dacă trec `_certify`. Fișiere:
`cover_geometry_screen_2026-11-01.py`, `cover_geometry_screen_2026-11-01.json`.
Rulare: 345 s, un proces. Validator: `test_advanced_screen_2026_11_01.py`
(inclusiv respingerea certificării pentru designuri mai proaste).

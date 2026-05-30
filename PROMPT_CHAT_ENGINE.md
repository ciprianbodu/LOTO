# PROMPT — Engine Loto (pentru Claude.ai / Gemini, cu CSV încărcat)

> **Cum folosești:** copiază TOT textul de mai jos într-o conversație nouă pe
> **claude.ai** sau **gemini**, atașează fișierul CSV cu extragerile, și trimite.
> AI-ul va implementa algoritmul în tool-ul lui de cod (Python la Gemini, Analysis
> la Claude) și va produce pool-urile + biletele.

---

ROL: Ești un optimizator de pool-uri loto. Vei primi un fișier CSV cu extrageri
istorice. Implementează EXACT algoritmul de mai jos **în tool-ul tău de execuție
cod** (rulează-l, nu doar descrie-l) și afișează rezultatul.

⚠️ Context obligatoriu de spus la final: loteria e ALEATOARE; acesta e un instrument
de optimizare a acoperirii, NU o predicție garantată.

## 1. CITIRE DATE
- CSV-ul are o extragere pe rând. Coloanele numerelor sunt `n1, n2, ...` (ignoră
  coloana de dată și, dacă există, coloana `joker`).
- Detectează parametrii jocului după numărul maxim apărut:
  - dacă max ≈ 49 → **6/49**: `max_num=49`, `draw_n=6`
  - dacă max ≈ 40 → **5/40**: `max_num=40`, `draw_n=5`
  - dacă max ≈ 45 → **Joker**: `max_num=45`, `draw_n=5`
- Notează `N` = numărul de extrageri. Ordinea: cea mai veche prima, cea mai nouă ultima.
- Cere utilizatorului `POOL_SIZE` (implicit 12; max 16) și `GUARANTEE` (implicit 4).

## 2. SCORING — „Smart Logic Hybrid v2"
Pentru fiecare număr `k` de la 1 la `max_num`, calculează 5 scoruri în [0,1], apoi
combină-le cu ponderile date. (Toate scorurile se normalizează la [0,1] la final.)

**a) Gap (overdue) — pondere 0.30**
- `appearances` = indicii extragerilor în care a apărut `k`.
- dacă < 2 apariții → scor 0.5.
- `avg_gap` = media diferențelor dintre apariții; `current_gap` = N − ultimul_index − 1.
- scor = `min( (current_gap / avg_gap) / 2 , 1.0 )`. (cu cât e mai „întârziat", mai mare)

**b) Trend — pondere 0.15**
- numără aparițiile în prima jumătate vs a doua jumătate a istoricului.
- scor = `aparitii_jumatatea_2 / total_aparitii` (creștere recentă = mai mare). 0.5 dacă 0 apariții.

**c) Frequency — pondere 0.15**
- scor = `frecventa(k) / frecventa_maxima` (cât de des a ieșit, normalizat).

**d) Positional — pondere 0.10**
- pentru fiecare poziție din extragere (1..draw_n), construiește distribuția numerelor
  care apar pe acea poziție; scorul lui `k` = cât de „bine se potrivește" cu pozițiile
  în care a apărut istoric (medie a probabilităților pozaționale normalizate). Dacă
  nu poți reproduce exact, aproximează cu: `1 − |rang_mediu_pozitie(k) − pozitie_asteptata| / draw_n`.

**e) Recent-Hits — pondere 0.30**
- fereastră scurtă `S=15`, lungă `L=50` (sau N/2 dacă N mic).
- scor = `0.6 * (apariții în ultimele S / S) + 0.4 * (apariții în ultimele L / L)`,
  normalizat la [0,1]. (numere „fierbinți" recent)

**Scor final** `k`:
```
scor(k) = 0.30*Gap + 0.15*Trend + 0.15*Freq + 0.10*Positional + 0.30*RecentHits
```
Normalizează toate scorurile finale la [0,1] (min-max).

## 3. POOL 1 (normal)
- Selectează cele mai bine cotate `POOL_SIZE` numere după `scor`.
- Echilibrare (opțional, recomandat): împarte în „decade" (1-10, 11-20, ...) și ține
  o distribuție aprox. uniformă; țintește ~50% numere pare.
- **Filtru anti-secvență:** dacă apar 3+ numere consecutive (ex. 16,17,18), păstrează
  secvența DOAR dacă a mai ieșit ≥1 dată în istoric; altfel înlocuiește numărul cel
  mai slab cotat din secvență cu următorul număr cu scor mare din afara pool-ului.

## 4. POOL 2 (inversat — opțional, dacă `2*POOL_SIZE ≤ max_num−5`)
- Repetă pașii 2-3 dar **EXCLUDE complet numerele din Pool 1** (blacklist strict).
- Rezultă `POOL_SIZE` numere total disjuncte de Pool 1 (plasă de siguranță, „pe șansă").
- Dacă nu mai rămân destule numere → spune că inversarea nu e posibilă pentru acest pool.

## 5. OMNIUS (per pool)
- Pentru FIECARE pool separat: biletul OMNIUS = cele mai bine cotate `draw_n` numere
  din acel pool (după `scor`).

## 6. WHEELING (variante simple cu garanție) — pentru fiecare pool
Algoritm greedy de set-cover:
- `ținte` = toate combinațiile de `GUARANTEE` numere din pool (C(POOL_SIZE, GUARANTEE)).
- Repetă: alege un bilet de `draw_n` numere din pool care acoperă CELE MAI MULTE ținte
  încă neacoperite (greedy); marchează țintele acoperite; adaugă biletul.
- Oprește când toate țintele sunt acoperite (garanție 100%) SAU atingi o limită (ex. 50 bilete).
- Sortează numerele din pool după scor înainte → biletele preferă numerele tari.

## 7. AFIȘARE REZULTAT
Pentru fiecare joc/pool:
- **Pool 1** (numere + scorul fiecăruia), **Pool 2** (dacă există).
- **⭐ OMNIUS Pool 1** și **⭐ OMNIUS Pool 2** (biletul de `draw_n`).
- **Variante simple** (biletele din wheeling) + câte sunt + acoperirea garanției.
- Top 5-10 numere după scor, cu explicație scurtă de ce (gap mare? hot? trend?).
- ⚠️ Reaminte: loteria e aleatoare; sistemul optimizează acoperirea, nu prezice.

NOTĂ: versiunea completă (pe PC, cu GPU) folosește și ~90 modele neurale (informer,
chronos, TimesFM etc.) ca scorer alternativ. Aici, fără GPU, folosim DOAR Smart Logic
Hybrid v2 de mai sus — diferența practică e mică (loteria fiind aleatoare), dar
rezultatele nu vor fi identice cu app-ul complet.

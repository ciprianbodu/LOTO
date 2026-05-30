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
- `avg_gap` = media diferențelor dintre apariții consecutive (interval mediu).
- `current_gap` = N − index_ultima_apariție.
- dacă `avg_gap == 0` → scor 0.5; altfel `gap_ratio = current_gap/avg_gap`,
  **`scor = min(1.0, gap_ratio*0.7 + 0.3)`** (scalat în 0.3–1.0).

**b) Trend — pondere 0.15**
- `window = min(20, N//2)`.
- `recent_freq` = apariții în ultimele `window` extrageri.
- `older_freq` = apariții în cele `window` extrageri de DINAINTE (offset cu window).
- dacă `older_freq == 0` → scor `0.6` (dacă recent_freq>0) altfel `0.4`;
  altfel `trend_ratio = recent_freq/older_freq`, **`scor = min(1, max(0, (trend_ratio−0.5)*2))`**.

**c) Frequency — pondere 0.15** (min-max pe frecvențele nenule)
- `max_f`/`min_f` = max/min dintre frecvențele > 0.
- dacă `max_f == min_f` → 0.5; altfel **`scor = (freq(k) − min_f)/(max_f − min_f)`**.

**d) Positional — pondere 0.10** (consistență pozițională)
- `counts[p]` = de câte ori `k` a apărut pe poziția `p` (0..draw_n−1) în extrageri.
- `total = sum(counts)`; dacă 0 → 0.5.
- `probs = counts/total`; `var = varianța(probs)`; **`scor = max(0, 1 − var*draw_n)`**
  (poziții consistente = scor mare).

**e) Recent-Hits — pondere 0.30**
- `S = min(15, N)`, `L = min(50, N)`.
- `short_rate` = apariții în ultimele S / S; `long_rate` = apariții în ultimele L / L.
- **`scor = 0.65*min(1, short_rate*2.5) + 0.35*min(1, long_rate*3.0)`**.

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

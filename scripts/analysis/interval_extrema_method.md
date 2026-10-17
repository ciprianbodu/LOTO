# Interval adaptiv după minime/maxime

`interval_extrema_k16` este o metodă experimentală de benchmark, adăugată la
cererea utilizatorului după analiza intervalelor din 3 octombrie 2026.
Re-Bench o măsoară automat pentru Loto 6/49. Este exclusă din producție:
restrângerea la un interval este explicită, iar avantajul predictiv nu este
confirmat. Înregistrarea metodei nu schimbă scorurile metodelor existente.

## Formula fixată

1. Se consideră toate intervalele contigue cu cel puțin 16 numere.
2. În fiecare se formează top16 după frecvența simplă a ultimelor 50 de
   extrageri anterioare zilei-țintă, cu departajarea canonică.
3. Se măsoară rezultatele 4+ ale acestor reguli pe ultimele 300 de extrageri
   cunoscute, după un început de 200 de extrageri.
4. Minimul și maximul ultimei zile complete definesc patru stări față de
   medianele teoretice uniforme. Rezultatele din aceeași stare sunt combinate
   cu rata recentă generală folosind greutatea fixă 50.
5. Se alege intervalul cu rata estimată cea mai mare. Egalitățile între
   intervale preferă lățimea mai mare, apoi limita inferioară mai mică.
6. Scorurile prioritizează intervalul ales și păstrează frecvențele și
   egalitățile în interior. Scorurile nu reprezintă probabilități de extragere.

Formula este optimizată pentru **pool16 și ținta4+**. Ratele la alte pool-uri
sunt ratele prefixelor aceluiași clasament. Joker Urna2 și geometriile care
nu pot avea4+ produc scoruri plate; nu sunt evaluate automat cu această metodă.
Un istoric prea scurt produce tot scoruri plate, raportate ca ne-evaluate.
Fereastra100% poate fi parțială din cauza acestui început, fără fallback mascat.

## Cronologie și integrare

Registry: `loto_enterprise/benchmark/methods.py`, implementare:
`loto_enterprise/benchmark/methods_experimental.py`.
`call_method` primește opțional `history_cutoffs`, exclusiv pentru metodele
marcate. Runnerul transmite numai cutoff-urile prefixului deja cunoscut.
Aceeași regulă de excludere a zilei întregi se aplică în evaluarea internă.
Niciuna dintre cele două extrageri din aceeași zi nu o poate vedea pe cealaltă.

La un apel pe o matrice fără context de date, fiecare rând este un pas temporal.
Acesta nu reproduce automat un istoric cu două extrageri pe zi. Controlul
amestecat al benchmarkului folosește intenționat această convenție și are rol
diagnostic. Pentru reproducerea studiului se folosesc cutoff-urile datate și
`block_size=1`.

Scorerii existenți păstrează apelul cu două argumente. Caches benchmark includ
numele metodei și cutoff-urile; nu se invalidează global geometria biletelor.
Curarea include experimentul numai la `loto_6_49`; excluderea din producție
se aplică indiferent de numele salvat în decizie. UI explică statutul și ținta.

## Dovezi anterioare integrării

Protocolul studiului a fost fixat înaintea rularii din sesiune, dar istoricul
fusese disponibil și analizat anterior. Separarea a folosit primele70% din
zilele distincte pentru dezvoltare și restul pentru evaluare, fără separarea
extragerilor aceleiași zile.

| 6/49, pool16, 791 extrageri | 4+ | 5+ |
|---|---:|---:|
| Interval condiționat | 87 | 11 |
| Frecvență simplă50, univers complet | 60 | 7 |
| Așteptare uniformă exactă | 62,96 | 8,61 |

Perioada este 13 ianuarie2019–1 octombrie2026. Cele două jumătăți au42/395 și
45/396 la4+; aceasta este consistență internă, nu replicare externă.
P brut4+=0,0015559162; corecția Holm pentru51 de comparații dă0,0793517280,
peste0,05. Pentru5+, p brut=0,2476467. Aceeași regulă nu reproduce avantajul
la5/40 sau Joker. Comparația cu frecvența este descriptivă, fără test pereche.
Cele19 controale uniforme dau46–71 hituri4+, însă eșantionul mic de controale
nu constituie confirmare. Acestea sunt hituri de pool, fără limita de
consecutive a utilizatorului și fără evaluarea biletelor.

SHA256 istoric6/49:
`f55fde7e883ccc8b0100eb2f6525896cf5b1eb2044b4efe29791132a6163b967`.
Rezultatele cercetării originale rămân separat în
`scratch/intervals_2026-10-03/`, fără a suprascrie benchmarkul utilizatorului.
Metoda trebuie verificată cu formula fixată pe date noi înainte de orice
reevaluare a eligibilității pentru producție.

## Fidelitatea implementării integrate

Replay-ul complet prin noul scorer contextual reproduce exact toate cele
791 pool-uri și toate cele791 clasamente: 87 hituri4+ și11hituri5+, fără
neconcordanțe. Sunt756 zile/cutoff-uri distincte; cele35 reutilizări sunt
locale probei, scorerul nu păstrează cache sau stare globală.

În481/791 cazuri (60,81%), rangurile16 și17 au scoruri egale. Alegerea
ultimului loc folosește astfel departajarea canonică. Acest indicator depășește
pragul existent de50% pentru dependența de tie-break, pe perioada studiului.
Egalitățile sunt păstrate; nu s-a adăugat un termen artificial pentru a evita
poarta. Este o limită suplimentară față de lipsa confirmării statistice.

Latența măsurată local: mediană98,53ms/apel, p95=128,73ms; replay77,08s.
Aceste valori descriu acest calculator și aceste date, nu sunt garanții de
viteză. Dovada este `scratch/interval_method_2026-10-03/fidelity.json`.

## Verificarea integrării

Python3.14.7 / Windows, copie izolată: **2038 teste trecute, 17 omise, zero
eșecuri**. Cele272 avertismente NumPy provin din testele existente.
Benchmark real în procese separate:32/32 ținte evaluate de noua metodă și de
martorul F50, fără erori. Inventar renumărat:53 metode în registry; matricea
Re-Bench cuprinde53 la6/49 și52 la fiecare dintre celelalte trei jocuri.

Pentru utilizator: pornire normală a aplicației, apoi **Re-Bench** pentru
România. Metoda apare în rezultate cu eticheta experimentală. Nu este nevoie
de o variabilă de mediu. Decizia de producție rămâne protejată de excludere.

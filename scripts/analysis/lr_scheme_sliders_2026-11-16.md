# Glisoare pentru schemele reduse oficiale LR

## Alegerea din interfață

Cele trei glisoare din România parcurg coduri oficiale, în ordinea
numărului de numere, apoi a costului. Catalogul are 33 de scheme: șapte
la 6/49, zece la Joker și șaisprezece la 5/40, în acord cu imaginile
furnizate de utilizator. Poolurile fără cod nu sunt poziții ale glisorului.

Două coduri cu același pool au poziții distincte. De exemplu, la 6/49:

| Pool | Cod | Variante | Cost în variante |
|---:|---:|---:|---:|
| 10 | 49 | 15 | 120 lei |
| 10 | 50 | 30 | 240 lei |

Helperul de lângă glisor actualizează imediat codul, numărul de numere,
variantele, costul și garanțiile condiționate. Tarifele standard sunt
8 lei/variantă la 6/49, 7 lei la Joker și 5 lei la 5/40. Prețul biletului
este 0,50 lei și se adaugă separat; nu se presupune un număr necunoscut de
bilete fizice și nici un comision al platformei online. Tarifele standard
au fost confruntate la 16.11.2026 cu [pagina oficială de prețuri](https://www.loto.ro/?page_id=1068).
Tragerile speciale pot avea alt tarif.

La pornire rămân schemele confirmate: 6/49 Cod 48 (9 numere, 12 variante),
5/40 Cod 16 (8 numere, 21 variante), Joker Cod 24 (10 numere, 14 variante).
Glisorul păstrează codul ales explicit, inclusiv un cod mai scump pe același
pool. O cerere veche care specifică numai poolul folosește automat schema
cu cele mai puține variante, conform preferinței utilizatorului.

Controalele generice de garanție, condiție și plafon nu mai modifică
generarea românească: un cod LR are o desfășurare fixă. Aceste controale
rămân disponibile pentru jocurile altor țări. Nu s-a adăugat un buton separat.

## Generarea și istoricul

Configurarea jobului românesc transmite explicit `lr_scheme_code`.
Motorul selectează numerele prin metoda existentă pentru joc și pool, apoi
folosește numai rândurile oficiale ale codului, pe poolul sortat numeric,
în ordinea terminalului. Codul, poolul și numărul de variante sunt validate;
nu există trunchiere, completare sau fallback la un wheel generic.
Joker păstrează bila aleasă de mecanismul existent al urnei a doua.

Un interval de candidați prea scurt pentru poolul oficial produce o eroare
explicată, nu o schemă redimensionată. Un cod LR pe un joc străin sau în
modul de referință completă virtuală este refuzat. Joburile legacy fără
flag își păstrează contractul general.

Rezultatul, raportul, premiile și fiecare pas din simularea fizică păstrează
codul și identitatea desfășurării. Mutarea glisorului după submit nu schimbă
schema jobului deja pornit. De exemplu, un rezultat Cod 50 pe zece numere
nu este reinterpretat în istoric drept Cod 49, deși acela este mai ieftin.

Simularea sistemului generat păstrează codul LR. Referința separată pentru
un sistem complet virtual omite flagul și este etichetată ca referință.
Cache-urile worker/WF includ codul și hashul geometriei și al rândurilor
oficiale, astfel încât două coduri pe același pool nu împart rezultatele.
Nu se șterg cache-uri globale sau istorice de producție.

## Garanții și limite

Garanția este tabelul condiționat al schemei, nu valoarea generică folosită
de vechiul constructor. De exemplu, la Cod 49, patru numere în pool duc
la patru pe o variantă în 92,8571% dintre cazuri; nu reprezintă o garanție
sigură de patru dacă patru. Metrica auxiliară de acoperire nu este prezentată
drept garanție oficială.

O creștere de pool poate reduce unele praguri când se alege un cod mai
ieftin. La 6/49, trecerea de la Cod 56 (11 numere, 66 variante) la Cod 57
(12 numere, 22 variante) reduce probabilitățile 5+/6; la 5/40, Cod 28→35
reduce ușor 5+. Helperul indică pierderile de probabilitate sau de garanție
la schimbarea codului. Desfășurările și scorerii nu sunt modificați și nu
se pretinde un avantaj predictiv sau o creștere garantată a hiturilor.

## Validare

Verificarea independentă a selectorului acoperă toate cele 33 de coduri și
hashuri, 26 de perechi joc/pool, 104 cereri cu plafon suficient, 134 de
refuzuri corecte, maparea numerică și metadatele față de numărători întregi.
Toate codurile explicite sunt păstrate; selecția automată după pool alege
costul minim, chiar dacă un cod mai scump este disponibil.

Validare finală: **7.312 teste trecute, 25 omise, zero eșecuri/erori, 220 fișiere, Python 3.15.2**. Cele trei grupuri au rulat
pe aceleași 11 surse finale, în copii/runtime-uri/cozi/cache-uri izolate.
Fixture-urile vechi care cereau universurile complete în UI sau construiau
joburi legacy din noua configurație LR au fost adaptate; aserțiunile de
paritate legacy și refuzul LR pe o țară străină sunt păstrate.

Toate cele 33 de coduri au fost confruntate cu rândurile oficiale.
ProcessPool Windows cu două procese a produs rezultate identice cu
execuția serială pentru Cod 49 și 50. Workerul real a păstrat B15/120 lei
și B30/240 lei, chiar dacă sidebarul era mutat la celălalt cod.
Configurația normală a încheiat toate trei jocurile cu codurile 48/16/24
și exact 12/21/14 variante oficiale. Proba WF→store→randare a evaluat
câte șase extrageri pentru Cod 49 și 50, cu profilul oficial corect.

UI HTTP 200 și verificare interactivă în Edge: 49→50 actualizează helperul
120→240 lei; Joker 24→23, 98→357 lei; 5/40 16→17, 105→150 lei.
Captura de verificare este păstrată în workspace. Serverele și workerii
probelor sunt închiși; SMTP și oprirea PC-ului au fost dezactivate.
Cele 134 de fișiere urmărite în proba E2E au rămas identice, iar la
aplicarea surselor au fost verificate 667 de fișiere existente neatinse.
Testele noi și adaptările rămân locale, conform instrucțiunilor proiectului.

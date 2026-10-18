# Auditul workflow-ului Claude: wheeling — 18 octombrie 2026

## Verdict

Metodele noi `weighted` și `multihit` nu justifică schimbarea selecției automate. La același pool de 16 numere și același buget, ambele scad șansa exactă de 4+ în toate cele 9 configurații testate: trei jocuri, bugete de 10, 30 și 65 de variante. Niciuna dintre cele 18 comparații nu trece certificarea de dominanță față de metoda automată existentă. Scorurile sunt sintetice, descrescătoare; acesta este un test de geometrie, fără concluzii predictive.

Designul început de Claude, `covering_designs/budget/H_11_5_4.txt`, aduce însă un câștig certificat la pool 11, garanție 4, plafon 65: șansa de 4+ crește cu 12,51% relativ la 5/40 și cu 4,63% la Joker, pe clasamentul sintetic descrescător, fără scădere la alt prag și fără cost suplimentar. Cele șase comparații pe scoruri descrescătoare, plate și mixte trec certificarea existentă a motorului.

## Comparația la cost egal

P(cel puțin 4 numere pe cel puțin o variantă), în procente. Sunt numărate exact toate intersecțiile pool–extragere, cu ponderile combinatorice ale numerelor din afara pool-ului. La 5/40 se folosesc toate cele șase numere extrase; Joker se referă numai la Urna 1.

| Joc | Variante | Automat | Weighted | Multihit |
|---|---:|---:|---:|---:|
| 6/49 | 10 | 0,977344 | 0,880675 | 0,874847 |
| 6/49 | 30 | 2,590108 | 2,309877 | 2,246247 |
| 6/49 | 65 | 4,707406 | 4,203352 | 3,951461 |
| 5/40 | 10 | 0,782075 | 0,720434 | 0,668772 |
| 5/40 | 30 | 2,297297 | 1,937614 | 1,763791 |
| 5/40 | 65 | 4,514040 | 3,781074 | 3,437414 |
| Joker Urna 1 | 10 | 0,164517 | 0,158624 | 0,151585 |
| Joker Urna 1 | 30 | 0,493551 | 0,454181 | 0,424470 |
| Joker Urna 1 | 65 | 1,029090 | 0,939301 | 0,859089 |

Fără plafon, `weighted` doar permută pozițiile unui design complet: probabilitățile uniforme rămân exact egale cu cele automate în cele trei configurații. Mai multe apariții ale numerelor cu scor mare nu constituie un avantaj demonstrat; scorurile nu sunt probabilități calibrate.

`multihit` fără plafon asigură două variante distincte pentru fiecare grupă de patru, dar cumpără 335 de variante față de 152 la 6/49 și 844 față de 404 la 5/40/Joker. Aceste rezultate nu sunt comparații la cost egal. Multiplicitatea și șansa de cel puțin un câștig sunt obiective diferite.

## Designul H(11,5,4), buget 65

| Joc, scor descrescător | Acoperire înainte | Acoperire după | P(4+) înainte | P(4+) după | P(5+) înainte | P(5+) după |
|---|---:|---:|---:|---:|---:|---:|
| 5/40 | 86,36% | 98,48% | 3,375643% | 3,797982% | 0,058228% | 0,059270% |
| Joker Urna 1 | 93,94% | 98,48% | 0,900505% | 0,942166% | 0,005320% | 0,005320% |

Baseline-ul este motorul automat cu directoarele designurilor H dezactivate numai în procesul auditului. Producția și scorurile reale nu sunt rescrise. Certificarea verifică același număr de variante distincte, păstrarea numerelor jucate, acoperire nedescrescătoare și probabilități nedescrescătoare la toate pragurile 3..5, cu un câștig strict.

Certificat independent de generator: 65 de blocuri distincte, fiecare cu cinci poziții valide; cele 325 de grupe de patru sunt toate distincte. Este maximul de 325 = 65 × C(5,4), din totalul de 330. Acoperirea rămâne parțială, 98,48%, și nu trebuie prezentată drept garanție completă.

Toate cele 165 de triple sunt acoperite, deci P(3+) atinge plafonul pool-ului. Nicio pereche de variante nu are patru numere comune, astfel evenimentele 5+ sunt disjuncte pentru extrageri de cinci sau șase numere: P(5+) atinge plafonul reuniunii la 65 de variante. La pragul 4, designul acoperă 461 din cele 462 de submulțimi de cinci și toate submulțimile de șase. Optimalitatea globală a P(4+) nu este demonstrată aici.

## Reparațiile modulelor noi

- Ponderile acceptă scoruri lipsă, nevalide, NaN și infinit. Valorile finite extreme ±10^308 sunt normalizate fără depășirea intervalului numeric: scorul central primește corect ponderea 2.
- Multiplicitatea imposibilă cu variante distincte este refuzată explicit fără plafon. De exemplu, când garanția este egală cu lungimea variantei, aceeași grupă poate fi conținută într-o singură variantă distinctă.
- Statistica multiplicității nu dublează variantele identice și refuză numerele repetate sau din afara pool-ului. Pool-urile duplicate și geometriile nevalide sunt refuzate.
- Alegerea multihit acordă prioritate strictă acoperirii primare, apoi celei duble și următoarelor straturi. Verificarea apartenenței variantelor folosește un set; oprirea arbitrară după 2.000 de pași a fost eliminată.
- Acoperirea finală este recalculată din variantele livrate după completarea numerelor pool-ului. Aceasta este o protecție de contract; auditul nu a găsit o discrepanță reală de procent în cele 400 de geometrii aleatoare încercate înaintea modificării.
- Afirmația inițială că ponderarea crește substanțial rata de hit a fost înlocuită cu descrierea exactă a obiectivului experimental.

## Reproducere și verificare

`python scripts/analysis/audit_claude_wheeling_2026_10_18.py --output scripts/analysis/claude_wheeling_2026-10-18.json`

JSON-ul conține toate pragurile, statisticile de multiplicitate, certificările, amprenta designului, timpii primei generări și mediana a trei apeluri ulterioare. Timpii au fost măsurați pe Python 3.14.8 / Windows, în timp ce rula și suita generală: sunt timpi locali ai generatorului, influențați de cache și concurență, fără afirmație de accelerare a întregii aplicații.

Cele 46 de teste dedicate `test_multihit_wheeling.py` și `test_weighted_wheeling.py` trec în 8,52 s. Compilarea modulelor și a scriptului trece; `git diff --check` nu raportează erori. Validarea generală a aplicației este consemnată în raportul auditului principal.

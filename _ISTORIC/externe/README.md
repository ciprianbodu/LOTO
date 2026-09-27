# Istorice externe (Uniunea Europeană)

Extrageri ale loteriilor din alte țări ale UE, pentru selectorul de țară și
pentru analize. Istoricul românesc rămâne în `_ISTORIC/`; fișierele de aici nu
sunt citite de benchmark-ul românesc (`discover_games` nu intră în subfoldere).

## Format

Identic cu istoricul românesc: antet `date,n1,n2,n3,n4,n5,n6` (EuroMillions:
`date,n1,n2,n3,n4,n5,s1,s2`), data ZZ-LL-AAAA,
ordine cronologică, un rând pe extragere, terminații LF. Se păstrează numai
numerele principale; numerele bonus (Superzahl, Zusatzzahl, complementario,
reintegro) nu intră. Când în aceeași zi au existat două extrageri principale
separate, sunt două rânduri cu aceeași dată, în ordinea oficială; aplicația le
tratează ca extrageri simultane (niciuna nu intră în istoricul celeilalte).

Fișierele se verifică la fiecare rulare a testelor (`test_externe_history.py`):
format, valori, ordine, fără rânduri repetate.

## Fișiere

### `germania_lotto_6aus49.csv`: Germania, Lotto 6aus49

- 6.564 de extrageri, 09.10.1955 – 26.09.2026 (la import).
- Sursa: portalul oficial lotto.de (DLTB), extragere cu extragere.
- Verificare: toate cele 6.564 de extrageri comparate cu WestLotto (loteria
  oficială a landului NRW), zero diferențe; a treia sursă, Lotto
  Rheinland-Pfalz, are aceleași numere.
- 757 de zile cu două rânduri: Mittwochslotto A și B, 04.06.1986 – 29.11.2000.
- Excluse: extragerile de miercuri 1982–1986, care erau „7 aus 38”.
- Între 1955 și 1965 extragerile erau duminica, nu sâmbăta.
- Ordinea numerelor pe rând este cea din sursa oficială: de regulă ordinea
  extragerii, iar pentru circa 15% din rânduri ordine crescătoare.

### `polonia_lotto_6din49.csv`: Polonia, Lotto (fost Duży Lotek)

- 7.410 extrageri, 27.01.1957 – 26.09.2026 (la import).
- Surse: wynikilotto.net.pl (fișier complet, numerotat 1..7410), megalotto.pl,
  multipasko.pl. Arhiva oficială lotto.pl blochează descărcarea automată.
- Verificare: 928 de extrageri comparate cu wynikilotto.com.pl și lotteryguru,
  zero diferențe după o singură corecție (1976-04-25, al șaselea număr 35, nu
  34, după majoritatea a cinci surse independente). Alte șase extrageri vechi
  (1968–2006), la care sursele nu concordă, au valoarea majorității: #622, #1153,
  #1730, #2164, #2231, #2384 și data extragerii #4323 (10.09.2006).
- 956 de zile cu două rânduri: perioada cu două extrageri pe zi, 1965–1991.
- Numere în ordine crescătoare.

### `spania_la_primitiva_6din49.csv`: Spania, La Primitiva

- 4.194 extrageri, 17.10.1985 – 26.09.2026 (la import).
- Surse: lotoideas.com, lawebdelaprimitiva.com, laprimitiva.info, elgordo.com.
  Un rând intră numai dacă cel puțin trei surse dau aceeași dată și aceleași
  numere (4.186 cu toate patru, 8 cu trei). Site-ul oficial loteriasyapuestas.es
  blochează descărcarea automată.
- Verificare: comparat integral cu o a doua sursă; cele două diferențe găsite
  erau greșeli ale acelei surse, confirmate de a treia.
- Pauza din pandemie: nicio extragere între 14.03.2020 și 21.05.2020.
- Numere în ordine crescătoare.

### `euromillions_5din50_stele.csv`: EuroMillions (doar antrenament)

- 1.984 de extrageri, 13.02.2004 – 25.09.2026 (la import); 5 numere din 50 și 2
  stele (`s1`, `s2`, crescător). Nu e un joc 6/49.
- Sursa: arhiva oficială Française des Jeux (fdj.fr), șase fișiere care se leagă
  cap la cap; numerele principale în ordinea extragerii.
- Verificare: toate cele 1.984 de extrageri comparate cu euro-millions.com și
  lottery.co.uk, iar ultimele 52 cu fișierul oficial al Loteriei Naționale
  britanice: zero diferențe.
- Stelele au avut alt interval: 1–9 până la 06.05.2011, 1–11 până la 23.09.2016,
  1–12 de la 27.09.2016. Un model pe stele trebuie să țină cont de epocă.
- Nu se poate juca de pe site-urile oficiale din România (toți operatorii cer
  domiciliu în țara lor): se păstrează ca istoric de antrenament.

### `austria_lotto_6aus45.csv`: Austria, Lotto 6 aus 45

- 3.685 de extrageri, 07.09.1986 – 23.09.2026 (la import), din fișierele
  oficiale win2day.at; include extragerile bonus de vineri (din 2020), care fac
  parte din aceeași numerotare oficială. Numere în ordine crescătoare.
- Verificare: 814 extrageri comparate cu trei surse independente (6richtige.at,
  theblueye.com, gewinnabfrage.at), zero diferențe.

### `belgia_lotto_6din45.csv`: Belgia, Lotto 6/45

- 1.565 de extrageri, 01.10.2011 – 26.09.2026 (la import), numai perioada 6/45
  (înainte jocul era 6/42), din API-ul oficial Loterie Nationale.
- Verificare: 1.553 de extrageri comparate cu o sursă independentă, zero diferențe.

### `ungaria_hatoslotto_6din45.csv`: Ungaria, Hatoslottó

- 1.850 de extrageri, 29.10.1988 – 24.09.2026 (la import), din fișierul oficial
  Szerencsejáték.
- Verificare: 1.763 de extrageri comparate cu o sursă independentă, zero diferențe.

### `cehia_sportka_6din49.csv`: Cehia, Sportka

- 7.620 de rânduri, 09.01.1994 – 25.09.2026 (la import), din arhiva oficială
  Sazka. Fiecare zi de extragere are două trageri (1. tah și 2. tah), păstrate
  ca două rânduri cu aceeași dată, în ordinea oficială.
- Verificare: 684 de trageri comparate cu o sursă independentă, zero diferențe.

### `slovacia_loto_6din49.csv`: Slovacia, Loto

- 5.164 de rânduri, 02.10.2002 – 25.09.2026 (la import), din arhiva oficială TIPOS.
- Verificare: 450 de extrageri comparate cu o sursă independentă, zero diferențe.

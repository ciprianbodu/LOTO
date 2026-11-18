# Rata de hituri la același buget pe schemele reduse

## Ce se poate îmbunătăți prin acoperire

La Loto 6/49, un bilet cu 66 de variante poate acoperi de trei ori mai multe extrageri cu 4+ pe cel puțin o variantă decât Cod 56. Cele trei zone conțin câte un Cod 57, fiecare pe alte 12 numere. Calculul presupune o extragere uniformă, un singur concurs și tariful standard. Aceeași organizare încape pe un singur bilet. [Tipurile și numărul de scheme admise pe biletul 6/49](https://www.loto.ro/loto-new/newLotoSiteNexioFinalVersion/web/app2.php/jocuri/649_si_noroc/cum_se_joaca/Sumarul_posibilitatilor_de_joc.html) · [Tarifele Loteriei Române](https://www.loto.ro/?page_id=1068)

| Joc și configurație | Variante | Total cu taxa | Orice premiu la 6/49 (3+) | 4+ pe variantă | 5+ pe variantă |
|---|---:|---:|---:|---:|---:|
| 6/49 · Cod 50 | 30 | 240,50 lei | 9,027064% | 1,184569% | 0,051703% |
| 6/49 · două Cod 49 pe pooluri disjuncte de câte 10 | 30 | 240,50 lei | 17,951152% | 2,210169% | 0,052990% |
| 6/49 · Cod 56 | 66 | 528,50 lei | 11,741773% | 1,787838% | 0,110914% |
| 6/49 · trei Cod 57 pe pooluri disjuncte de câte 12 | 66 | 528,50 lei | 43,355076% | 5,363515% | 0,122241% |

Pentru bugetul de 240,50 lei, 4+ trece de la 165.648 la 309.066 extrageri favorabile din 13.983.816, iar 5+ trece de la 7.230 la 7.410. Pentru bugetul de 528,50 lei, numărătorile trec de la 250.008 la 750.024 pentru 4+ și de la 15.510 la 17.094 pentru 5+. La 6/6 contează numărul variantelor distincte, respectiv 30 sau 66 în fiecare comparație. Distribuția premiilor în bani depinde de categoria obținută. Garanțiile condiționate de numerele prinse în poolul codului inițial se schimbă când variantele sunt împărțite pe alte pooluri.

Am verificat toate cele 33 de coduri oficiale românești, la bugetul fiecărui cod și la două praguri. În **32 dintre cele 66 de comparații** există o configurație cu șansă strict mai mare la pragul urmărit și cost total cel mult egal. În **48 din 66** construcția atinge maximul dintre toate combinațiile accesibile de scheme oficiale complete. Dintre cele 32 cu o creștere, 15 ating și maximul. În celelalte 18 comparații limita superioară și cea mai bună configurație construită diferă. Calculatorul le afișează separat.

La bugetul implicit al **Codului 48 la 6/49** și al **Codului 24 la Joker**, codul individual atinge deja maximul pentru 4+ și 5+ pe numerele principale. La **5/40**, două Cod 15 pe pooluri disjuncte costă 90,50 lei față de 105,50 lei pentru Cod 16. Numărul extragerilor favorabile la 4+ urcă de la 36.540 la 38.360 din 3.838.380, iar la 5+ scade de la 700 la 608. Pentru ținta 5+, Cod 16 atinge maximul la acel buget. La 5/40 sunt luate în calcul toate cele șase numere extrase. La Joker, măsura din comparator privește exclusiv urna principală. [Broșura oficială 5/40](https://www.loto.ro/images/upload/pages/Brosura_Loto_5_din_40.pdf) · [Broșura oficială Joker](https://www.loto.ro/images/upload/pages/brosura-joker1.pdf)

## Cum se verifică maximul

Pentru fiecare cod numărăm exact extragerile în care cel puțin una dintre variante atinge pragul. Pentru orice combinație de coduri, suma acestor numărători este o limită superioară pentru reuniune. Un calcul dinamic maximizează suma peste toate combinațiile de coduri întregi, cu repetiții permise, în limita prețului variantelor și a taxei de 0,50 lei pe bilet fizic.

Calculăm separat o configurație realizabilă. Dacă două pooluri au mai puțin de `2 × prag − numărul de bile extrase` numere în comun, o extragere nu poate atinge pragul în ambele. În acest caz numărătorile se adună exact. Când suma coincide cu limita globală, nicio altă combinație de coduri LR complete din acel buget, indiferent de suprapunerea numerelor, nu o poate depăși. Pentru 3+ la două pooluri disjuncte într-un joc cu șase bile, scădem din sumă cazurile cu exact trei numere acoperite în fiecare pool. Un al treilea eveniment simultan ar cere nouă bile. Oracolul independent a verificat 391 de rezultate, inclusiv pragurile secundare raportate.

## Verificare pe extrageri istorice

Am refăcut aceleași patru sisteme 6/49 pe **686 de extrageri din 655 de zile**, între 27.02.2020 și 15.11.2026. Scorerul `frequency` din registru pondera frecvențele după vechime și vedea numai zile anterioare extragerii. Fiecare zonă primea numerele cel mai bine clasate dintre cele încă disponibile, cu cel mult două consecutive în fiecare zonă.

| Sistem | Extrageri cu 3+ | Cu 4+ | Cu 5+ | Cu 6/6 |
|---|---:|---:|---:|---:|
| Cod 50 | 70 | 10 | 0 | 0 |
| Două Cod 49 | 147 | 23 | 0 | 0 |
| Cod 56 | 83 | 17 | 1 | 0 |
| Trei Cod 57 | 313 | 45 | 1 | 0 |

Acestea sunt numărători din arhivă. Probabilitățile din primul tabel provin din toate extragerile posibile. De exemplu, cele 686 de extrageri au dat câte un singur rezultat de 5+ la ambele configurații cu 66 de variante, deși numărătorile combinatorice diferă.

## Alte reguli de predicție testate

Pe 13 jocuri și 12.339 de extrageri am testat două criterii noi de alegere între nouă metode. Un criteriu selectează după 4+ pe variantă, apoi după 3+. Al doilea acordă credit și unei extrageri istorice aflate la o singură înlocuire de bilă de un rezultat de 5+. Înlocuirile sunt folosite numai pentru selecție. Calculul final numără exclusiv extragerile reale din următoarea perioadă. Metoda se realege la fiecare 26 de zile de extragere din trecutul deja disponibil. În România se aplică rândurile oficiale 48/24/16, iar în străinătate se simulează un cod cu 10 numere la același cost pentru toate regulile jocului respectiv. Urna 2 are o singură bilă, iar vecinătatea cu o înlocuire cuprinde întregul univers, oferind aceeași informație pentru orice alegere.

| Criteriul de alegere | Toate jocurile, 4+ / 5+ | România, 4+ / 5+ (1.681 extrageri) |
|---|---:|---:|
| Metoda fixă `frequency` | 114 / 6 | 12 / 1 |
| Selecție după 5+ | 136 / 4 | 8 / 0 |
| Selecție după 4+, apoi 3+ | 157 / 5 | 7 / 0 |
| Selecție după ratările la o bilă de 5+ | 135 / 7 | 7 / 0 |

La 5+, ultima regulă și frecvența de referință au câștigat pe extrageri diferite, șapte față de șase. La 4+, selecția după 4+/3+ a avut 147 de extrageri favorabile doar acesteia și 104 favorabile doar frecvenței de referință. Niciunul dintre cei doi candidați nu a depășit criteriul statistic stabilit pentru cele 52 de comparații principale (valoarea e maximă 1,702, prag 1.040). Cele 22 de apariții de 5+ însumate peste cele patru reguli au fost verificate direct în rândurile schemelor.

Am testat separat construcția poolului cu aceeași metodă `frequency` pentru toate regulile. Regula existentă preferă numerele în ordinea rangului și limitează seria de consecutive la două. O a doua regulă maximizează suma exactă a scorurilor cu aceeași limită, prin programare dinamică. A treia folosește direct primele K numere. O a patra alege între primele trei folosind exclusiv extragerile de dinaintea perioadei finale, apoi păstrează alegerea pe întregul test.

| Regula de construire a poolului | Toate jocurile, 4+ / 5+ pe variantă | România, 4+ / 5+ |
|---|---:|---:|
| Rang cu maximum două consecutive | 114 / 6 | 12 / 1 |
| Sumă maximă, maximum două consecutive | 112 / 6 | 12 / 1 |
| Primele K, fără limită | 107 / 3 | 13 / 0 |
| Regulă aleasă înaintea testului | 113 / 4 | 13 / 0 |

Pe datele tuturor jocurilor, maximizarea sumei a schimbat 118 pooluri, iar eliminarea limitei a schimbat 4.358. În România maximizarea sumei a dat exact aceleași pooluri pentru codurile testate. Nici această comparație cronologică pe arhive reutilizate nu justifică înlocuirea automată a regulii curente.

## Aplicație și reproducere

În pagina pentru România, panoul **Compară schemele LR la același buget** calculează configurarea pentru codul ales. Arată costul total, șansele pe variante și limita superioară. Generarea obișnuită și e-mailul folosesc în continuare codul ales în setările de generare. Glisorul 6/49 semnalează acum și scăderile la categoria IV. De exemplu, de la Cod 57 la Cod 56, șansa oricărui premiu scade de la 14,797806% la 11,741773%. Verificarea a acoperit toate cele 42 de tranziții direcționate între codurile 6/49. [Categoriile oficiale la 6/49](https://www.loto.ro/?p=3878)

```powershell
python -m scripts.analysis.lr_portfolio_bounds_2026_11_18 --output scratch/portofolii.json
python -m scripts.analysis.lr_portfolio_bounds_2026_11_18 --replay-649 --output scratch/portofolii_istoric.json
python -m scripts.analysis.lr_near_hits_2026_11_18 --output scratch/vecinatate.json
python -m scripts.analysis.lr_pool_rules_2026_11_18 --output scratch/reguli_pool.json
```

Pe versiunea finală s-au verificat 7.656 de teste distincte în 240 de fișiere. Cumulat, 7.631 au trecut și 25 au fost omise. Un test Windows care simulează expirarea timpului în lansatorul Git a eșuat la prima rulare paralelă și a trecut la reluarea izolată, fără schimbări. Cauza primei abateri nu a fost stabilită. Workerul a finalizat 13/13 jocuri. Integrarea a verificat 912 rezultate de scorer și 26 de fluxuri, iar catalogul 53.557 de comparații exacte, fără diferențe. Într-o instanță izolată deschisă în browser, Cod 56 a afișat trei Cod 57 la 528,50 lei, cu valorile din tabel. Toate cele 22 de fișiere de producție urmărite și-au păstrat amprentele.

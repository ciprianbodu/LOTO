# Audit LOTO și optimizarea hiturilor — 18.11.2026

Punct de plecare `dbbe04e`. Verificarea acoperă istoricele, selecția, coveringurile, cache-ul, workerul, afișarea, mailul, premiile și lansatorul. Probele care execută aplicația folosesc copii cu runtime, cache și coadă separate.

## Mai multe hituri la același număr de variante

Generatorul generic pentru geometria 6/49 aplică o căutare finală limitată după ce termină selecția dintre candidați. Constructorii interni folosesc traseul anterior. Verificarea numără exact, pentru fiecare număr posibil de bile din pool și fiecare prag, câte intersecții au cel puțin o variantă care atinge pragul. Înlocuirea se acceptă numai la același număr de variante, cu toate aceste numărători cel puțin egale și un câștig strict la 3+, 4+ sau 5+.

Se aplică pe pooluri 7–12, buget 2–64, garanție clasică și geometrie 6/49. Sistemele complete accesibile, cazurile cu candidat LR oficial la acel buget și traseul LR explicit sunt excluse. Semnătura `fp1` separă cache-ul workerului și al walk-forward-ului numai pentru traseul exterior eligibil. Sunt păstrate geometria Joker și 5/40, fișierele oficiale, scorarea și deciziile locale de benchmark.

| Pool / garanție / variante | Câștig confirmat | Extragere uniformă, rezultate favorabile înainte → după |
| --- | --- | ---: |
| 10 / 6 / 32 | 5+ | 7.230 → 7.386 |
| 11 / 4 / 24 | 4+, 5+ | 219.779 → 220.482, 5.925 → 5.926 |
| 11 / 5 / 32 | 4+ | 238.760 → 242.275 |
| 11 / 5 / 64 | 5+ | 14.787 → 14.902 |
| 11 / 6 / 64 | 5+ | 14.787 → 14.902 |
| 12 / 4 / 24 | 4+, 5+ | 252.672 → 253.338, 6.100 → 6.104 |

Numitorul fiecărui prag este C(49,6) = 13.983.816. La 32 de variante, 5+ trece de la 0,051703% la 0,052818%, o creștere relativă de 2,16%. În acel caz, 3+, 4+ și 6 rămân identice. Același spor apare în două execuții ale workerului pe istoricul Sportka, cu și fără rafinarea finală, pentru același pool și aceleași 32 de variante. Pe cei cinci martori proba independentă a măsurat aproximativ 20–64 ms pentru căutarea rece și 0,10–0,33 ms la refolosirea geometriei. Screeningul a verificat 306 configurații și a găsit 23 de candidați exploratorii. Pe domeniul adoptat, comparația finală înainte/după a acoperit 144 de configurații și a confirmat cele șase câștiguri din tabel, fără pierderi de hituri sau garanție. Cele 2.880 de chei de cache verificate se împart în 134 revizuite și 2.746 identice.

### Schemele LR folosite în UI

La extrageri uniforme, o permutare a numerelor poolului păstrează șansele unei scheme date. Schimbarea scorerului pe baza aceluiași istoric nu aduce o dovadă nouă de avantaj. Reenumerarea catalogului a produs 186 de comparații la cost cel mult egal și nicio alternativă care să domine simultan pragurile urmărite. Pentru codurile implicite, comparația tuturor partițiilor în coduri oficiale întregi dă aceeași concluzie în limitele catalogului și ale bugetelor 12/21/14.

| Cod implicit și cost în variante | P(4+) | P(5+) | P(6) |
| --- | ---: | ---: | ---: |
| 6/49 Cod 48, 12 variante, 96 lei | 0,739454810% | 0,021195931% | 0,000085813% |
| 5/40 Cod 16, 21 variante, 105 lei | 0,951964110% | 0,018236860% | — |
| Joker Cod 24, 14 variante, 98 lei | 0,221156546% | 0,001145889% | — |

Tabelul măsoară cel puțin o variantă. La Joker valorile privesc urna principală, iar P(5+J) este 0,000057294%. La 5/40 sunt considerate toate cele șase bile extrase pentru pragurile de hit. Categoria I cere o verificare separată. Ratele benchmarkului privesc hiturile în pool.

## Remedieri verificate

- Două teste de finalizare walk-forward reproduceau eșecul cu starea reziduală Re-Bench introdusă în izolare. Fixture-urile pornesc explicit fără Re-Bench în curs, iar testele porții își păstrează scenariile. Originea exactă a stării din rulările din 17.11 rămâne nedovedită. Copia veche conține și un marker local Re-Bench provenit din producție. Copiile curente exclud markerii, configurarea mailului și runtime-ul live.
- Nota statistică redă p-ul brut pentru deciziile cu `correction=none` și p-ul ajustat pentru o decizie istorică Holm. Păstrează verdictul salvat și identifică metadatele lipsă. Testele de UI și mail verifică ambele ramuri.
- Parserul premiilor acceptă `REPORT *` drept report și păstrează nota oficială când asocierea este unică. Sumele cu punct zecimal și grupe de mii corecte sunt acceptate alături de formatul românesc. O lună pentru Joker și una pentru 5/40 se importă din nou în copia de test, câte nouă extrageri. Cazurile ambigue rămân respinse. Notele sunt opționale în cache și nu sunt convertite în premii bănești.
- UI și worker citesc cadrele JSON fără inferența automată a datelor. Paritatea exactă include 13 istorice și trei cadre sintetice, 47.493 de rânduri. Se folosește `dtype=False`, alternativa documentată la `convert_dates`. [Documentația pandas 3.1.1](https://pandas.pydata.org/docs/reference/api/pandas.read_json.html).
- Lansatorul păstrează stdout, stderr și codul de ieșire al UI în `ui_8000.log`, plus o copie a sesiunii anterioare. Păstrează și logul precedent de pornire. Proba cu un proces care scrie pe ambele fluxuri și iese cu 37 confirmă captura, propagarea codului și rotația la pornire.

## Validare și limite

- 277 de fișiere, 519 designuri, 33 de scheme LR și 53.557 de comparații exacte, fără diferențe.
- 13 istorice, 57 de metode, 912 probe de paritate benchmark–producție, 224 de comparații ale ordinii bilelor și 44 de rate validate prin oracol temporal. Verificarea integrată include 26 de fluxuri, workerul pe toate cele 13 jocuri și UI HTTP 200.
- În UI izolată, toate cele 47 de variante ale jobului 42 coincid în ordine cu workerul. Clasamentul afișează separat hiturile în pool și hiturile pe variante. Compilarea a 201 fișiere și verificarea celor șase dependențe obligatorii au trecut.
- Suita pe 227 fișiere în 12 grupe: **7.470 trecute, 25 omise**, după reluarea grupurilor afectate. Prima execuție a identificat două dependențe de fixture, reproduse separat. Stubul pentru Re-Bench oprit a fost mutat din fixture-ul comun în testul care îl necesită. Fixture-ul secvențial de walk-forward forțează execuția în procesul părinte, astfel încât testele compact/detaliat folosesc aceeași decizie. Consumatorii fixture-urilor și probele paralele explicite au trecut la reluare. Toate cele patru decizii active din copia testată au starea `fresh`.
- Rularea într-un singur proces, pornită pe o copie intermediară cu 225 de fișiere, a terminat cu 7.386 trecute, 25 omise și trei eșecuri. Două erau așteptări de cache anterioare sufixului `fp1`, al treilea era stubul Re-Bench din fixture-ul comun. Toate trei sunt acoperite de reluările pe sursele finale, inclusiv de bateria de 380 teste de covering/cache și de reluarea grupei de recuperare. Separat, 189 de teste ale fluxurilor afectate au trecut împreună într-un singur proces, pe copia finală. Cele două eșecuri de finalizare semnalate la 17.11 nu s-au repetat în rularea monolitică intermediară după izolarea fixture-urilor.

Sursele numerelor, benchmarkurile și celelalte fișiere protejate au fost citite fără rescriere. SMTP a fost blocat în probe, iar aplicația activă nu a fost repornită. Rezultatele de mai sus măsoară probabilități combinatorice și verificări de implementare. Niciun scorer nou nu a fost promovat în producție.

Probe locale în `scratch/audit_2026_11_18/`: `designs.json`, `integration.json`, `covering/final_swap_independent.json`, `selection/selection_parity_2026_11_18.json`, `selection/lr_exact/`, `launcher_log_probe.json`, `worker_gain.json`, `verification_final.json`, `monolithic.log` și `target_single_process.log`.

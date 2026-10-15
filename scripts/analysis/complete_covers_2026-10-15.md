# Covere complete mai mici — 15 octombrie 2026

## Rezultat

Au fost comparate toate cele **52 de covere clasice locale** cu datele oficiale
La Jolla din [Zenodo 19735294](https://zenodo.org/records/19735294).
**12** au un design mai mic, cu aceeași garanție de 100%; celelalte 40 rămân
neschimbate. Validarea a enumerat toate submulțimile garanției și a verificat
dimensiunea, domeniul și unicitatea fiecărui bloc. Nu afirmăm optimalitatea.

Comparația pornește de la commitul `1d7ee026778cbb0f758ed299f8d4bb2c2fe4577b`.
Numerele de mai jos sunt cele ale fișierelor complete, înainte de etapele
suplimentare ale motorului. Variantele sunt combinații individuale, nu bilete
fizice cu mai multe variante.

| Design | Variante înainte | Variante după | Reducere |
|---|---:|---:|---:|
| C(11,6,5) | 103 | 100 | 2.91% |
| C(13,6,5) | 287 | 245 | 14.63% |
| C(14,5,4) | 231 | 229 | 0.87% |
| C(14,6,4) | 86 | 80 | 6.98% |
| C(14,6,5) | 457 | 371 | 18.82% |
| C(15,5,3) | 56 | 55 | 1.79% |
| C(15,5,4) | 303 | 294 | 2.97% |
| C(15,6,4) | 130 | 117 | 10.00% |
| C(15,6,5) | 689 | 578 | 16.11% |
| C(16,5,4) | 416 | 404 | 2.88% |
| C(16,6,4) | 172 | 152 | 11.63% |
| C(16,6,5) | 986 | 808 | 18.05% |

## Ce înseamnă pentru hituri

La acoperire completă, probabilitatea de a avea cel puțin `g` numere pe un
bilet este exact probabilitatea ca pool-ul să conțină cel puțin `g` numere
extrase. Aceasta rămâne identică atunci când toate submulțimile de ordin `g`
sunt acoperite. Avantajul acestor designuri este **costul mai mic pentru
aceeași garanție**. Nu îmbunătățesc selecția pool-ului.

Cumpărând mai puține variante, șansele pragurilor deasupra garanției pot
scădea. Măsurare exactă a fișierelor brute pe extrageri uniforme, cu
`covering.probability.wheel_hit_probabilities` (toate suprapunerile incluse):

| Joc, pool 16, garanție 4 | Variante | P(4+), înainte = după | P(5+) înainte | P(5+) după |
|---|---:|---:|---:|---:|
| 6/49 | 172 → 152 | 7.960002% | 0.292188% | 0.251434% |
| 5/40, șase numere extrase | 416 → 404 | 16.026553% | 0.372292% | 0.363122% |
| Joker Urna 1 | 416 → 404 | 4.677518% | 0.034049% | 0.033067% |

La 6/49, pool 16 și garanție 5, trecerea **986 → 808** păstrează P(5+)
la **1,088058%**. P(6) scade de la 0,007051% la 0,005778%, proporțional
cu numărul de variante distincte. La garanția 3, singurul design schimbat
este C(15,5,3): 56 → 55; P(4+) scade de la 3,848499% la 3,811973% la
5/40 și de la 0,878324% la 0,867847% la Joker. Aceste scăderi nu sunt
prezentate drept îmbunătățiri ale hiturilor.

## Proveniență și reproducere

Sursa: Daniel M. Gordon și contributorii numiți în metadate, La Jolla
Coverings Repository, DOI [10.5281/zenodo.19735294](https://doi.org/10.5281/zenodo.19735294),
publicat 24 aprilie 2026, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
C(16,6,4) este construcția simetrică Nurmela–Östergård din 1997;
C(16,5,4), cu 404 blocuri, este atribuit lui Mathias Liesener, 7 aprilie 2024.

Manifestul `ljcr_designs_2026_10_15.json` păstrează geometria, autorii,
numărul blocurilor, poziția exactă a fiecărei secțiuni din arhivă, SHA-256
al secțiunii, SHA-256 al fișierului normalizat și SHA-256 al fișierului vechi.
Amprentele designurilor folosesc text ASCII cu linii LF, inclusiv pentru
fișierul vechi (identic cu `git show` al commitului indicat). CRLF-ul local
al checkout-ului Windows nu schimbă aceste amprente; secțiunile arhivei
se verifică pe octeții originali.
Sunt necesari numai **60.848 de octeți** din `covers.json`; nu se descarcă
întreaga arhivă. MD5-ul întregii arhive este metadată de identificare a
sursei, nu o verificare pretinsă asupra octeților care nu au fost descărcați.

```powershell
python scripts/analysis/import_ljcr_designs.py --download
python scripts/analysis/import_ljcr_designs.py --download --save-sections scratch/ljcr-sections --output scratch/ljcr-designs
python scripts/analysis/import_ljcr_designs.py --sections-dir scratch/ljcr-sections --output scratch/ljcr-replayed
```

Ambele căi (descărcare directă și reluare offline) au fost executate:
12/12 designuri au aceleași amprente și 100% acoperire. Utilitarul nu
suprascrie fișiere existente cu alt conținut și nu se apelează la runtime.

Măsurătorile de mai sus sunt independente de istoric și de scoreri.
Pentru comparația brută a probabilităților, fișierele vechi se citesc din
commitul indicat, iar cele noi din manifest; pentru fiecare geometrie se
apelează `wheel_hit_probabilities(range(1, v + 1), blocks, draw_n, max_num)`
cu (6,49), (6,40) sau (5,45).

Normalizarea a sortat numerele în interiorul blocurilor și a convertit JSON
la linii separate prin spații. Ordinea blocurilor și designul matematic au
fost păstrate.

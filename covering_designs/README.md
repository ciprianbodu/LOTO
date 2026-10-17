# Designuri de acoperire locale

Fișierele `C_v_k_t.txt` conțin blocuri de `k` poziții din `1..v`; fiecare
`t`-submulțime trebuie să apară în cel puțin un bloc. Fișierele `L_v_k_p_t.txt`
au garanția diferită „t dacă p”. Directorul `budget/` conține designuri cu
număr limitat de variante, care pot avea acoperire incompletă.

## Importul La Jolla din 15 octombrie 2026

Cele 12 fișiere clasice enumerate în
[manifestul importului](../scripts/analysis/ljcr_designs_2026_10_15.json)
provin din **Daniel M. Gordon și contributorii La Jolla Coverings Repository**,
[înregistrarea Zenodo 19735294](https://zenodo.org/records/19735294), publicată
la 24 aprilie 2026, DOI [10.5281/zenodo.19735294](https://doi.org/10.5281/zenodo.19735294).
Licența datelor importate este
[Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/).
Autorii/contributorii, metodele și datele înscrise în metadatele sursei sunt
păstrate pentru fiecare design în manifest; unde sursa nu numește autorul,
nu atribuim unul. Această atribuire privește cele 12 fișiere, nu schimbă
proveniența celorlalte fișiere ale aplicației.

Modificări de format: selectarea celor 12 secțiuni JSON, ordonarea numerelor
în fiecare bloc și conversia la un bloc pe linie, cu spații între numere.
Ordinea blocurilor a fost păstrată; construcțiile matematice nu au fost
modificate. Fiecare design a fost verificat exhaustiv la 100%, cu blocuri
valide și distincte. Nu afirmăm optimalitatea globală.

Importul este **offline**. Aplicația folosește fișierele locale și nu
contactează Zenodo la pornire sau la generarea biletelor. Utilitarul
[import_ljcr_designs.py](../scripts/analysis/import_ljcr_designs.py)
poate reproduce importul: solicită numai 60.848 de octeți din arhiva de
4,19 GB, prin intervale exacte; verifică SHA-256 pentru secțiunea originală
și pentru designul normalizat, apoi verifică integral garanția. Fără
`--output` validează fără să scrie designuri. Codul din arhivă nu se descarcă
și nu se execută.

Rezultatele și compromisul dintre cost și pragurile de hit de deasupra
garanției sunt în
[raportul măsurătorii](../scripts/analysis/complete_covers_2026-10-15.md).

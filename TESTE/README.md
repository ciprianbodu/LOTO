# Teste

Toate modulele de teste, fixturele comune și datele lor sunt în acest folder.
Rularea obișnuită, din rădăcina proiectului, rămâne:

```bash
python -m pytest -q
```

Un singur fișier:

```bash
python -m pytest -q TESTE/test_lr_schemes.py
```

Se poate rula și `python -m pytest -q` din `TESTE`. Configurarea pytest adaugă
rădăcina proiectului la importuri, iar `conftest.py` o folosește drept director
de lucru pe durata testelor, inclusiv la colectare. Directorul inițial este
restabilit la încheiere.

Importurile între modulele de teste folosesc pachetul `TESTE`, de exemplu
`from TESTE.test_country_worker import queue`. Căile spre codul și datele
proiectului pornesc din rădăcină; fixturele de teste rămân lângă teste, în
`testdata/`, `test_demo.csv` și `test_wheel_golden.json`.

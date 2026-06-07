# 🎰 Loto Enterprise Wheeling — Instrucțiuni

Aplicație de optimizare a pool-urilor pentru Loto 6/49, Loto 5/40 și Joker, cu
benchmark de ~180 metode (ML, foundation, statistice, geometrice) și validare
walk-forward onestă.

> ⚠️ **Realism:** loteria e aleatoare. Niciun model nu o poate prezice. Aplicația
> e un instrument de **optimizare a acoperirii** (wheeling/garanție) și de
> selecție a celei mai bune metode pe istoric — NU o predicție garantată.

---

## 🚀 Pornire

Dublu-click pe **`START_8000.bat`** (sau în terminal). Face automat:
1. Verifică mediul (venv `.venv`, GPU, importuri).
2. **Trage automat ultimele actualizări de pe GitHub** (`git pull`).
3. Oprește orice UI/worker vechi (eliberează portul 8000).
4. Pornește worker-ul (fereastra „LOTO WORKER", minimizată) + UI-ul.
5. Deschide browserul pe **http://localhost:8000**.

**Nu trebuie să dai `git pull` manual** — START se ocupă.

---

## 📋 Fluxul de lucru

```
Încarci CSV-uri  →  [🟠 RE-BENCH FULL]  →  (auto) [⚡ Auto-Pilot]  →  Rezultate
                     (re-testează metodele)        (generează pool-uri)
```

1. **Încarcă datele** — secțiunea „1. Încărcare Date CSV" (loto_6_49.csv, loto_5_40.csv, joker.csv).
2. **Setări** — Dimensiune Pool (6–16), Garanție, etc.
3. **🟠 RE-BENCH FULL** — testează toate metodele pe istoric, alege câștigătorul per joc (scrie `best_methods.json`). Durează **ore** (lasă peste noapte). Dacă bifa „Auto-Pilot automat după Re-Bench" e pornită → **pornește singur Auto-Pilot** la final.
4. **⚡ Auto-Pilot** — citește decizia din bench și generează pool-urile (cu adâncimea de simulare optimă **per joc**).

### Cele trei butoane de generare
- **🔵 Auto-Pilot** — folosește metoda câștigătoare din bench + setări optime per joc.
- **Generează (manual)** — folosește setările tale din UI (slider-ele).
- **🎯 Auto-Pilot Pure** — fără filtre suplimentare (doar scorerul brut).

---

## 🧩 Ce vezi în rezultate

- **🏆 Metodă câștigătoare** (roșu) — ce model a generat pool-ul (ex. `informer`), cu descriere + familie.
- **🟢 POOL 1 — normal** — pariul principal, validat istoric prin walk-forward.
- **🔄 POOL 2 — inversat** — numerele EXCLUSE din Pool 1, jucate „pe șansă" (plasă de siguranță). Fără validare, intenționat.
- **⭐ OMNIUS** — cel mai bun bilet din FIECARE pool (top numere după scor), separat.
- **📊 Walk-forward** — validarea onestă: pe ultimele extrageri, câte numere ar fi prins pool-ul (rată %, medie/pool, max). Pliabil.
- **Variante simple** — biletele generate (wheeling cu garanție).

---

## 🔁 Când re-benchezi vs când doar generezi

| Situație | Ce faci |
|---|---|
| Adaugi câteva extrageri noi | **Doar generezi** (Auto-Pilot) — pool-ul folosește imediat datele noi |
| 50–100+ extrageri noi / câteva luni | **Re-Bench Full** o dată, să re-verifici metoda câștigătoare |
| Nu ești sigur | Te uiți la indicatorul **Freshness** (Analiză & Calibrare) |

- **Generarea folosește ÎNTOTDEAUNA CSV-ul curent** (datele noi contează imediat).
- **Re-Bench decide doar CARE metodă câștigă** — stabil, nu se schimbă de la câteva extrageri.
- Freshness: ✅ cache valid (nu re-benchezi) / 🟡 drift ușor / 🔴 drift mare.

---

## ⚙️ Concepte cheie

- **Pool (Nucleu Dur)** — setul de numere „bune" (max **16**, ca inversarea să meargă pe toate jocurile).
- **Garanție (Set Cover)** — câte numere garantezi că prinzi dacă pică în pool (wheeling).
- **Adâncime Simulare Backtesting** — cât din coada istoricului folosește POST-HOC-ul; la Auto-Pilot e **optim per joc** (din bench).
- **Inversare automată** — generează și pool-ul inversat (Pool 2). Necesită pool ≤16.
- **Walk-forward** = backtesting onest (la fiecare pas folosește doar trecutul, fără să „vadă" viitorul).

---

## 📁 Fișiere importante

| Fișier | Ce e |
|---|---|
| `_ISTORIC/` | datele tale (CSV-uri cu extrageri) — local, ne-versionat |
| `best_methods.json` | „creierul": metoda câștigătoare per joc (din bench) — local |
| `raport_complet.txt` | raportul complet după generare (deschide-l și trimite-l pentru analiză) |
| `bench_full.log` | logul benchmark-ului |
| `loto.log` | logul engine/worker |

---

## 🛠 Mentenanță

- **`ACTUALIZARI.bat`** — actualizează pachetele venv-ului (rar, după upgrade-uri).
- **🛠 Consolă DEBUG** (în UI) — loguri live (engine + bench) + buton „Curăță logurile".
- **🔴 Anulează TOT Procesul** — oprește job + bench.

> ℹ️ Worker-ul e proces **independent** de UI: dacă închizi UI-ul în timpul unei
> generări, worker-ul **termină jobul în fundal** (nu pierzi munca).

---

## 🔧 Git (o singură stație: ALF-LUPTATORI)

- Lucrezi **doar din folderul curent**; nu ține copii multiple în OneDrive (riscă conflicte pe `.git`).
- `START_8000.bat` trage singur ultimele fix-uri.
- Dacă apare vreodată conflict din sincronizare:
  ```cmd
  git reset --hard HEAD
  git pull origin main --no-edit
  ```
  (`best_methods.json` e regenerabil, deci reset-ul e sigur)

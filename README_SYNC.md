# Sincronizare Proiect între PC și Laptop

Proiectul LOTO folosește un mediu virtual Python (`.venv`). Deoarece OneDrive sincronizează tot folderul, apar conflicte de căi între PC și Laptop.

## Soluția Implementată

Am actualizat scriptul `start_app_8000.bat` să includă logică de **Auto-Reparare**:
1. Verifică dacă `python` din `.venv` funcționează pe mașina curentă.
2. Dacă nu (cale invalidă), rulează `py -3.11 -m venv .venv` pentru a "vindeca" mediul.
3. Continuă pornirea aplicației în siguranță.

## Recomandare Sincronizare (OneDrive)

Pentru performanță maximă și zero erori, recomandăm:
1. **Excluderea .venv**: Dacă este posibil, excludeți folderul `.venv` de la sincronizare.
2. **Păstrare locală**: Click dreapta pe folderul proiectului -> "Always keep on this device".
3. **Instalări separate**: Dacă adăugați librării noi pe PC (ex: `pip install x`), rulați `pip install -r requirements.txt` și pe laptop după sincronizare.

Aplicația afișează acum numele stației în secțiunea "Hardware Utilizat" pentru a putea identifica rapid mediul de execuție.

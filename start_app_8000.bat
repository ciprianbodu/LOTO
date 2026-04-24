@echo off
setlocal
cd /d "%~dp0"

echo [1/4] Verificare Mediu TimesFM (Python 3.11)...
.\venv_timesfm\Scripts\python --version
if %ERRORLEVEL% NEQ 0 (
    echo [EROARE] Mediul venv_timesfm nu a fost gasit sau Python 3.11 nu este functional!
    echo Incercati sa rulati: py -3.11 -m venv venv_timesfm
    pause
    exit /b 1
)

echo [2/4] Eliberare resurse (Port 8000 si Worker)...
powershell -Command "$p = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue; if($p){ Stop-Process -Id $p.OwningProcess -Force -ErrorAction SilentlyContinue; echo '      - Streamlit oprit.' }"
powershell -Command "$w = Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*worker.py*' }; if($w){ Stop-Process -Id $w.Id -Force -ErrorAction SilentlyContinue; echo '      - Worker oprit.' }"

echo [3/4] Pornire Worker intr-o fereastra separata...
start "LOTO WORKER" /min .\venv_timesfm\Scripts\python worker.py

echo [4/4] Pornire aplicatie Streamlit...
echo       - Adresa: http://localhost:8000
.\venv_timesfm\Scripts\python -m streamlit run app.py --server.port 8000 --browser.gatherUsageStats false

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [EROARE] Streamlit s-a oprit cu codul %ERRORLEVEL%
)

echo.
echo Script finalizat.
pause
endlocal

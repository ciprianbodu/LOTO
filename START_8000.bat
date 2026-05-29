@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

:: Resetam log-ul de eroare la startup
echo STARTUP LOG - %DATE% %TIME% > startup_error.log

:: Folosim un folder specific pentru fiecare masina, DAR in interiorul proiectului
set VENV_DIR=.venv_%COMPUTERNAME%

echo [1/4] Verificare Mediu Proiect (%VENV_DIR%)...

:: Asiguram existenta folderului venv in radacina proiectului
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [INFO] Creare mediu nou in proiect: %VENV_DIR%...
    py -3.11 -m venv %VENV_DIR%
    if !ERRORLEVEL! NEQ 0 (
        echo [EROARE] Nu am putut crea mediul in folderul proiectului. 
        echo Verificati permisiunile sau daca Python 3.11 este instalat.
        goto :error_exit
    )
)

:: Verificam mediul. START_8000.bat doar verifica + porneste; install in ACTUALIZARI.bat.
set "ENV_COMPLETE=1"
set "MISSING="

REM Core app (UI = NiceGUI; Streamlit ramane optional pentru app.py legacy)
"%VENV_DIR%\Scripts\python" -c "import nicegui" >nul 2>&1
if !ERRORLEVEL! NEQ 0 set "ENV_COMPLETE=0" & set "MISSING=!MISSING! nicegui"
for %%M in (torch timesfm pandas numpy scipy) do (
    "%VENV_DIR%\Scripts\python" -c "import %%M" >nul 2>&1
    if !ERRORLEVEL! NEQ 0 (
        set "ENV_COMPLETE=0"
        set "MISSING=!MISSING! %%M"
    )
)

REM Benchmark stack (auto-pilot are nevoie de ele)
for %%M in (chronos momentfm neuralforecast rich pynvml transformers) do (
    "%VENV_DIR%\Scripts\python" -c "import %%M" >nul 2>&1
    if !ERRORLEVEL! NEQ 0 (
        set "ENV_COMPLETE=0"
        set "MISSING=!MISSING! %%M"
    )
)

if "%ENV_COMPLETE%"=="0" (
    echo.
    echo ============================================================
    echo  [EROARE] Mediul Python e incomplet - lipsesc:!MISSING!
    echo ============================================================
    echo.
    echo  START_8000.bat doar verifica + porneste; nu face install.
    echo  Pentru install, ruleaza:
    echo.
    echo     ACTUALIZARI.bat
    echo.
    echo  Apoi ruleaza din nou START_8000.bat.
    echo ============================================================
    goto :error_exit
)
echo [OK] Mediul complet: nicegui + torch + timesfm + chronos + momentfm
echo      + neuralforecast + rich + pynvml + transformers.

echo [2/4] Eliberare resurse (Port 8000 + workeri vechi)...
:: Kill orice proces pe portul 8000 (streamlit vechi)
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000') do (
    if NOT "%%a"=="0" taskkill /f /pid %%a >nul 2>&1
)

:: Kill workeri vechi via PowerShell (wmic e deprecat pe Win11 si nu mai functioneaza).
:: Match strict: doar python.exe care ruleaza 'worker.py' din proiectul asta (via %~dp0).
:: Backslash-urile din %~dp0 sunt tratate literal in -like (nu regex), deci nu necesita escape.
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*worker.py*' -and $_.CommandLine -like '*%~dp0*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host ('[CLEANUP] Worker vechi oprit: PID ' + $_.ProcessId) }"

:: Asteapta 1 secunda pentru ca procesele sa se inchida curat inainte sa pornim altele noi
timeout /t 1 /nobreak >nul 2>&1

echo [3/4] Pornire Worker...
:: Folosim cai absolute pentru ca app.py:_is_worker_running() sa detecteze corect
:: worker-ul (altfel, cu cale relativa, cmdline nu contine project_root si app.py
:: spawn-eaza un al doilea worker paralel - race condition confirmata 2026-05-02).
start "LOTO WORKER" /min "%~dp0%VENV_DIR%\Scripts\python.exe" "%~dp0worker.py"

echo [4/4] Pornire UI NiceGUI (port 8000)...
:: NiceGUI tine starea pe server si face update prin websocket (fara reload de
:: pagina) -> bifele/CSV-ul NU se mai pierd. UI vechi Streamlit: app.py (legacy).
set LOTO_UI_PORT=8000
"%~dp0%VENV_DIR%\Scripts\python.exe" "%~dp0app_nicegui.py"
if !ERRORLEVEL! NEQ 0 goto :error_exit

echo.
echo Script finalizat cu succes.
pause
exit /b 0

:error_exit
echo.
echo !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
echo [CRITIC] Eroare detectata. Fereastra ramane deschisa.
echo !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
pause
cmd /k

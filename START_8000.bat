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

:: Verificam daca mediul este complet
set "ENV_COMPLETE=1"
if not exist "%VENV_DIR%\Scripts\streamlit.exe" set "ENV_COMPLETE=0"
"%VENV_DIR%\Scripts\python" -c "import torch" >nul 2>&1
if !ERRORLEVEL! NEQ 0 set "ENV_COMPLETE=0"

if "%ENV_COMPLETE%"=="0" (
    echo [INFO] Mediul este incomplet. Incep instalarea locala...
    echo [PAS 1/3] Actualizare Pip...
    "%VENV_DIR%\Scripts\python" -m pip install --upgrade pip
    
    echo [PAS 2/3] Instalare dependinte din requirements.txt...
    echo (Aceasta etapa descarca PyTorch si poate dura 2-5 minute)
    "%VENV_DIR%\Scripts\python" -m pip install -r requirements.txt
    
    echo [PAS 3/3] Verificare finala...
    "%VENV_DIR%\Scripts\python" -c "import torch; import streamlit; print('Verificare REUSITA')"
    
    if !ERRORLEVEL! NEQ 0 (
        echo [EROARE] Instalarea a esuat la verificarea finala.
        goto :error_exit
    )
    echo [OK] Dependinte instalate local cu succes.
)

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

echo [4/4] Pornire Streamlit...
"%~dp0%VENV_DIR%\Scripts\python.exe" -m streamlit run "%~dp0app.py" --server.port 8000 --browser.gatherUsageStats false
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

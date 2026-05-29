@echo off
REM ============================================================
REM START_8000.bat — Launcher rapid + log silent in fundal.
REM Pe success: nu mai vezi nimic despre log, pornesti direct streamlit.
REM Pe eroare: afisez log-ul automat si las fereastra deschisa.
REM ============================================================
cd /d "%~dp0"
set "LOGFILE=%~dp0startup_8000.log"

REM ---- Header log (overwrite la fiecare rulare; vizibil DOAR la eroare) ----
> "%LOGFILE%" echo === START_8000 LOG ===
>> "%LOGFILE%" echo Time:     %DATE% %TIME%
>> "%LOGFILE%" echo CWD:      %CD%
>> "%LOGFILE%" echo Computer: %COMPUTERNAME%
>> "%LOGFILE%" echo.

REM ===== Verify phase (silent, logat in fundal) =====
call :verify_phase >> "%LOGFILE%" 2>&1
set "VERIFY_RC=%ERRORLEVEL%"

if not "%VERIFY_RC%"=="0" (
    echo.
    echo ============================================================
    echo  [EROARE] Verificare mediu esuata. Log:
    echo ============================================================
    type "%LOGFILE%"
    echo ============================================================
    echo  RC = %VERIFY_RC%
    echo ============================================================
    pause
    cmd /k
    exit /b %VERIFY_RC%
)

REM ===== Launch phase (live, fara redirectare) =====
call :launch_phase
set "LAUNCH_RC=%ERRORLEVEL%"

if not "%LAUNCH_RC%"=="0" (
    echo.
    echo [EROARE] Launch esuat ^(RC=%LAUNCH_RC%^).
    pause
    cmd /k
    exit /b %LAUNCH_RC%
)

exit /b 0


REM ============================================================
REM :verify_phase — verifica venv, detecteaza GPU, importa core/benchmark
REM ============================================================
:verify_phase
setlocal enabledelayedexpansion
set "VENV_DIR=.venv_ALF-LUPTATORI"
echo [1/4] Verificare Mediu Proiect (%VENV_DIR%)

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [INFO] Creare mediu nou: %VENV_DIR%
    py -3.11 -m venv "%VENV_DIR%"
    if not !ERRORLEVEL!==0 (
        echo [EROARE] Creare venv esuata. Verifica Python 3.11 si permisiunile.
        endlocal & exit /b 10
    )
)

echo.
echo --- Detectare GPU ---
REM Statie unica (LUPTATORI) - fara logica multi-statie/OneDrive. Detectam GPU
REM o data si cache-uim in .machine_profile; daca exista, il folosim direct.
set "GPU_TYPE=UNKNOWN"
set "GPU_NAME="
if exist ".machine_profile" (
    for /f "tokens=1,2 delims==" %%A in (.machine_profile) do (
        if "%%A"=="GPU_TYPE" set "GPU_TYPE=%%B"
        if "%%A"=="GPU_NAME" set "GPU_NAME=%%B"
    )
    echo Profil hardware cached: GPU_TYPE=!GPU_TYPE!  NAME=!GPU_NAME!
) else (
    echo Profil hardware lipsa, detectez acum...
    call :DetectGpu
    for /f "tokens=1,2 delims==" %%A in (.machine_profile) do (
        if "%%A"=="GPU_TYPE" set "GPU_TYPE=%%B"
        if "%%A"=="GPU_NAME" set "GPU_NAME=%%B"
    )
    echo Detectie: GPU_TYPE=!GPU_TYPE!  NAME=!GPU_NAME!
)

if /i "!GPU_TYPE!"=="NVIDIA" (
    echo Mod: GPU
) else (
    echo Mod: CPU-ONLY ^(setez CUDA_VISIBLE_DEVICES=-1 ca torch sa nu probleze CUDA^)
)

echo.
echo --- Verificare UI NiceGUI ---
"%VENV_DIR%\Scripts\python.exe" -c "import nicegui" >nul 2>&1
if not "!ERRORLEVEL!"=="0" (
    echo [LIPSA] nicegui nu e instalat in venv.
    echo Solutie: ruleaza ACTUALIZARI.bat apoi reincearca.
    endlocal & exit /b 20
)
echo [OK] nicegui prezent.

REM Pe CPU-only, setam env vars ca import-urile sa fie rapide si offline.
if /i not "!GPU_TYPE!"=="NVIDIA" (
    set CUDA_VISIBLE_DEVICES=-1
    set HF_HUB_OFFLINE=1
    set TRANSFORMERS_OFFLINE=1
    set PYTHONUNBUFFERED=1
) else (
    set HF_HUB_OFFLINE=1
    set TRANSFORMERS_OFFLINE=1
    set PYTHONUNBUFFERED=1
)

echo.
echo --- Verificare imports prin verify_imports.py ---
echo ^(timesfm OPTIONAL pe CPU, REQUIRED pe GPU; progress real-time^)
"%VENV_DIR%\Scripts\python.exe" -u "%~dp0verify_imports.py"
set "VERIFY_PY_RC=!ERRORLEVEL!"

if not "!VERIFY_PY_RC!"=="0" (
    echo.
    echo [EROARE] verify_imports.py a returnat RC=!VERIFY_PY_RC!
    echo Vezi mai sus pentru pachetele REQUIRED lipsa.
    endlocal & exit /b !VERIFY_PY_RC!
)

echo.
echo [OK] Mediu verificat complet.
endlocal & exit /b 0


REM ============================================================
REM :launch_phase — porneste worker + streamlit
REM ============================================================
:launch_phase
setlocal enabledelayedexpansion
set "VENV_DIR=.venv_ALF-LUPTATORI"

REM Re-aplica env vars din profil (sunt propagate in subprocese streamlit + worker)
set "GPU_TYPE=NVIDIA"
if exist ".machine_profile" (
    for /f "tokens=1,2 delims==" %%A in (.machine_profile) do (
        if "%%A"=="GPU_TYPE" set "GPU_TYPE=%%B"
    )
)
if /i not "!GPU_TYPE!"=="NVIDIA" set CUDA_VISIBLE_DEVICES=-1
REM Modelele TimesFM sunt deja cached local; nu vrem network calls la runtime.
set HF_HUB_OFFLINE=1
set TRANSFORMERS_OFFLINE=1

echo [2/4] Eliberare resurse (port 8000, workeri vechi)
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 2^>nul') do (
    if NOT "%%a"=="0" taskkill /f /pid %%a >nul 2>&1
)
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*worker.py*' -and $_.CommandLine -like '*%~dp0*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host ('[CLEANUP] Worker vechi oprit: PID ' + $_.ProcessId) }"
timeout /t 1 /nobreak >nul 2>&1

echo [3/4] Pornire Worker
start "LOTO WORKER" /min "%~dp0%VENV_DIR%\Scripts\python.exe" "%~dp0worker.py"

echo [4/4] Pornire UI NiceGUI (port 8000)
REM NiceGUI tine starea pe server si face update prin websocket (fara reload de
REM pagina) -^> bifele/CSV-ul NU se mai pierd. UI vechi Streamlit: app.py (legacy).
REM Deschidem browserul automat dupa 5s (timp ca serverul sa porneasca), intr-un
REM proces paralel ca sa nu blocheze pornirea serverului.
start "" /min cmd /c "timeout /t 5 /nobreak >nul & start http://localhost:8000"
set "LOTO_UI_PORT=8000"
"%~dp0%VENV_DIR%\Scripts\python.exe" "%~dp0app_nicegui.py"
set "RC=!ERRORLEVEL!"
endlocal & exit /b %RC%


REM ============================================================
REM :DetectGpu — detecteaza GPU NVIDIA si scrie .machine_profile
REM ============================================================
:DetectGpu
setlocal enabledelayedexpansion
set "DETECTED_TYPE=CPU_ONLY"
set "DETECTED_NAME="

REM Test 1: nvidia-smi (rapid)
where nvidia-smi >nul 2>&1
if !ERRORLEVEL!==0 (
    nvidia-smi -L >nul 2>&1
    if !ERRORLEVEL!==0 (
        set "DETECTED_TYPE=NVIDIA"
        for /f "tokens=*" %%G in ('nvidia-smi --query-gpu^=name --format^=csv^,noheader 2^>nul') do (
            if "!DETECTED_NAME!"=="" set "DETECTED_NAME=%%G"
        )
        goto :DG_Write
    )
)

REM Test 2: PowerShell fallback (Win32_VideoController) — caut NVIDIA
for /f "tokens=*" %%G in ('powershell -NoProfile -Command "(Get-CimInstance Win32_VideoController | Where-Object { $_.Name -like '*NVIDIA*' } | Select-Object -First 1).Name" 2^>nul') do (
    if not "%%G"=="" (
        set "DETECTED_TYPE=NVIDIA"
        set "DETECTED_NAME=%%G"
    )
)

:DG_Write
(
    echo GPU_TYPE=!DETECTED_TYPE!
    echo GPU_NAME=!DETECTED_NAME!
    echo DETECTED_AT=%DATE% %TIME%
) > .machine_profile
endlocal & exit /b 0

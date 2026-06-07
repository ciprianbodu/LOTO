@echo off
REM ============================================================
REM START_8000.bat — Launcher rapid + log silent in fundal.
REM Pe success: nu mai vezi nimic despre log, pornesti direct NiceGUI (app_nicegui.py).
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

REM ===== Auto-update din GitHub (best-effort; NU blocheaza daca esueaza) =====
REM Aduce ultimele fix-uri automat la fiecare pornire. best_methods.json /
REM _ISTORIC / venv sunt gitignore-uite -> fara divergenta -> fast-forward curat.
where git >nul 2>&1
if errorlevel 1 (
    echo [GIT] git negasit - sar peste auto-update.
) else (
    call :git_autoupdate
)
echo.

REM ===== Auto-update CSV extrageri (best-effort, silent) =====
REM Detecteaza extrageri noi pe loto49.ro si le adauga in _ISTORIC/ fara sa
REM blocheze pornirea (exit 0 mereu, chiar si la eroare de retea).
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0update_csv.py" >> "%LOGFILE%" 2>&1
)

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
set "VENV_DIR=.venv"
echo [1/4] Verificare Mediu Proiect (%VENV_DIR%)

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [INFO] Creare mediu nou: %VENV_DIR%
    py -3.14 -m venv "%VENV_DIR%"
    if not !ERRORLEVEL!==0 (
        echo [EROARE] Creare venv esuata. Verifica Python 3.14 si permisiunile.
        endlocal & exit /b 10
    )
)

echo.
echo --- Detectare GPU ---
REM Statie unica (LUPTATORI) - fara logica multi-statie/OneDrive. Detectam GPU
REM o data si cache-uim in .machine_profile; daca exista, il folosim direct.
set "GPU_TYPE=UNKNOWN"
set "GPU_NAME="
REM Re-detectam MEREU cu nvidia-smi (sursa de adevar). NU ne mai bazam pe
REM .machine_profile cache-uit: pe OneDrive se sincronizeaza intre masini
REM (ALF NVIDIA -> laptop fara GPU) si ar da profil GRESIT (verify_imports ar
REM cere torch GPU pe o masina fara GPU -> RC=20). DetectGpu rescrie profilul.
call :DetectGpu
for /f "tokens=1,2 delims==" %%A in (.machine_profile) do (
    if "%%A"=="GPU_TYPE" set "GPU_TYPE=%%B"
    if "%%A"=="GPU_NAME" set "GPU_NAME=%%B"
)
echo Detectie GPU ^(nvidia-smi, sursa de adevar^): GPU_TYPE=!GPU_TYPE!  NAME=!GPU_NAME!

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
REM :launch_phase — porneste worker + NiceGUI
REM ============================================================
:launch_phase
setlocal enabledelayedexpansion
set "VENV_DIR=.venv"

REM Re-aplica env vars din profil (sunt propagate in subprocese NiceGUI + worker)
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

echo [2/4] Eliberare resurse (port 8000, UI + worker + bench vechi)
REM Omoara procesele python ale ACESTUI proiect din sesiunea anterioara: app_nicegui.py
REM (UI care tine portul 8000), worker.py, SI bench_all_methods.py (bench-ul ruleaza in
REM CMD propriu, CREATE_NEW_CONSOLE -> ramanea deschis dupa restart si putea rula in
REM paralel cu un bench nou = doua procese scriu folds.csv = corupere). Omorand parintele
REM bench, copiii din ProcessPool (CPU/GPU) ies singuri (pipe rupt).
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | Where-Object { $_.CommandLine -and ($_.CommandLine -like '*app_nicegui.py*' -or $_.CommandLine -like '*worker.py*' -or $_.CommandLine -like '*bench_all_methods.py*') -and $_.CommandLine -like '*%~dp0*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host ('[CLEANUP] Oprit PID ' + $_.ProcessId) }"
REM Fallback: orice mai asculta pe 8000 (LISTENING)
for /f "tokens=5" %%a in ('netstat -aon ^| findstr "LISTENING" ^| findstr ":8000" 2^>nul') do (
    if NOT "%%a"=="0" taskkill /f /pid %%a >nul 2>&1
)
timeout /t 3 /nobreak >nul 2>&1

REM Golire coada de joburi la FIECARE pornire -> mereu fresh, fara joburi
REM reziduale care se reiau singure (procesele vechi sunt deja omorate la [2/4],
REM deci putem reseta in siguranta). Numerotarea reincepe de la #1.
echo [2b/4] Golire coada de joburi (fresh start)
"%~dp0%VENV_DIR%\Scripts\python.exe" "%~dp0reset_jobs.py" --force

echo [3/4] Pornire Worker
start "LOTO WORKER" /min "%~dp0%VENV_DIR%\Scripts\python.exe" "%~dp0worker.py"

echo [4/4] Pornire UI NiceGUI (port 8000)
REM NiceGUI tine starea pe server si face update prin websocket (fara reload de
REM pagina) -^> bifele/CSV-ul NU se mai pierd.
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


:git_autoupdate
REM ============================================================
REM Auto-update ROBUST. Inainte: la orice esec al fast-forward-ului sarea TACIT
REM update-ul -> ramaneai pe cod vechi fara sa stii de ce. Acum: arata cauza reala
REM (git status) si SINCRONIZEAZA FORTAT cu GitHub (cu backup in stash).
REM   - Datele tale (best_methods.json, _ISTORIC, pool_history, raport, venv,
REM     .machine_profile) sunt gitignore -> NU se pierd la reset.
REM   - Modificarile locale la fisiere URMARITE sunt salvate in 'git stash list'.
REM ============================================================
echo [GIT] Verific actualizari de pe GitHub...
REM OneDrive strica scrierea atomica in .git -> "update_ref failed / Invalid argument"
REM la merge/reset. Dezactivam appendAtomically (leacul recomandat de git insusi).
git config windows.appendAtomically false >nul 2>&1
git fetch origin main --quiet 2>nul
if errorlevel 1 (
    echo [GIT] Offline / fetch esuat - pornesc cu codul curent.
    goto :eof
)
git merge --ff-only origin/main >nul 2>&1
if not errorlevel 1 (
    echo [GIT] Cod la zi cu GitHub.
    goto :eof
)
echo [GIT] Fast-forward imposibil ^(divergenta sau modificari locale^). Stare:
git status -sb
echo [GIT] Sincronizez FORTAT cu GitHub ^(backup local in stash^)...
git stash push -m "auto-backup START_8000" >nul 2>&1
git reset --hard origin/main >nul 2>&1
if errorlevel 1 (
    echo [GIT] Sincronizare fortata esuata - pornesc cu codul curent.
) else (
    echo [GIT] Sincronizat la zi cu GitHub. Backup local: ruleaza 'git stash list'.
)
goto :eof

@echo off
setlocal DisableDelayedExpansion
REM ============================================================
REM START_8000.bat — launcher UI + worker.
REM Daca origin/main e inainte sau lansatoarele de pe disc sunt vechi:
REM scriem D:\_BUILD\_LOTO\loto_relaunch.bat, IESIM, iar acela face
REM git pull --ff-only si reporneste. Nu tragem .bat-ul aflat in rulare.
REM CRLF obligatoriu (.gitattributes). Linie goala = echo/ nu echo.
REM In echo din blocuri if (...): fara paranteze rotunde.
REM ============================================================
set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"

:main
if "%PROJECT_DIR%"=="" set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"
REM Doar START_8000.bat si ACTUALIZARI.bat raman. Helperul git vechi si
REM orice alt .bat din radacina se sterg de pe disc.
for %%F in ("%~dp0*.bat") do (
    if /I not "%%~nxF"=="START_8000.bat" if /I not "%%~nxF"=="ACTUALIZARI.bat" (
        echo [GIT] Sterg lansator vechi %%~nxF
        del /f /q "%%~fF" >nul 2>&1
    )
)
set "RUNTIME_DIR=%LOTO_RUNTIME_DIR%"
if "%RUNTIME_DIR%"=="" set "RUNTIME_DIR=D:\_BUILD\_LOTO"
if not exist "%RUNTIME_DIR%" mkdir "%RUNTIME_DIR%"
set "RELAUNCH=START_8000.bat"
call :maybe_git_update
set "LOGFILE=%RUNTIME_DIR%\startup_8000.log"

REM Venv-ul sta in afara OneDrive (D:\_BUILD\_LOTO) ca sa nu fie sincronizat.
set "VENV_DIR=D:\_BUILD\_LOTO\.venv"

REM ---- Header log (overwrite la fiecare rulare; vizibil DOAR la eroare) ----
> "%LOGFILE%" echo === START_8000 LOG ===
>> "%LOGFILE%" echo Time:     %DATE% %TIME%
>> "%LOGFILE%" echo CWD:      %CD%
>> "%LOGFILE%" echo Computer: %COMPUTERNAME%
>> "%LOGFILE%" echo/

REM ===== Auto-update CSV extrageri, best-effort, silent =====
REM Detecteaza extrageri noi pe loto49.ro si le adauga in _ISTORIC fara sa
REM blocheze pornirea. Exit 0 mereu, chiar si la eroare de retea.
if exist "%VENV_DIR%\Scripts\python.exe" (
    "%VENV_DIR%\Scripts\python.exe" "%PROJECT_DIR%update_csv.py" >> "%LOGFILE%" 2>&1
)

REM ===== Auto-commit + push extrageri noi din _ISTORIC, best-effort =====
REM Vizibil in consola, nu doar in startup_8000.log. Push STRICT pe origin/main.
REM git push origin HEAD pe alta ramura era pierdut la urmatorul reset.
where git >nul 2>&1
if not errorlevel 1 call :push_istoric

REM ===== Verify phase (silent, logat in fundal) =====
call :verify_phase >> "%LOGFILE%" 2>&1
set "VERIFY_RC=%ERRORLEVEL%"

if not "%VERIFY_RC%"=="0" (
    echo/
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
    echo/
    echo [EROARE] Launch esuat RC=%LAUNCH_RC%.
    pause
    cmd /k
    exit /b %LAUNCH_RC%
)

exit /b 0


REM ============================================================
REM :verify_phase — verifica venv + importa core/benchmark (exclusiv CPU)
REM ============================================================
:verify_phase
setlocal enabledelayedexpansion
set "VENV_DIR=D:\_BUILD\_LOTO\.venv"
echo [1/4] Verificare Mediu Proiect - %VENV_DIR%

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [EROARE] Mediul aplicației lipseste: %VENV_DIR%
    echo Solutie: ruleaza ACTUALIZARI.bat, apoi reincearca START_8000.bat.
    endlocal & exit /b 10
)

echo/
echo --- Verificare UI NiceGUI ---
"%VENV_DIR%\Scripts\python.exe" -c "import nicegui" >nul 2>&1
if not "!ERRORLEVEL!"=="0" (
    echo [LIPSA] nicegui nu e instalat in venv.
    echo Solutie: ruleaza ACTUALIZARI.bat apoi reincearca.
    endlocal & exit /b 20
)
echo [OK] nicegui prezent.

set PYTHONUNBUFFERED=1

echo/
echo --- Verificare imports prin verify_imports.py - exclusiv CPU ---
"%VENV_DIR%\Scripts\python.exe" -u "%~dp0verify_imports.py"
set "VERIFY_PY_RC=!ERRORLEVEL!"

if not "!VERIFY_PY_RC!"=="0" (
    echo/
    echo [EROARE] verify_imports.py a returnat RC=!VERIFY_PY_RC!
    echo Vezi mai sus pentru pachetele REQUIRED lipsa.
    REM %VERIFY_PY_RC% - procent, NU !VERIFY_PY_RC! - endlocal opreste
    REM delayed expansion INAINTE ca exit sa ruleze pe acelasi rand cu &,
    REM deci !var! s-ar rezolva gol - RC-ul real s-ar pierde.
    endlocal & exit /b %VERIFY_PY_RC%
)

echo/
echo [OK] Mediu verificat complet.
endlocal & exit /b 0


REM ============================================================
REM :launch_phase — porneste worker + NiceGUI
REM ============================================================
:launch_phase
setlocal enabledelayedexpansion
set "VENV_DIR=D:\_BUILD\_LOTO\.venv"
set PYTHONUNBUFFERED=1

echo [2/4] Eliberare resurse - port 8000, UI + worker + bench vechi
REM Omoara UI + worker + bench + copiii ProcessPool din sesiunea anterioara.
REM NU filtra pe calea proiectului in CommandLine: bench-ul din UI e pornit cu
REM cale RELATIVA, exe-ul e venv-ul din D:\_BUILD\_LOTO (in AFARA repo-ului),
REM deci %~dp0 nu apare pe cmdline — acelasi bug ca in cancel_all din UI.
REM Python face tree-kill: pe Windows uciderea parintelui NU omoara copiii.
"%VENV_DIR%\Scripts\python.exe" "%~dp0cleanup_old_processes.py" --venv "%VENV_DIR%" --port 8000
REM Fallback: orice mai asculta pe 8000. /C:":8000 " evita :80001; /T = arborele.
for /f "tokens=5" %%a in ('netstat -aon ^| findstr "LISTENING" ^| findstr /C:":8000 " 2^>nul') do (
    if NOT "%%a"=="0" (
        echo [CLEANUP] Port 8000 inca ocupat de PID %%a
        taskkill /f /t /pid %%a >nul 2>&1
    )
)
timeout /t 3 /nobreak >nul 2>&1

REM Golire coada de joburi la FIECARE pornire -> mereu fresh, fara joburi
REM reziduale care se reiau singure (procesele vechi sunt deja omorate la [2/4],
REM deci putem reseta in siguranta). Numerotarea reincepe de la #1.
echo [2b/4] Golire coada de joburi - fresh start
"%VENV_DIR%\Scripts\python.exe" "%~dp0reset_jobs.py" --force
if errorlevel 1 (
    echo [EROARE] Resetarea cozii de joburi a esuat. Nu pornesc worker-ul peste o baza inconsistenta.
    endlocal & exit /b 30
)

echo [3/4] Pornire Worker
start "LOTO WORKER" /min "%VENV_DIR%\Scripts\python.exe" "%~dp0worker.py"

echo [4/4] Pornire UI NiceGUI - port 8000
REM NiceGUI tine starea pe server si face update prin websocket (fara reload de
REM pagina) -^> bifele/CSV-ul NU se mai pierd.
REM Deschidem browserul automat dupa 12s, intr-un proces paralel.
echo [UI] http://127.0.0.1:8000 - lasa fereastra asta DESCHISA.
REM 127.0.0.1 nu localhost: Chrome pe Windows rezolva localhost ca IPv6 ::1, iar
REM NiceGUI asculta IPv4 -^> ERR_CONNECTION_REFUSED. 12s: importul a 50 metode.
start "" /min cmd /c "timeout /t 12 /nobreak >nul & start http://127.0.0.1:8000"
set "LOTO_UI_PORT=8000"
REM Sesiune noua: UI-ul NU reia un job vechi si NU afiseaza «Job în rulare»
REM pana nu apesi Genereaza / Auto-Pilot. Worker-ul NU primeste flag-ul
REM (trebuie sa preia joburile pe care le trimitI TU dupa pornire).
set "LOTO_FRESH_START=1"
"%VENV_DIR%\Scripts\python.exe" "%~dp0app_nicegui.py"
set "RC=!ERRORLEVEL!"
endlocal & exit /b %RC%


:push_istoric
where git >nul 2>&1
if errorlevel 1 goto :eof
if "%PROJECT_DIR%"=="" set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"
git config core.hooksPath scripts/git-hooks >nul 2>&1
git status --porcelain _ISTORIC 2>nul | findstr /R "." >nul 2>&1
if errorlevel 1 (
    echo [GIT] _ISTORIC fara modificari.
    goto :eof
)
echo [GIT] Extrageri noi - commit + push origin/main...
git add -A -- _ISTORIC
if errorlevel 1 goto :eof
git diff --cached --quiet -- _ISTORIC
if not errorlevel 1 goto :eof
git commit -m "auto: update istoric extrageri"
if errorlevel 1 (
    echo [GIT] commit _ISTORIC esuat.
    goto :eof
)
git push origin main <nul
if errorlevel 1 echo [GIT] Push _ISTORIC esuat - commitul e local.
goto :eof


:maybe_git_update
if "%LOTO_RELAUNCHED%"=="1" (
    echo [GIT] Repornit dupa actualizare.
    goto :eof
)
where git >nul 2>&1
if errorlevel 1 (
    echo [GIT] git.exe nu e in PATH. Continui cu codul local.
    goto :eof
)
if "%PROJECT_DIR%"=="" set "PROJECT_DIR=%~dp0"
if "%RUNTIME_DIR%"=="" set "RUNTIME_DIR=D:\_BUILD\_LOTO"
if "%RELAUNCH%"=="" set "RELAUNCH=START_8000.bat"
cd /d "%PROJECT_DIR%"
git config core.hooksPath scripts/git-hooks >nul 2>&1
echo [GIT] Verific origin/main...
set "GIT_TERMINAL_PROMPT=0"
git fetch origin <nul
set "FETCH_FAILED="
if errorlevel 1 set "FETCH_FAILED=1"
set "LOCAL_SHA="
set "REMOTE_SHA="
for /f %%H in ('git rev-parse HEAD 2^>nul') do set "LOCAL_SHA=%%H"
for /f %%H in ('git rev-parse origin/main 2^>nul') do set "REMOTE_SHA=%%H"
echo [GIT] local  %LOCAL_SHA%
echo [GIT] origin %REMOTE_SHA%
set "NEED_UPDATE="
if not "%LOCAL_SHA%"=="%REMOTE_SHA%" set "NEED_UPDATE=1"
git diff --quiet -- START_8000.bat ACTUALIZARI.bat
if errorlevel 1 set "NEED_UPDATE=1"
if defined FETCH_FAILED set "NEED_UPDATE=1"
if not defined NEED_UPDATE (
    echo [GIT] Deja la zi.
    goto :eof
)
echo [GIT] Cod nou sau lansator vechi pe disc. Actualizez si repornesc...
if not exist "%RUNTIME_DIR%" mkdir "%RUNTIME_DIR%"
set "UPDATER=%RUNTIME_DIR%\loto_relaunch.bat"
> "%UPDATER%" echo @echo off
>> "%UPDATER%" echo cd /d "%PROJECT_DIR%."
>> "%UPDATER%" echo echo [GIT] Astept 2s ca lansatorul vechi sa se inchida...
>> "%UPDATER%" echo timeout /t 2 /nobreak ^>nul
>> "%UPDATER%" echo git config core.hooksPath scripts/git-hooks
>> "%UPDATER%" echo git fetch origin
>> "%UPDATER%" echo git pull --ff-only origin main
>> "%UPDATER%" echo if errorlevel 1 echo [GIT] pull --ff-only esuat - restabilesc lansatoarele.
>> "%UPDATER%" echo git checkout origin/main -- START_8000.bat ACTUALIZARI.bat
>> "%UPDATER%" echo echo [GIT] Repornesc lansatorul actualizat.
>> "%UPDATER%" echo set LOTO_RELAUNCHED=1
>> "%UPDATER%" echo call %RELAUNCH%
echo [GIT] Inchid fereastra curenta ca sa pot inlocui lansatorul.
start "LOTO UPDATE" cmd /c "%UPDATER%"
exit 0


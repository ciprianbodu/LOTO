@echo off
REM ============================================================
REM START_8000.bat — Launcher rapid + log silent in fundal.
REM Pe success: nu mai vezi nimic despre log, pornesti direct NiceGUI (app_nicegui.py).
REM Pe eroare: afisez log-ul automat si las fereastra deschisa.
REM CRLF obligatoriu (.gitattributes). Linie goala = echo/ nu echo.
REM In echo din blocuri if (...): fara paranteze rotunde.
REM ============================================================
cd /d "%~dp0"
set "LOGFILE=%~dp0startup_8000.log"

REM Venv-ul sta in afara OneDrive (D:\_BUILD\_LOTO) ca sa nu fie sincronizat.
set "VENV_DIR=D:\_BUILD\_LOTO\.venv"

REM ---- Header log (overwrite la fiecare rulare; vizibil DOAR la eroare) ----
> "%LOGFILE%" echo === START_8000 LOG ===
>> "%LOGFILE%" echo Time:     %DATE% %TIME%
>> "%LOGFILE%" echo CWD:      %CD%
>> "%LOGFILE%" echo Computer: %COMPUTERNAME%
>> "%LOGFILE%" echo/

REM ===== Auto-update din GitHub (best-effort; NU blocheaza daca esueaza) =====
REM Aduce ultimele fix-uri de pe origin/main. best_methods.json / venv sunt
REM gitignore; _ISTORIC E VERSIONAT (commit+push dupa update_csv).
where git >nul 2>&1
if errorlevel 1 (
    echo [GIT] git negasit - sar peste auto-update.
) else (
    call :git_autoupdate
)
echo/

REM ===== Auto-update CSV extrageri (best-effort, silent) =====
REM Detecteaza extrageri noi pe loto49.ro si le adauga in _ISTORIC/ fara sa
REM blocheze pornirea (exit 0 mereu, chiar si la eroare de retea).
if exist "%VENV_DIR%\Scripts\python.exe" (
    "%VENV_DIR%\Scripts\python.exe" "%~dp0update_csv.py" >> "%LOGFILE%" 2>&1
)

REM ===== Auto-commit + push extrageri noi din _ISTORIC (best-effort) =====
REM VIZIBIL in consola (nu doar in startup_8000.log). Push STRICT pe origin/main
REM — `git push origin HEAD` pe alta ramura era pierdut la urmatorul reset.
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
    echo [INFO] Creare mediu nou: %VENV_DIR%
    if not exist "D:\_BUILD\_LOTO" mkdir "D:\_BUILD\_LOTO"
    py -3.14 -m venv "%VENV_DIR%"
    if not !ERRORLEVEL!==0 (
        echo [EROARE] Creare venv esuata. Verifica Python 3.14 si permisiunile.
        endlocal & exit /b 10
    )
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
    endlocal & exit /b !VERIFY_PY_RC!
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
REM Omoara procesele python ale ACESTUI proiect din sesiunea anterioara: app_nicegui.py
REM (UI care tine portul 8000), worker.py, SI bench_all_methods.py (bench-ul ruleaza in
REM CMD propriu, CREATE_NEW_CONSOLE -> ramanea deschis dupa restart si putea rula in
REM paralel cu un bench nou = doua procese scriu folds.csv = corupere). Omorand parintele
REM bench, copiii din ProcessPool ies singuri (pipe rupt).
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | Where-Object { $_.CommandLine -and ($_.CommandLine -like '*app_nicegui.py*' -or $_.CommandLine -like '*worker.py*' -or $_.CommandLine -like '*bench_all_methods.py*') -and $_.CommandLine -like '*%~dp0*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host ('[CLEANUP] Oprit PID ' + $_.ProcessId) }"
REM Fallback: orice mai asculta pe 8000 (LISTENING)
for /f "tokens=5" %%a in ('netstat -aon ^| findstr "LISTENING" ^| findstr ":8000" 2^>nul') do (
    if NOT "%%a"=="0" taskkill /f /pid %%a >nul 2>&1
)
timeout /t 3 /nobreak >nul 2>&1

REM Golire coada de joburi la FIECARE pornire -> mereu fresh, fara joburi
REM reziduale care se reiau singure (procesele vechi sunt deja omorate la [2/4],
REM deci putem reseta in siguranta). Numerotarea reincepe de la #1.
echo [2b/4] Golire coada de joburi - fresh start
"%VENV_DIR%\Scripts\python.exe" "%~dp0reset_jobs.py" --force

echo [3/4] Pornire Worker
start "LOTO WORKER" /min "%VENV_DIR%\Scripts\python.exe" "%~dp0worker.py"

echo [4/4] Pornire UI NiceGUI - port 8000
REM NiceGUI tine starea pe server si face update prin websocket (fara reload de
REM pagina) -^> bifele/CSV-ul NU se mai pierd.
REM Deschidem browserul automat dupa 5s (timp ca serverul sa porneasca), intr-un
REM proces paralel ca sa nu blocheze pornirea serverului.
start "" /min cmd /c "timeout /t 5 /nobreak >nul & start http://localhost:8000"
set "LOTO_UI_PORT=8000"
REM Sesiune noua: UI-ul NU reia un job vechi si NU afiseaza «Job în rulare»
REM pana nu apesi Genereaza / Auto-Pilot. Worker-ul NU primeste flag-ul
REM (trebuie sa preia joburile pe care le trimitI TU dupa pornire).
set "LOTO_FRESH_START=1"
"%VENV_DIR%\Scripts\python.exe" "%~dp0app_nicegui.py"
set "RC=!ERRORLEVEL!"
endlocal & exit /b %RC%


:git_autoupdate
call "%~dp0loto_git_sync.bat" autoupdate
goto :eof


:push_istoric
call "%~dp0loto_git_sync.bat" push_istoric
goto :eof


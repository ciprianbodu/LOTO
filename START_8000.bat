@echo off
setlocal DisableDelayedExpansion
REM ============================================================
REM START_8000.bat — Launcher rapid + log silent in fundal.
REM Pe success: nu mai vezi nimic despre log, pornesti direct NiceGUI (app_nicegui.py).
REM Pe eroare: afisez log-ul automat si las fereastra deschisa.
REM CRLF obligatoriu (.gitattributes). Linie goala = echo/ nu echo.
REM In echo din blocuri if (...): fara paranteze rotunde.
REM ============================================================
if /I "%~1"=="--bootstrap-sync" goto :bootstrap_sync
if /I "%~1"=="--post-sync" goto :post_sync

set "PROJECT_DIR=%~dp0"
set "BOOT_DIR=%TEMP%\loto-start-%RANDOM%-%RANDOM%"
mkdir "%BOOT_DIR%" >nul 2>&1 || goto :bootstrap_failed
copy /Y "%~f0" "%BOOT_DIR%\START_8000.bat" >nul || goto :bootstrap_failed
copy /Y "%~dp0loto_git_sync.bat" "%BOOT_DIR%\loto_git_sync.bat" >nul || goto :bootstrap_failed
REM FARA CALL: fisierul din repo poate fi inlocuit in siguranta de git reset.
"%BOOT_DIR%\START_8000.bat" --bootstrap-sync "%PROJECT_DIR%" "%BOOT_DIR%"
exit /b 99

:bootstrap_failed
echo [GIT] Nu pot crea bootstrap-ul temporar - continui fara auto-update.
if not "%BOOT_DIR%"=="" rmdir /s /q "%BOOT_DIR%" >nul 2>&1
goto :main

:bootstrap_sync
set "PROJECT_DIR=%~2"
set "BOOT_DIR=%~3"
cd /d "%PROJECT_DIR%"
where git >nul 2>&1
if errorlevel 1 (
    echo [GIT] git negasit - sar peste auto-update.
) else (
    call "%BOOT_DIR%\loto_git_sync.bat" autoupdate "%PROJECT_DIR%"
)
REM Ruleaza launcherul NOU din repo; nu continua copia veche.
"%PROJECT_DIR%START_8000.bat" --post-sync "%PROJECT_DIR%" "%BOOT_DIR%"
exit /b 98

:post_sync
set "PROJECT_DIR=%~2"
set "BOOT_DIR=%~3"
cd /d "%PROJECT_DIR%"
if not "%BOOT_DIR%"=="" rmdir /s /q "%BOOT_DIR%" >nul 2>&1

:main
if "%PROJECT_DIR%"=="" set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"
set "LOGFILE=%PROJECT_DIR%startup_8000.log"

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


:push_istoric
call "%~dp0loto_git_sync.bat" push_istoric
goto :eof


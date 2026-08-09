@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

set VENV_DIR=D:\_BUILD\_LOTO\.venv
set VENV_PY=%VENV_DIR%\Scripts\python.exe
set SITE_PACKAGES=%VENV_DIR%\Lib\site-packages
set REQ_SNAPSHOT=requirements_snapshot.txt

echo ============================================================
echo   ACTUALIZARE MEDIU LOTO ENTERPRISE
echo   Venv vizat: %VENV_DIR%
echo ============================================================
echo.

REM Asigura directorul de build (in afara OneDrive) exista.
if not exist "D:\_BUILD\_LOTO" mkdir "D:\_BUILD\_LOTO"

REM Curatare resturi din versiuni vechi (backup-uri venv cu sufix, orice _backup,
REM plus venv-ul vechi relativ .venv din folderul de proiect — acum mutat la D:\_BUILD\_LOTO\.venv).
for /d %%D in (".venv_ALF-LUPTATORI_backup" ".venv_backup" ".venv_ALF-LUPTATORI" ".venv") do (
    if exist "%%D" (
        echo [CLEANUP] Sterg folder vechi: %%D
        rmdir /s /q "%%D" >nul 2>&1
    )
)

REM ===== Auto-update COD din GitHub (main) inainte de a actualiza mediul =====
REM Asa, cand dai ACTUALIZARI.bat primesti si ultimul cod, si ultimele librarii.
where git >nul 2>&1
if errorlevel 1 (
    echo [GIT] git negasit - sar peste auto-update cod.
) else (
    call :git_autoupdate
)
echo.

if not exist "%VENV_PY%" (
    echo [INFO] Venv lipsa la %VENV_DIR% — il creez acum cu py -3.14...
    if not exist "D:\_BUILD\_LOTO" mkdir "D:\_BUILD\_LOTO"
    py -3.14 -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [EROARE] Creare venv esuata. Verifica ca Python 3.14 e instalat ^(py -3.14 --version^).
        pause
        exit /b 1
    )
    echo [OK] Venv creat la %VENV_DIR%.
)

REM ============================================================
REM [-1/4] Detectie Python: daca exista un 3.14.x mai nou decat
REM cel din venv, oferim upgrade. Skip altfel.
REM ============================================================
echo [-1/4] Detectie versiune Python venv vs sistem...
for /f "tokens=2 delims= " %%V in ('"%VENV_PY%" --version 2^>^&1') do set VENV_VER=%%V
for /f "tokens=2 delims= " %%V in ('py -3.14 --version 2^>^&1') do set SYS_VER=%%V
REM Daca interpretorul 3.14 lipseste de pe sistem, il INSTALAM automat (winget/python.org).
if "%SYS_VER%"=="" call :ensure_python314
if "%SYS_VER%"=="" goto :skip_python_upgrade
echo   Venv:    %VENV_VER%
echo   Sistem:  %SYS_VER%

if "%VENV_VER%"=="%SYS_VER%" (
    echo   [OK] Acelasi patch — niciun upgrade Python necesar.
    echo.
    goto :skip_python_upgrade
)

echo.
echo   [INFO] Versiune Python diferita detectata pe sistem.
echo          Doresti sa migrezi venv-ul de la %VENV_VER% la %SYS_VER%?
echo          (Recreeaza venv-ul + reinstaleaza CURAT din requirements; FARA backup venv.)
echo.
choice /C YN /M "Upgrade Python venv "
if errorlevel 2 goto :skip_python_upgrade

echo.
echo   Migrare in curs ...

REM Kill TOATE procesele care folosesc venv-ul vechi
taskkill /F /T /IM streamlit.exe >nul 2>&1
powershell -NoProfile -Command "$venv='%VENV_DIR%'; Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | Where-Object { $_.ExecutablePath -and ($_.ExecutablePath -like \"*$venv*\") } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
timeout /t 3 /nobreak >nul

REM Snapshot pachete instalate
echo   Snapshot pachete -^> %REQ_SNAPSHOT%
"%VENV_PY%" -m pip freeze > "%REQ_SNAPSHOT%"

REM Sterg venv-ul vechi DIRECT (fara backup - ai cerut sa nu mai ramana .._backup).
REM Snapshot-ul de mai sus + reinstall-ul recreeaza acelasi mediu in venv-ul nou.
echo   Sterg venv vechi: %VENV_DIR%
rmdir /s /q "%VENV_DIR%"
if exist "%VENV_DIR%" (
    echo   [EROARE] Nu pot sterge venv-ul vechi ^(procese active inca?^). Abandonez upgrade.
    goto :skip_python_upgrade
)

REM Creez venv nou cu cea mai noua 3.14.x
py -3.14 -m venv "%VENV_DIR%"
if errorlevel 1 (
    echo   [EROARE] Creare venv nou esuata. Ruleaza din nou ACTUALIZARI.bat.
    goto :skip_python_upgrade
)
"%VENV_PY%" --version

echo.
echo   Upgrade pip in venv-ul nou. Pachetele se instaleaza mai jos, CURAT,
echo   din requirements_base.txt (exclusiv CPU).
"%VENV_PY%" -m pip install --upgrade pip --quiet

echo.
echo   [OK] Venv %SYS_VER% creat (gol). Pachetele se instaleaza in pasii [1b]+.
echo        Snapshot vechi pastrat ca referinta: %REQ_SNAPSHOT%.
echo.

:skip_python_upgrade

REM ============================================================
REM [0/4] Kill procese venv + cleanup
REM ============================================================
echo [0/4] KILL Streamlit + worker + python.exe din venv...
taskkill /F /T /IM streamlit.exe >nul 2>&1
powershell -NoProfile -Command "$venv='%VENV_DIR%'; Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | Where-Object { $_.ExecutablePath -and ($_.ExecutablePath -like \"*$venv*\") } | ForEach-Object { Write-Host ('  - {0} PID {1} oprit' -f $_.Name, $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

echo.
echo Astept 5s pentru release DLL-uri...
timeout /t 5 /nobreak >nul

echo.
echo [0b/4] Curatare ghost-uri pip ('~xxx') din site-packages (runda 1)...
call :CleanGhosts
timeout /t 2 /nobreak >nul
echo [0b/4] Runda 2 (capturare DLL-uri eliberate intre timp)...
call :CleanGhosts
echo.

echo [0c/4] Verificare INTEGRITATE Python + librarii (OneDrive poate corupe .dll/.pyd)...
call :check_integrity
echo.

echo [1/4] Pip upgrade + pachete benchmark...
"%VENV_PY%" -m pip install --upgrade pip --quiet
echo.

echo [1b] Install pachete din requirements_base.txt (exclusiv CPU)...
if not exist "requirements_base.txt" (
    echo   [WARN] requirements_base.txt lipseste — sar peste.
) else (
    "%VENV_PY%" -m pip install --prefer-binary --upgrade-strategy only-if-needed -r requirements_base.txt
    if errorlevel 1 (
        echo   [ATENTIE] Install partial a esuat. Continui.
        echo.
    ) else (
        echo   [OK] Pachete instalate / actualizate.
    )
)

echo.
echo [1c] Curatare ghost-uri post-install (3 runde cu wait)...
timeout /t 3 /nobreak >nul
call :CleanGhosts
timeout /t 2 /nobreak >nul
call :CleanGhosts
timeout /t 2 /nobreak >nul
call :CleanGhosts
echo.

echo [2/4] Verificare mediu (CPU): metode statistice/ML + assets benchmark...
"%VENV_PY%" verifica_mediu.py
echo.

echo [2b/4] Descarcare extrageri noi din loto49.ro...
set "UPDATE_LOG=%TEMP%\loto_update_%RANDOM%.log"
"%VENV_PY%" "%~dp0update_csv.py" > "%UPDATE_LOG%" 2>&1
powershell -NoProfile -Command "$log='%UPDATE_LOG%'; Get-Content $log | ForEach-Object { if ($_ -match 'extrageri noi') { Write-Host $_ -ForegroundColor Green } else { Write-Host $_ } }"
findstr /C:"EROARE" "%UPDATE_LOG%" >nul 2>&1
if not errorlevel 1 (
    echo [WARN] update_csv.py a intampinat erori ^(offline?^) - continui cu istoricul existent.
)
del "%UPDATE_LOG%" >nul 2>&1

REM Auto-commit + push extrageri noi din _ISTORIC pe GitHub (best-effort).
where git >nul 2>&1
if not errorlevel 1 call :push_istoric
echo.

REM Cache WF vechi (alt CACHE_VERSION) in bench_results/ - inaccesibil, umfla OneDrive.
REM Doar stale: purge_stale_wf_cache (NU clear_walk_forward_cache - pastreaza versiunea curenta).
echo [2c/4] Curatare cache walk-forward stale (versiuni vechi CACHE_VERSION)...
"%VENV_PY%" -c "import sys; sys.path.insert(0, '.'); from loto_enterprise.core.walk_forward_adapter import purge_stale_wf_cache, CACHE_VERSION; r=purge_stale_wf_cache(dry_run=False); print('  CACHE_VERSION curenta:', CACHE_VERSION); print('  Fisiere stale gasite:', r.get('n_files', 0), '('+str(r.get('mb', 0))+' MB)'); print('  Sterse efectiv:', r.get('n_deleted', 0))" 2>nul
if errorlevel 1 (
    echo   [WARN] Purge cache WF esuat - continui ^(import/disk?^).
)
echo.

echo [3/4] Verificare freshness best_methods.json...
"%VENV_PY%" -c "import sys; sys.path.insert(0, '.'); from loto_enterprise.benchmark.freshness import check_freshness, aggregate_recommendation; r = check_freshness(); print('Overall recommendation:', aggregate_recommendation(r)); [print(f'  {gk}: {rep.status} (delta {rep.row_delta_pct:.1f}%%)') for gk, rep in r.items()]" 2>nul
if errorlevel 1 (
    echo [WARN] Freshness check esuat - probabil benchmark nu a rulat inca.
    echo        Ruleaza: %VENV_PY% bench_all_methods.py
    echo.
)

echo.
echo [4/4] Curatare finala ghost-uri si recomandari
timeout /t 2 /nobreak >nul
call :CleanGhosts
echo.

echo ------------------------------------------------------------
echo  Python: ACTUALIZARI.bat instaleaza/migreaza automat la 3.14 (winget sau
echo    installer python.org). Daca instalarea auto esueaza, ia-l manual de la
echo    https://www.python.org/downloads/  (bifeaza Add to PATH) si reruleaza.
echo.
echo  Daca freshness recomanda re-bench:
echo    Re-Bench Full: din UI (butonul portocaliu) sau %VENV_PY% bench_all_methods.py
echo  Pentru a porni aplicatia:  START_8000.bat
echo ------------------------------------------------------------
echo.
pause
endlocal
exit /b 0


:git_autoupdate
REM ============================================================
REM Auto-update ROBUST din main (acelasi pattern ca START_8000.bat).
REM Datele tale (best_methods.json, _ISTORIC, venv) sunt
REM gitignore -> NU se pierd la reset. Modificarile locale urmarite -> in stash.
REM ============================================================
echo [GIT] Verific actualizari cod de pe GitHub ^(main^)...
REM OneDrive strica scrierea atomica in .git -> dezactivam appendAtomically.
git config windows.appendAtomically false >nul 2>&1
git fetch origin main --quiet 2>nul
if errorlevel 1 (
    echo [GIT] Offline / fetch esuat - continui cu codul curent.
    goto :eof
)
git merge --ff-only origin/main >nul 2>&1
if not errorlevel 1 (
    echo [GIT] Cod la zi cu main.
    goto :eof
)
echo [GIT] Fast-forward imposibil ^(divergenta / modificari locale^). Stare:
git status -sb
echo [GIT] Sincronizez FORTAT cu main ^(backup local in stash^)...
git stash push -m "auto-backup ACTUALIZARI" >nul 2>&1
git reset --hard origin/main >nul 2>&1
if errorlevel 1 (
    echo [GIT] Sincronizare fortata esuata - continui cu codul curent.
) else (
    echo [GIT] Sincronizat la zi cu main. Backup local: ruleaza 'git stash list'.
)
goto :eof


:push_istoric
REM ============================================================
REM Auto-commit + push al extragerilor noi din _ISTORIC/ (best-effort).
REM Ruleaza DUPA update_csv.py. Daca nu sunt modificari -> nimic. Daca push-ul
REM esueaza (offline) -> commit-ul ramane local si se reincearca data viitoare.
REM ============================================================
git config windows.appendAtomically false >nul 2>&1
git status --porcelain _ISTORIC 2>nul | findstr /R "." >nul 2>&1
if errorlevel 1 (
    echo [GIT] _ISTORIC fara modificari - nimic de comis.
    goto :eof
)
echo [GIT] Extrageri noi in _ISTORIC - commit + push pe GitHub...
git add _ISTORIC >nul 2>&1
git commit -m "auto: update istoric extrageri (%DATE%)" >nul 2>&1
git push origin HEAD >nul 2>&1
if errorlevel 1 (
    echo [GIT] Push _ISTORIC esuat ^(offline?^) - se reincearca la urmatoarea rulare.
) else (
    echo [GIT] _ISTORIC pushat pe GitHub.
)
goto :eof


:CleanGhosts
set GHOSTS=0
if exist "%SITE_PACKAGES%" (
    for /d %%G in ("%SITE_PACKAGES%\~*") do (
        rmdir /s /q "%%G" 2>nul
        if not exist "%%G" (
            echo   Sters ghost: %%~nxG
            set /a GHOSTS+=1
        )
    )
)
if !GHOSTS! EQU 0 (
    echo   [OK] Fara ghost-uri pip.
) else (
    echo   [OK] Sterse !GHOSTS! ghost-uri pip.
)
exit /b 0


:check_integrity
REM Integritate venv: Python ruleaza? dependinte coerente (pip check)? librariile
REM critice se importa (prinde coruptie binara .dll/.pyd de la sync OneDrive)?
"%VENV_PY%" -c "import sys; print('  Python venv:', sys.version.split()[0])" 2>nul
if errorlevel 1 (
    echo   [EROARE] Python din venv NU ruleaza - corupt/incomplet.
    echo            Sterge venv-ul si reruleaza START_8000.bat: rmdir /s /q "%VENV_DIR%"
    goto :eof
)
echo   - pip check ^(dependinte lipsa/incompatibile = install corupt^)...
"%VENV_PY%" -m pip check
if errorlevel 1 (
    echo   [ATENTIE] pip check a gasit probleme - le repara pasii [1b]+ ^(reinstall^).
) else (
    echo   [OK] Dependinte coerente.
)
echo   - smoke test import librarii critice ^(CPU^)...
"%VENV_PY%" -c "import numpy,pandas,scipy,numba,nicegui" 2>nul
if errorlevel 1 (
    echo   [ATENTIE] O librarie critica NU se importa - posibil corupta ^(OneDrive^)
    echo            SAU prima instalare ^(normal - se instaleaza la pasii [1b]+^).
    echo            Daca persista dupa install: %VENV_PY% -m pip install --force-reinstall ^<pachet^>
) else (
    echo   [OK] Librarii critice importate curat ^(numpy/pandas/scipy/numba/nicegui^).
)
goto :eof


:ensure_python314
REM ============================================================
REM Instaleaza AUTOMAT interpretorul Python 3.14 daca "py -3.14" lipseste.
REM Intai winget (Windows 10 1709+/11); daca nu exista, descarca installer-ul
REM oficial python.org si il ruleaza silentios (user-scope, Add to PATH, py launcher).
REM La final re-detecteaza si seteaza SYS_VER (vizibil in fluxul principal).
REM ============================================================
echo.
echo   [INFO] Python 3.14 nu e instalat pe sistem ^(py -3.14 indisponibil^).
echo          Incerc instalare automata...
where winget >nul 2>&1
if errorlevel 1 goto :ep_download
echo   winget install -e --id Python.Python.3.14 --silent ...
winget install -e --id Python.Python.3.14 --silent --accept-package-agreements --accept-source-agreements
goto :ep_verify

:ep_download
echo   winget indisponibil — descarc installer-ul oficial python.org ^(3.14.6^)...
set "PY_URL=https://www.python.org/ftp/python/3.14.6/python-3.14.6-amd64.exe"
set "PY_EXE=%TEMP%\python-3.14.6-amd64.exe"
powershell -NoProfile -Command "try { Invoke-WebRequest -Uri '%PY_URL%' -OutFile '%PY_EXE%' -UseBasicParsing } catch { exit 1 }"
if not exist "%PY_EXE%" (
    echo   [EROARE] Descarcare installer esuata ^(verifica conexiunea^).
    goto :ep_verify
)
echo   Instalare silentioasa ^(user-scope, PrependPath, py launcher^)...
"%PY_EXE%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1
del "%PY_EXE%" >nul 2>&1

:ep_verify
set "SYS_VER="
for /f "tokens=2 delims= " %%V in ('py -3.14 --version 2^>^&1') do set SYS_VER=%%V
if "%SYS_VER%"=="" (
    echo   [EROARE] py -3.14 inca indisponibil dupa instalare.
    echo           Inchide/redeschide terminalul si reruleaza ACTUALIZARI.bat, sau
    echo           instaleaza manual de la https://www.python.org/downloads/ ^(bifeaza Add to PATH^).
) else (
    echo   [OK] Python instalat: %SYS_VER%
)
goto :eof

@echo off
setlocal DisableDelayedExpansion

REM Git poate inlocui chiar acest .bat. CMD reia un CALL la offsetul vechi din
REM fisierul nou si executa fragmente de linie ca comenzi. De aceea sincronizarea
REM ruleaza din copii IMUTABILE in TEMP, apoi transfera controlul la scriptul nou.
if /I "%~1"=="--bootstrap-sync" goto :bootstrap_sync
if /I "%~1"=="--post-sync" goto :post_sync

set "PROJECT_DIR=%~dp0"
set "BOOT_DIR=%TEMP%\loto-update-%RANDOM%-%RANDOM%"
mkdir "%BOOT_DIR%" >nul 2>&1 || goto :bootstrap_failed
copy /Y "%~f0" "%BOOT_DIR%\ACTUALIZARI.bat" >nul || goto :bootstrap_failed
copy /Y "%~dp0loto_git_sync.bat" "%BOOT_DIR%\loto_git_sync.bat" >nul || goto :bootstrap_failed
REM FARA CALL: contextul fisierului din repo trebuie abandonat inainte de git reset.
"%BOOT_DIR%\ACTUALIZARI.bat" --bootstrap-sync "%PROJECT_DIR%" "%BOOT_DIR%"
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
    echo [GIT] git negasit - sar peste auto-update cod.
) else (
    call "%BOOT_DIR%\loto_git_sync.bat" autoupdate "%PROJECT_DIR%"
)
REM FARA CALL: ruleaza versiunea NOUA descarcata, nu copia veche din TEMP.
"%PROJECT_DIR%ACTUALIZARI.bat" --post-sync "%PROJECT_DIR%" "%BOOT_DIR%"
exit /b 98

:post_sync
set "PROJECT_DIR=%~2"
set "BOOT_DIR=%~3"
cd /d "%PROJECT_DIR%"
if not "%BOOT_DIR%"=="" rmdir /s /q "%BOOT_DIR%" >nul 2>&1

:main
if "%PROJECT_DIR%"=="" set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"
setlocal enabledelayedexpansion

set VENV_DIR=D:\_BUILD\_LOTO\.venv
set VENV_PY=%VENV_DIR%\Scripts\python.exe
set SITE_PACKAGES=%VENV_DIR%\Lib\site-packages
set REQ_SNAPSHOT=requirements_snapshot.txt

echo ============================================================
echo   ACTUALIZARE MEDIU LOTO ENTERPRISE
echo   Venv vizat: %VENV_DIR%
echo ============================================================
echo/

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

REM Asigura ULTIMUL patch stabil Python 3.14.x inainte de a crea/compara venv-ul.
REM Detectia online vine de pe python.org; winget face upgrade-ul cand e disponibil,
REM iar fallback-ul descarca dinamic installer-ul acelei versiuni (fara patch hardcodat).
call :ensure_latest_python314
if "%SYS_VER%"=="" (
    echo [EROARE] Python 3.14 nu poate fi instalat/detectat. Oprire.
    pause
    exit /b 1
)

if not exist "%VENV_PY%" (
    echo [INFO] Venv lipsa la %VENV_DIR% — il creez acum cu py -3.14...
    if not exist "D:\_BUILD\_LOTO" mkdir "D:\_BUILD\_LOTO"
    py -3.14 -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [EROARE] Creare venv esuata. Verifica ca Python 3.14 e instalat: py -3.14 --version.
        pause
        exit /b 1
    )
    echo [OK] Venv creat la %VENV_DIR%.
)

REM ============================================================
REM [-1/4] Detectie Python: daca patch-ul de sistem (actualizat mai sus la
REM ultimul 3.14.x disponibil) difera de venv, recream venv-ul automat.
REM ============================================================
echo [-1/4] Detectie versiune Python venv vs sistem...
for /f "tokens=2 delims= " %%V in ('"%VENV_PY%" --version 2^>^&1') do set VENV_VER=%%V
for /f "tokens=2 delims= " %%V in ('py -3.14 --version 2^>^&1') do set SYS_VER=%%V
if "%SYS_VER%"=="" goto :skip_python_upgrade
echo   Venv:    %VENV_VER%
echo   Sistem:  %SYS_VER%

if "%VENV_VER%"=="%SYS_VER%" (
    echo   [OK] Acelasi patch — niciun upgrade Python necesar.
    echo/
    goto :skip_python_upgrade
)

echo/
echo   [INFO] Versiune Python noua detectata pe sistem.
echo          Migrez automat venv-ul de la %VENV_VER% la %SYS_VER%.
echo          Recreeaza venv-ul + reinstaleaza CURAT din requirements; FARA backup venv.
echo/
echo   Migrare in curs ...

REM Kill TOATE procesele care folosesc venv-ul vechi
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
    echo   [EROARE] Nu pot sterge venv-ul vechi - procese active inca? Abandonez upgrade.
    goto :skip_python_upgrade
)

REM Creez venv nou cu cea mai noua 3.14.x
py -3.14 -m venv "%VENV_DIR%"
if errorlevel 1 (
    echo   [EROARE] Creare venv nou esuata. Ruleaza din nou ACTUALIZARI.bat.
    goto :skip_python_upgrade
)
"%VENV_PY%" --version

echo/
echo   Upgrade pip in venv-ul nou. Pachetele se instaleaza mai jos, CURAT,
echo   din requirements_base.txt - exclusiv CPU.
"%VENV_PY%" -m pip install --upgrade pip --quiet

echo/
echo   [OK] Venv %SYS_VER% creat - gol. Pachetele se instaleaza in pasii [1b]+.
echo        Snapshot vechi pastrat ca referinta: %REQ_SNAPSHOT%.
echo/

:skip_python_upgrade

REM ============================================================
REM [0/4] Kill procese venv + cleanup
REM ============================================================
echo [0/4] KILL worker/UI + python.exe din venv...
powershell -NoProfile -Command "$venv='%VENV_DIR%'; Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | Where-Object { $_.ExecutablePath -and ($_.ExecutablePath -like \"*$venv*\") } | ForEach-Object { Write-Host ('  - {0} PID {1} oprit' -f $_.Name, $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

echo/
echo Astept 5s pentru release DLL-uri...
timeout /t 5 /nobreak >nul

echo/
echo [0b/4] Curatare ghost-uri pip '~xxx' din site-packages - runda 1...
call :CleanGhosts
timeout /t 2 /nobreak >nul
echo [0b/4] Runda 2 - capturare DLL-uri eliberate intre timp...
call :CleanGhosts
echo/

echo [0c/4] Verificare INTEGRITATE Python + librarii - OneDrive poate corupe .dll/.pyd...
call :check_integrity
echo/

echo [1/4] Pip upgrade + pachete benchmark...
"%VENV_PY%" -m pip install --upgrade pip --quiet
echo/

echo [1b] Install pachete din requirements_base.txt - exclusiv CPU...
if not exist "requirements_base.txt" (
    echo   [WARN] requirements_base.txt lipseste — sar peste.
) else (
    "%VENV_PY%" -m pip install --prefer-binary --upgrade-strategy only-if-needed -r requirements_base.txt
    if errorlevel 1 (
        echo   [ATENTIE] Install partial a esuat. Continui.
        echo/
    ) else (
        echo   [OK] Pachete instalate / actualizate.
    )
)

echo/
echo [1c] Curatare ghost-uri post-install - 3 runde cu wait...
timeout /t 3 /nobreak >nul
call :CleanGhosts
timeout /t 2 /nobreak >nul
call :CleanGhosts
timeout /t 2 /nobreak >nul
call :CleanGhosts
echo/

echo [2/4] Verificare mediu CPU: metode statistice/ML + assets benchmark...
"%VENV_PY%" verifica_mediu.py
echo/

echo [2b/4] Descarcare extrageri noi din loto49.ro...
set "UPDATE_LOG=%TEMP%\loto_update_%RANDOM%.log"
"%VENV_PY%" "%~dp0update_csv.py" > "%UPDATE_LOG%" 2>&1
powershell -NoProfile -Command "$log='%UPDATE_LOG%'; Get-Content $log | ForEach-Object { if ($_ -match 'extrageri noi') { Write-Host $_ -ForegroundColor Green } else { Write-Host $_ } }"
findstr /C:"EROARE" "%UPDATE_LOG%" >nul 2>&1
if not errorlevel 1 (
    echo [WARN] update_csv.py a intampinat erori - offline? Continui cu istoricul existent.
)
del "%UPDATE_LOG%" >nul 2>&1

REM Auto-commit + push extrageri noi din _ISTORIC pe GitHub (best-effort).
where git >nul 2>&1
if not errorlevel 1 call :push_istoric
echo/

REM Cache WF vechi (alt CACHE_VERSION) in bench_results/ - inaccesibil, umfla OneDrive.
REM Doar stale: purge_stale_wf_cache (NU clear_walk_forward_cache - pastreaza versiunea curenta).
echo [2c/4] Curatare cache walk-forward stale - versiuni vechi CACHE_VERSION...
"%VENV_PY%" -c "import sys; sys.path.insert(0, '.'); from loto_enterprise.core.walk_forward_adapter import purge_stale_wf_cache, CACHE_VERSION; r=purge_stale_wf_cache(dry_run=False); print('  CACHE_VERSION curenta:', CACHE_VERSION); print('  Fisiere stale gasite:', r.get('n_files', 0), '('+str(r.get('mb', 0))+' MB)'); print('  Sterse efectiv:', r.get('n_deleted', 0))" 2>nul
if errorlevel 1 (
    echo   [WARN] Purge cache WF esuat - continui, import/disk?
)
echo/

echo [3/4] Verificare freshness best_methods.json...
"%VENV_PY%" -c "import sys; sys.path.insert(0, '.'); from loto_enterprise.benchmark.freshness import check_freshness, aggregate_recommendation; r = check_freshness(); print('Overall recommendation:', aggregate_recommendation(r)); [print(f'  {gk}: {rep.status} (delta {rep.row_delta_pct:.1f}%%)') for gk, rep in r.items()]" 2>nul
if errorlevel 1 (
    echo [WARN] Freshness check esuat - probabil benchmark nu a rulat inca.
    echo        Ruleaza: %VENV_PY% bench_all_methods.py
    echo/
)

echo/
echo [4/4] Curatare finala ghost-uri si recomandari
timeout /t 2 /nobreak >nul
call :CleanGhosts
echo/

echo ------------------------------------------------------------
echo  Python: ACTUALIZARI.bat instaleaza/migreaza automat la ultimul 3.14.x stabil
echo    prin winget sau installer python.org. Daca instalarea auto esueaza, ia-l de la
echo    https://www.python.org/downloads/  - bifeaza Add to PATH - si reruleaza.
echo/
echo  Daca freshness recomanda re-bench:
echo    Re-Bench Full: din UI - butonul portocaliu - sau %VENV_PY% bench_all_methods.py
echo  Pentru a porni aplicatia:  START_8000.bat
echo ------------------------------------------------------------
echo/
pause
endlocal
exit /b 0


:push_istoric
call "%~dp0loto_git_sync.bat" push_istoric
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
echo   - pip check - dependinte lipsa/incompatibile = install corupt...
"%VENV_PY%" -m pip check
if errorlevel 1 (
    echo   [ATENTIE] pip check a gasit probleme - le repara pasii [1b]+ reinstall.
) else (
    echo   [OK] Dependinte coerente.
)
echo   - smoke test import librarii critice - CPU...
"%VENV_PY%" -c "import numpy,pandas,scipy,sklearn,statsmodels,nicegui" 2>nul
if errorlevel 1 (
    echo   [ATENTIE] O librarie critica NU se importa - posibil corupta OneDrive
    echo            SAU prima instalare - normal, se instaleaza la pasii [1b]+.
    echo            Daca persista dupa install: %VENV_PY% -m pip install --force-reinstall ^<pachet^>
) else (
    echo   [OK] Librarii critice importate curat - numpy/pandas/scipy/sklearn/statsmodels/nicegui.
)
goto :eof


:ensure_latest_python314
REM ============================================================
REM Instaleaza/actualizeaza AUTOMAT la ultimul patch stabil Python 3.14.x.
REM  1. python.org/ftp este sursa versiunii curente (fara patch hardcodat).
REM  2. winget instaleaza/actualizeaza pachetul cand este disponibil.
REM  3. Daca winget lipseste sau nu gestioneaza instalarea existenta, descarcam
REM     dinamic installer-ul oficial al versiunii detectate.
REM Offline: pastreaza runtime-ul existent; o instalare noua necesita retea.
REM La final seteaza SYS_VER pentru fluxul principal.
REM ============================================================
echo/
set "SYS_VER="
for /f "tokens=2 delims= " %%V in ('py -3.14 --version 2^>^&1') do set "SYS_VER=%%V"
set "PY_LATEST="
set "PY_VER_FILE=%TEMP%\loto-python-3.14-latest.version"
del "%PY_VER_FILE%" >nul 2>&1

echo   [PYTHON] Verific ultimul patch stabil 3.14.x pe python.org...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop';" ^
  "$r=Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/' -UseBasicParsing;" ^
  "$v=[regex]::Matches($r.Content,'3\.14\.\d+/') ^| ForEach-Object {$_.Value.TrimEnd('/')} ^| Sort-Object {[version]$_} -Descending -Unique ^| Select-Object -First 1;" ^
  "if(-not $v){throw 'Nu am gasit nicio versiune 3.14.x'};" ^
  "Set-Content -LiteralPath '%PY_VER_FILE%' -Value $v -NoNewline -Encoding ascii" >nul 2>&1
if exist "%PY_VER_FILE%" set /p PY_LATEST=<"%PY_VER_FILE%"
del "%PY_VER_FILE%" >nul 2>&1

if not "!PY_LATEST!"=="" (
    echo   [PYTHON] Sistem: !SYS_VER! ^| online: !PY_LATEST!
    if "!SYS_VER!"=="!PY_LATEST!" (
        echo   [OK] Python !SYS_VER! este deja ultimul 3.14.x stabil.
        goto :eof
    )
) else (
    echo   [ATENTIE] Nu pot determina versiunea online. Incerc winget.
)

REM Oprim doar procesele venv-ului proiectului inainte de upgrade-ul runtime.
powershell -NoProfile -Command "$venv='%VENV_DIR%'; Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | Where-Object { $_.ExecutablePath -and ($_.ExecutablePath -like \"*$venv*\") } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
timeout /t 2 /nobreak >nul

where winget >nul 2>&1
if errorlevel 1 goto :ep_download_latest
if "!SYS_VER!"=="" (
    echo   [PYTHON] winget install Python.Python.3.14...
    winget install -e --id Python.Python.3.14 --silent --accept-package-agreements --accept-source-agreements
) else (
    echo   [PYTHON] winget upgrade Python.Python.3.14...
    winget upgrade -e --id Python.Python.3.14 --silent --accept-package-agreements --accept-source-agreements
)
set "SYS_VER="
for /f "tokens=2 delims= " %%V in ('py -3.14 --version 2^>^&1') do set "SYS_VER=%%V"
if "!PY_LATEST!"=="" (
    if not "!SYS_VER!"=="" (
        echo   [OK] Python disponibil dupa winget: !SYS_VER!
        goto :eof
    )
)
if "!SYS_VER!"=="!PY_LATEST!" (
    echo   [OK] Python actualizat prin winget: !SYS_VER!
    goto :eof
)

:ep_download_latest
if "!PY_LATEST!"=="" (
    if not "!SYS_VER!"=="" (
        echo   [ATENTIE] Raman pe Python !SYS_VER! - versiunea online nu poate fi verificata.
        goto :eof
    )
    echo   [EROARE] Fara winget si fara versiune online; Python 3.14 nu poate fi instalat.
    goto :eof
)
set "PY_URL=https://www.python.org/ftp/python/!PY_LATEST!/python-!PY_LATEST!-amd64.exe"
set "PY_EXE=%TEMP%\python-!PY_LATEST!-amd64.exe"
echo   [PYTHON] Descarc installer-ul oficial !PY_LATEST!...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-WebRequest -Uri '!PY_URL!' -OutFile '!PY_EXE!' -UseBasicParsing } catch { exit 1 }"
if not exist "!PY_EXE!" (
    echo   [EROARE] Descarcare installer esuata - verifica conexiunea.
    goto :ep_verify_latest
)
echo   [PYTHON] Instalare silentioasa !PY_LATEST! - user-scope + py launcher...
"!PY_EXE!" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0 SimpleInstall=1
del "!PY_EXE!" >nul 2>&1

:ep_verify_latest
set "SYS_VER="
for /f "tokens=2 delims= " %%V in ('py -3.14 --version 2^>^&1') do set "SYS_VER=%%V"
if "!SYS_VER!"=="" (
    echo   [EROARE] py -3.14 inca indisponibil dupa instalare.
    echo           Inchide/redeschide terminalul si reruleaza ACTUALIZARI.bat.
) else if not "!SYS_VER!"=="!PY_LATEST!" (
    echo   [ATENTIE] Python detectat !SYS_VER!, dar online este !PY_LATEST!.
    echo              Inchide/redeschide terminalul si reruleaza ACTUALIZARI.bat.
) else (
    echo   [OK] Python instalat/actualizat: !SYS_VER!
)
goto :eof

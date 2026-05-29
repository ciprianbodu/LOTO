@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

set VENV_DIR=.venv_%COMPUTERNAME%
set VENV_PY=%VENV_DIR%\Scripts\python.exe
set SITE_PACKAGES=%CD%\%VENV_DIR%\Lib\site-packages
set BACKUP_DIR=.venv_%COMPUTERNAME%_backup
set REQ_SNAPSHOT=requirements_snapshot.txt

echo ============================================================
echo   ACTUALIZARE MEDIU LOTO ENTERPRISE
echo   Venv vizat: %VENV_DIR%
echo ============================================================
echo.

if not exist "%VENV_PY%" (
    echo [EROARE] Mediul virtual %VENV_DIR% nu exista in proiect.
    echo Ruleaza intai START_8000.bat - va crea automat venv-ul.
    pause
    exit /b 1
)

REM ============================================================
REM [-1/4] Detectie Python: daca exista un 3.11.x mai nou decat
REM cel din venv, oferim upgrade. Skip altfel.
REM ============================================================
echo [-1/4] Detectie versiune Python venv vs sistem...
for /f "tokens=2 delims= " %%V in ('"%VENV_PY%" --version 2^>^&1') do set VENV_VER=%%V
for /f "tokens=2 delims= " %%V in ('py -3.11 --version 2^>^&1') do set SYS_VER=%%V
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
echo          (Pastreaza TOATE pachetele in versiuni identice; backup automat.)
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

REM Backup venv vechi
if exist "%BACKUP_DIR%" rmdir /s /q "%BACKUP_DIR%"
ren "%VENV_DIR%" "%BACKUP_DIR:~1%"
if errorlevel 1 (
    echo   [EROARE] Backup esuat. Abandonez upgrade.
    goto :skip_python_upgrade
)

REM Creez venv nou cu cea mai noua 3.11.x
py -3.11 -m venv "%VENV_DIR%"
if errorlevel 1 (
    echo   [EROARE] Creare venv nou esuata. Restore backup...
    rmdir /s /q "%VENV_DIR%" 2>nul
    ren "%BACKUP_DIR%" "%VENV_DIR:~1%"
    goto :skip_python_upgrade
)
"%VENV_PY%" --version

echo.
echo   Reinstall pip + pachete IDENTIC din snapshot (poate dura 5-15 min)...
"%VENV_PY%" -m pip install --upgrade pip --quiet
"%VENV_PY%" -m pip install --prefer-binary -r "%REQ_SNAPSHOT%"
if errorlevel 1 (
    echo   [ATENTIE] Reinstall partial esuat. Backup pastrat la %BACKUP_DIR%.
    echo   Revert: rmdir /s /q "%VENV_DIR%" ^& ren "%BACKUP_DIR%" "%VENV_DIR:~1%"
    pause
    exit /b 1
)

echo.
echo   [OK] Upgrade Python complet. Backup: %BACKUP_DIR% (sterge dupa verificare).
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

echo [1/4] Pip upgrade + pachete benchmark...
"%VENV_PY%" -m pip install --upgrade pip --quiet
echo.

echo [1a] Detectare pachete benchmark deja instalate (skip daca prezente)...
set "TO_INSTALL="
for %%P in (nicegui rich pynvml chronos-forecasting momentfm neuralforecast utilsforecast) do (
    "%VENV_PY%" -m pip show %%P >nul 2>&1
    if errorlevel 1 (
        set "TO_INSTALL=!TO_INSTALL! %%P"
        echo   - LIPSA %%P, va fi instalat
    ) else (
        echo   - OK %%P deja prezent
    )
)

if defined TO_INSTALL (
    echo.
    echo Instalez pachetele lipsa:!TO_INSTALL!
    "%VENV_PY%" -m pip install --prefer-binary --no-deps !TO_INSTALL!
    if errorlevel 1 (
        echo [ATENTIE] Install partial esuat. Aplicatia continua cu pachetele existente.
        echo.
    )
) else (
    echo   Toate pachetele benchmark sunt deja instalate.
)

echo.
echo [1b] Update minor pe pachete sigure (nicegui, streamlit, requests, psutil, numba)...
"%VENV_PY%" -m pip install --prefer-binary --upgrade-strategy only-if-needed --upgrade nicegui streamlit requests psutil numba 2>nul
if errorlevel 1 (
    echo [ATENTIE] Update minor a esuat partial. Continui.
    echo.
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

echo [2/4] Verificare PyTorch + CUDA + foundation + NeuralForecast...
"%VENV_PY%" verifica_mediu.py
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
echo  Pentru upgrade Python: descarca un 3.11.x mai nou de la
echo    https://www.python.org/downloads/  (bifeaza Add to PATH)
echo    apoi reruleaza ACTUALIZARI.bat — va detecta si oferi upgrade.
echo.
echo  Daca freshness e 'quick_rebench' sau 'full_rebench':
echo    Quick (~5 min):  %VENV_PY% bench_all_methods.py --quick
echo    Full  (~50 min): %VENV_PY% bench_all_methods.py
echo  Pentru a porni aplicatia:  START_8000.bat
echo  Pentru predictie CLI:      %VENV_PY% predict_with_winner.py
echo ------------------------------------------------------------
echo.
pause
endlocal
exit /b 0


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

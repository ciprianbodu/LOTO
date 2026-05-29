@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

set VENV_DIR=.venv_LUPTATORI
set VENV_PY=%VENV_DIR%\Scripts\python.exe
set SITE_PACKAGES=%CD%\%VENV_DIR%\Lib\site-packages
set BACKUP_DIR=.venv_LUPTATORI_backup
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

echo [1a] Migrare pynvml deprecat -^> nvidia-ml-py (oficial NVIDIA)...
REM PyTorch 2.5+ emite FutureWarning daca pachetul vechi 'pynvml' e instalat.
REM Pachetul oficial 'nvidia-ml-py' expune ACELASI modul Python 'pynvml',
REM deci codul (hw_sampler.py: import pynvml) ramane neschimbat.
call :MigratePynvml
echo.

echo [1a2] Detectare profil hardware...
REM Statie unica (LUPTATORI) - detectie directa prin nvidia-smi (sursa de adevar),
REM rescriem .machine_profile de fiecare data. Fara logica multi-statie/OneDrive.
set "GPU_TYPE=CPU_ONLY"
where nvidia-smi >nul 2>&1
if not errorlevel 1 (
    nvidia-smi -L >nul 2>&1
    if not errorlevel 1 (
        set "GPU_TYPE=NVIDIA"
    )
)
if /i "!GPU_TYPE!"=="NVIDIA" (
    echo   Detectie: nvidia-smi OK -^> GPU_TYPE=NVIDIA
) else (
    echo   Detectie: nvidia-smi absent / fara GPU -^> GPU_TYPE=CPU_ONLY
)
(
    echo GPU_TYPE=!GPU_TYPE!
    echo GPU_NAME=
    echo DETECTED_AT=%DATE% %TIME%
) > .machine_profile

echo.
echo [1b] Install pachete BASE (comune CPU + GPU) din requirements_base.txt...
if not exist "requirements_base.txt" (
    echo   [WARN] requirements_base.txt lipseste — sar peste.
) else (
    "%VENV_PY%" -m pip install --prefer-binary --upgrade-strategy only-if-needed -r requirements_base.txt
    if errorlevel 1 (
        echo   [ATENTIE] Install partial base a esuat. Continui.
        echo.
    ) else (
        echo   [OK] Pachete base instalate / actualizate.
    )
)

echo.
REM Folosim !GPU_TYPE! (delayed expansion) ca sa luam valoarea EFECTIV setata
REM in blocul de detectie de mai sus, nu valoarea snapshot-uita la parse time.
if /i "!GPU_TYPE!"=="NVIDIA" (
    call :InstallGpuStack
) else (
    call :InstallCpuStack
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


:MigratePynvml
REM Subrutina izolata: foloseste setlocal propriu si goto in loc de errorlevel-capture
setlocal
"%VENV_PY%" -m pip show nvidia-ml-py >nul 2>&1
if not errorlevel 1 goto :MP_HasNew

REM nvidia-ml-py LIPSESTE - verificam daca trebuie sa dezinstalam pynvml vechi
"%VENV_PY%" -m pip show pynvml >nul 2>&1
if errorlevel 1 goto :MP_InstallNew

echo   - Detectat pynvml deprecat. Dezinstalez...
"%VENV_PY%" -m pip uninstall -y pynvml >nul 2>&1

:MP_InstallNew
echo   - Instalez nvidia-ml-py (oficial NVIDIA)...
"%VENV_PY%" -m pip install --prefer-binary nvidia-ml-py
if errorlevel 1 (
    echo   [ATENTIE] Install nvidia-ml-py esuat. Telemetrie GPU optionala dezactivata.
    goto :MP_End
)
echo   [OK] nvidia-ml-py instalat (expune modul Python 'pynvml').
goto :MP_End

:MP_HasNew
echo   [OK] nvidia-ml-py deja prezent.
REM IMPORTANT: chiar daca nvidia-ml-py e instalat, pynvml vechi poate fi
REM inca prezent in venv (instalat ca dep tranzitiv sau cache). Atunci torch
REM importa modulul vechi si emite FutureWarning. Verificam si curatam.
"%VENV_PY%" -m pip show pynvml >nul 2>&1
if not errorlevel 1 (
    echo   - Detectat ^(in plus^) pynvml deprecat alaturi de nvidia-ml-py. Dezinstalez pynvml...
    "%VENV_PY%" -m pip uninstall -y pynvml >nul 2>&1
    echo   [OK] pynvml vechi sters. nvidia-ml-py ramane sursa pentru modulul Python 'pynvml'.
)

:MP_End
endlocal
exit /b 0


:InstallGpuStack
REM Instaleaza torch+cu128 + foundation models pentru masini cu GPU NVIDIA.
echo [1c] Profil GPU detectat - instalez torch+cu128 + foundation models...
echo.

REM Check daca torch existent are CUDA si build tag corect. Pip vede 2.12.0+cpu
REM si 2.12.0+cu128 ca "aceeasi versiune" si poate skip cu --upgrade, deci
REM verificam EXPLICIT build tag-ul si fortez uninstall+install daca e nevoie.
set "TORCH_BUILD="
for /f "delims=" %%V in ('"%VENV_PY%" -c "import torch; print(torch.__version__)" 2^>nul') do set "TORCH_BUILD=%%V"
echo   torch instalat acum: !TORCH_BUILD!

"%VENV_PY%" -c "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)" >nul 2>&1
if errorlevel 1 (
    echo   torch fara CUDA ^(build !TORCH_BUILD!^) - reinstalez cu cu128 wheel...
    echo   ^(~2 GB, dureaza 3-5 minute la prima rulare^)
    REM Uninstall HARD ca pip sa nu trateze 2.12.0+cpu si 2.12.0+cu128 ca "same"
    "%VENV_PY%" -m pip uninstall -y torch torchvision torchaudio >nul 2>&1
    "%VENV_PY%" -m pip install --prefer-binary torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
    if errorlevel 1 (
        echo   [EROARE] Install torch+cu128 esuat. Verifica conexiunea internet.
        echo   Manual: %VENV_PY% -m pip install torch --index-url https://download.pytorch.org/whl/cu128
        exit /b 5
    )
    REM Re-verifica dupa install
    "%VENV_PY%" -c "import torch; print('  Post-install:', torch.__version__, '| CUDA:', torch.cuda.is_available())"
) else (
    echo   [OK] torch cu CUDA deja prezent ^(build !TORCH_BUILD!^) - skip reinstall.
)

echo.
echo   Install foundation models din requirements_gpu_extras.txt...
if not exist "requirements_gpu_extras.txt" (
    echo   [WARN] requirements_gpu_extras.txt lipseste - sar peste.
) else (
    REM --no-deps: evita backtracking pe transformers ^(pin strict 4.33.3^).
    "%VENV_PY%" -m pip install --prefer-binary --no-deps -r requirements_gpu_extras.txt
    if errorlevel 1 (
        echo   [ATENTIE] Install partial foundation models. Continui.
    ) else (
        echo   [OK] Foundation models instalate.
    )
)
exit /b 0


:InstallCpuStack
REM Instaleaza torch+cpu pentru masini fara GPU NVIDIA. Stack-ul AI greu e
REM exclus deliberat - engine-ul foloseste fallback determinist pe CPU.
echo [1c] Profil CPU detectat - instalez torch+cpu ^(lean^)...
echo.

REM Check daca torch existent e fara CUDA. Daca da, skip.
"%VENV_PY%" -c "import torch; assert not torch.cuda.is_available()" >nul 2>&1
if not errorlevel 1 (
    echo   [OK] torch+cpu deja prezent — skip reinstall.
) else (
    "%VENV_PY%" -c "import torch" >nul 2>&1
    if not errorlevel 1 (
        echo   torch cu CUDA detectat pe masina FARA GPU — reinstalez cu wheel CPU
        echo   ^(economisesc ~1.8 GB + import time mai rapid^).
        "%VENV_PY%" -m pip uninstall -y torch torchvision torchaudio >nul 2>&1
    ) else (
        echo   torch lipsa - instalez torch+cpu...
    )
    "%VENV_PY%" -m pip install --prefer-binary torch --index-url https://download.pytorch.org/whl/cpu
    if errorlevel 1 (
        echo   [ATENTIE] Install torch+cpu esuat. Aplicatia merge cu fallback determinist fara torch.
    ) else (
        echo   [OK] torch+cpu instalat.
    )
)

echo.
echo   Profilul CPU EXCLUDE foundation models ^(timesfm, chronos, momentfm,
echo   neuralforecast, transformers^) - pe CPU sunt prea lente.
echo   Engine-ul foloseste fallback determinist ^(frecventa + recency + gap^).
echo   Daca vrei totusi sa testezi, instaleaza manual:
echo     %VENV_PY% -m pip install timesfm chronos-forecasting momentfm
exit /b 0

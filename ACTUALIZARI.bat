@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

:: NU mai cerem Admin: actualizam DOAR venv-ul local al proiectului,
:: care e in radacina proiectului si nu necesita drepturi elevate.

:: Folosim acelasi venv per-masina ca START_8000.bat
set VENV_DIR=.venv_%COMPUTERNAME%
set VENV_PY=%VENV_DIR%\Scripts\python.exe

echo ========================================================
echo   VERIFICARE SI ACTUALIZARE LIBRARII (LOTO APP)
echo   Mediu verificat: %VENV_DIR%
echo ========================================================
echo.

if not exist "%VENV_PY%" (
    echo [EROARE] Mediul virtual %VENV_DIR% nu exista in proiect.
    echo.
    echo Ruleaza intai START_8000.bat - va crea automat mediul si
    echo va instala dependintele din requirements.txt.
    echo.
    pause
    exit /b 1
)

:: Ruleaza scriptul de verificare CU PYTHON-UL DIN VENV
:: (in acelasi mediu pe care il foloseste si app-ul!)
"%VENV_PY%" verifica_mediu.py

echo.
pause
endlocal

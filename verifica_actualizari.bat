@echo off
:: Solicitare drepturi de Administrator in mod automat
net session >nul 2>&1
if %errorLevel% == 0 (
    goto :Admin
) else (
    echo Se solicita drepturi de Administrator pentru a putea verifica si actualiza librariile...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

:Admin
:: Schimba directorul curent la locatia acestui script
cd /d "%~dp0"
echo ========================================================
echo   VERIFICARE SI ACTUALIZARE LIBRARII (LOTO APP)
echo ========================================================
echo.

:: Ruleaza scriptul Python care face verificarile
python verifica_mediu.py

echo.
pause

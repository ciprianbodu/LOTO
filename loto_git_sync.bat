@echo off
REM ============================================================
REM loto_git_sync.bat — git helper partajat de START_8000.bat si ACTUALIZARI.bat
REM
REM   loto_git_sync.bat autoupdate     trage origin/main, checkout main
REM   loto_git_sync.bat push_istoric   commit+_ISTORIC + push origin/main
REM
REM De ce exista: :push_istoric din START_8000 mergea in startup_8000.log
REM (invizibil), inghitea erorile de commit (>nul) si facea `git push origin HEAD`
REM — daca nu erai pe main, commit-ul NU ajungea pe origin/main, iar urmatorul
REM `reset --hard origin/main` il stergea. _ISTORIC E VERSIONAT (nu e gitignore).
REM ============================================================
cd /d "%~dp0"
git config windows.appendAtomically false >nul 2>&1

if /I "%~1"=="autoupdate" goto :autoupdate
if /I "%~1"=="push_istoric" goto :push_istoric
echo [GIT] Utilizare: %~nx0 autoupdate ^| push_istoric
exit /b 2


:autoupdate
setlocal EnableExtensions EnableDelayedExpansion
echo [GIT] Verific actualizari de pe GitHub ^(main^)...
git fetch origin main --quiet 2>nul
if errorlevel 1 (
    echo [GIT] Offline / fetch esuat - pornesc cu codul curent.
    endlocal & exit /b 0
)

call :ensure_main
if errorlevel 1 (
    echo [GIT] Nu pot trece pe main - pornesc cu ramura curenta.
    endlocal & exit /b 0
)

REM Nu pierde extrageri locale necommise: copie _ISTORIC INAINTE de reset.
set "_IST_BAK="
git status --porcelain _ISTORIC 2>nul | findstr /R "." >nul 2>&1
if not errorlevel 1 (
    set "_IST_BAK=%TEMP%\loto_istoric_bak_!RANDOM!"
    echo [GIT] Salvez _ISTORIC local inainte de sync.
    xcopy /E /I /Y /Q "_ISTORIC" "!_IST_BAK!" >nul
)

git merge --ff-only origin/main >nul 2>&1
if errorlevel 1 (
    echo [GIT] Fast-forward imposibil. Stare:
    git status -sb
    echo [GIT] Sincronizez FORTAT cu origin/main ^(backup in stash^)...
    git stash push -m "auto-backup START_8000" >nul 2>&1
    git reset --hard origin/main >nul 2>&1
    if errorlevel 1 (
        echo [GIT] Sincronizare fortata esuata - pornesc cu codul curent.
    ) else (
        echo [GIT] Sincronizat la zi cu GitHub ^(main^). Backup: git stash list.
    )
) else (
    echo [GIT] Cod la zi cu GitHub ^(main^).
)

if defined _IST_BAK (
    xcopy /E /I /Y /Q "!_IST_BAK!" "_ISTORIC" >nul
    rmdir /s /q "!_IST_BAK!" >nul 2>&1
    echo [GIT] Restaurat _ISTORIC local (va fi commis de push_istoric daca e nou^).
)
endlocal & exit /b 0


:push_istoric
REM Commit + push _ISTORIC STRICT pe origin/main (acolo trage START_8000).
call :ensure_main
if errorlevel 1 (
    echo [GIT] Nu sunt pe main - NU comit _ISTORIC ^(altfel il sterge reset-ul la pornire^).
    exit /b 1
)

git status --porcelain _ISTORIC 2>nul | findstr /R "." >nul 2>&1
if errorlevel 1 (
    echo [GIT] _ISTORIC fara modificari - nimic de comis.
    exit /b 0
)

echo [GIT] Extrageri noi in _ISTORIC - commit + push pe origin/main...
git add -A -- _ISTORIC
if errorlevel 1 (
    echo [GIT] git add _ISTORIC a esuat.
    exit /b 1
)
git diff --cached --quiet -- _ISTORIC
if not errorlevel 1 (
    echo [GIT] Nimic staged in _ISTORIC - sar commit.
    exit /b 0
)

git commit -m "auto: update istoric extrageri (%DATE%)"
if errorlevel 1 (
    echo [GIT] git commit _ISTORIC a esuat. Verifica git config user.name / user.email.
    git status -sb
    exit /b 1
)

git push origin main
if errorlevel 1 (
    echo [GIT] Push esuat - incerc git pull --ff-only origin main apoi push...
    git pull --ff-only origin main
    git push origin main
)
if errorlevel 1 (
    echo [GIT] Push origin/main ESUAT ^(offline / auth / OneDrive?^).
    echo [GIT] Commit-ul e LOCAL pe main. Reincearca: git push origin main
    git status -sb
    exit /b 1
)
echo [GIT] _ISTORIC pushat pe origin/main.
exit /b 0


:ensure_main
for /f "delims=" %%b in ('git rev-parse --abbrev-ref HEAD 2^>nul') do set "_BR=%%b"
if /I "%_BR%"=="main" exit /b 0
echo [GIT] Ramura curenta e %_BR%, nu main. Checkout main...
git checkout main
if errorlevel 1 (
    echo [GIT] Checkout main esuat.
    exit /b 1
)
exit /b 0

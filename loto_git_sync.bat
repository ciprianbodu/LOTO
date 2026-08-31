@echo off
REM ============================================================
REM loto_git_sync.bat - git helper for START_8000.bat and ACTUALIZARI.bat
REM   loto_git_sync.bat autoupdate [root]  fetch+merge origin/main
REM   loto_git_sync.bat push_istoric    commit _ISTORIC, push origin/main
REM ASCII only. No delayed expansion. No parens in echo or REM.
REM CRLF required via .gitattributes. LF breaks for /f and echo.
REM Bug: echo with escaped parens after delayed expansion left an open
REM parenthesis, so the next REM Auto-update line ran as a command.
REM ============================================================
set "_ROOT=%~2"
if "%_ROOT%"=="" set "_ROOT=%~dp0"
cd /d "%_ROOT%"
git config windows.appendAtomically false >nul 2>&1

if /I "%~1"=="autoupdate" goto autoupdate
if /I "%~1"=="push_istoric" goto push_istoric
echo [GIT] Utilizare: %~nx0 autoupdate sau push_istoric
exit /b 2


:autoupdate
echo [GIT] Verific actualizari de pe GitHub...
git fetch origin main --quiet 2>nul
if errorlevel 1 (
    echo [GIT] Offline / fetch esuat - pornesc cu codul curent.
    exit /b 0
)

call :ensure_main
if errorlevel 1 (
    echo [GIT] Nu pot trece pe main - pornesc cu ramura curenta.
    exit /b 0
)

set "_IST_BAK=%TEMP%\loto_istoric_bak_%RANDOM%"
set "_IST_DIRTY=0"
git status --porcelain _ISTORIC 2>nul | findstr /R "." >nul 2>&1
if not errorlevel 1 set "_IST_DIRTY=1"
if "%_IST_DIRTY%"=="1" (
    echo [GIT] Salvez _ISTORIC local inainte de sync.
    xcopy /E /I /Y /Q "_ISTORIC" "%_IST_BAK%" >nul
)

git merge --ff-only origin/main >nul 2>&1
if errorlevel 1 (
    echo [GIT] Fast-forward imposibil. Stare:
    git status -sb
    call :force_sync
    if errorlevel 1 exit /b 1
) else (
    echo [GIT] Cod la zi cu GitHub.
)

if "%_IST_DIRTY%"=="1" (
    xcopy /E /I /Y /Q "%_IST_BAK%" "_ISTORIC" >nul
    rmdir /s /q "%_IST_BAK%" >nul 2>&1
    echo [GIT] Restaurat _ISTORIC local.
)
exit /b 0


:force_sync
REM Stash protejeaza doar modificarile tracked, NU commit-urile locale ahead.
REM Branch-ul backup pastreaza HEAD-ul complet inainte de reset.
set "_HEAD_SHORT=unknown"
for /f "delims=" %%h in ('git rev-parse --short HEAD 2^>nul') do set "_HEAD_SHORT=%%h"
set "_BACKUP_BRANCH=backup/auto-sync-%_HEAD_SHORT%-%RANDOM%"
git branch "%_BACKUP_BRANCH%" HEAD >nul 2>&1
if errorlevel 1 (
    echo [GIT] Nu pot crea branch backup - ANULEZ resetul fortat.
    exit /b 1
)
echo [GIT] Backup commit local: %_BACKUP_BRANCH%
git stash push -m "auto-backup before forced sync" >nul 2>&1
if errorlevel 1 (
    echo [GIT] Stash esuat - ANULEZ resetul fortat. Branch-ul backup ramane.
    exit /b 1
)
echo [GIT] Sincronizez FORTAT cu origin/main. Modificarile tracked sunt in stash.
git reset --hard origin/main >nul 2>&1
if errorlevel 1 (
    echo [GIT] Sincronizare fortata esuata - branch-ul backup ramane disponibil.
    exit /b 1
)
echo [GIT] Sincronizat la zi cu GitHub.
echo [GIT] Recuperare commit: git switch %_BACKUP_BRANCH%
echo [GIT] Recuperare modificari: git stash list
exit /b 0


:push_istoric
call :ensure_main
if errorlevel 1 (
    echo [GIT] Nu sunt pe main - NU comit _ISTORIC.
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
    echo [GIT] Push origin/main ESUAT. Commit-ul e LOCAL pe main.
    echo [GIT] Reincearca: git push origin main
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

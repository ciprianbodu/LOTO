param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Sync', 'PushHistory', 'Cleanup', 'Detect', 'EnsureGit')]
    [string]$Mode,
    [Parameter(Mandatory = $true)]
    [string]$ProjectDir,
    [string]$SnapshotDir,
    # PushHistory: Python-ul venv-ului, pentru verifica_istoric.py.
    [string]$PythonExe
)

# Sync is called ONLY from an immutable launcher copy outside the repository.
# No downloaded fragments, forced reset, stash or user-file deletion.
# Sync = fetch, apply origin/main, replay unpublished local commits onto it
# (rebase, aborted on conflict) and push. Uncommitted edits are never lost:
# files the update does not touch keep them in place; files changed on both
# sides are copied byte for byte to .git\loto-sync-backup, reset to HEAD for the
# update, then merged back with `git merge-file` on temporary copies. On a real
# conflict the code takes origin/main, bench_results keeps the local Re-Bench,
# and the local copy stays in the backup folder. Any failure puts them back.
$ErrorActionPreference = 'Stop'

if ($Mode -eq 'Cleanup') {
    if ($SnapshotDir) {
        $snapshot = [IO.Path]::GetFullPath($SnapshotDir).TrimEnd([char[]]@('\', '/'))
        $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd([char[]]@('\', '/'))
        if ((Split-Path $snapshot) -eq $tempRoot -and
            (Split-Path $snapshot -Leaf) -match '^loto-launch-\d+-\d+$') {
            # Remove only our known files; never recursively delete extras.
            foreach ($name in @('START_8000.bat', 'ACTUALIZARI.bat', 'launcher_git.ps1', 'git-checked')) {
                $file = Join-Path $snapshot $name
                if (Test-Path -LiteralPath $file -PathType Leaf) {
                    Remove-Item -LiteralPath $file -Force
                }
            }
            if ((Get-ChildItem -LiteralPath $snapshot -Force | Measure-Object).Count -eq 0) {
                Remove-Item -LiteralPath $snapshot
            }
        }
    }
    exit 0
}

if ($Mode -eq 'EnsureGit') {
    # Git for Windows itself (registry or standard install), not a portable git
    # shipped by another app (Codex): that one can vanish with the app's update,
    # lacks the GitHub credential helper and the pager. Never blocks the caller.
    function Find-GitForWindows {
        $candidates = @()
        foreach ($key in @('HKCU:\Software\GitForWindows', 'HKLM:\Software\GitForWindows',
                           'HKLM:\Software\WOW6432Node\GitForWindows')) {
            $install = (Get-ItemProperty -LiteralPath $key -ErrorAction SilentlyContinue).InstallPath
            if ($install) { $candidates += Join-Path $install 'cmd\git.exe' }
        }
        foreach ($base in @($env:ProgramW6432, $env:ProgramFiles, ${env:ProgramFiles(x86)})) {
            if ($base) { $candidates += Join-Path $base 'Git\cmd\git.exe' }
        }
        if ($env:LOCALAPPDATA) {
            $candidates += Join-Path $env:LOCALAPPDATA 'Programs\Git\cmd\git.exe'
        }
        foreach ($candidate in $candidates) {
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                return [IO.Path]::GetFullPath($candidate)
            }
        }
        return $null
    }

    function Find-OtherGit {
        # A git the user chose (LOTO_GIT_EXE, which Sync also uses first) or one
        # on PATH installed another way (scoop, custom folder). The Codex
        # portable git does not count: it is exactly what this step replaces.
        $codex = '[\\/]codex-runtimes[\\/]'
        $chosen = if ($env:LOTO_GIT_EXE) { $env:LOTO_GIT_EXE.Trim('"') } else { '' }
        if ($chosen -and [IO.Path]::IsPathRooted($chosen) -and $chosen -notmatch $codex -and
            (Test-Path -LiteralPath $chosen -PathType Leaf)) {
            return [pscustomobject]@{ Path = [IO.Path]::GetFullPath($chosen); Source = 'LOTO_GIT_EXE' }
        }
        $command = Get-Command git.exe -CommandType Application -All -ErrorAction SilentlyContinue |
            Where-Object { $_.Source -notmatch $codex } |
            Select-Object -First 1
        if ($command) { return [pscustomobject]@{ Path = $command.Source; Source = 'PATH' } }
        return $null
    }

    function Get-GitVersion([string]$Exe) {
        if (-not $Exe) { return $null }
        try { $text = (& $Exe --version 2>$null | Out-String) } catch { return $null }
        if ($text -match 'git version (\d+)\.(\d+)\.(\d+)(?:\.windows\.(\d+))?') {
            $build = 0
            if ($Matches[4]) { $build = [int]$Matches[4] }
            return [version]::new([int]$Matches[1], [int]$Matches[2], [int]$Matches[3], $build)
        }
        return $null
    }

    function Format-GitVersion([version]$Version) {
        $text = '{0}.{1}.{2}' -f $Version.Major, $Version.Minor, $Version.Build
        if ($Version.Revision -gt 0) { $text += '.windows.' + $Version.Revision }
        return $text
    }

    try {
        # One header line per run: the launcher tests count runs by it.
        Write-Host '[GIT] Verific Git for Windows (instalat si la zi)...'
        $manual = 'https://git-scm.com/download/win'
        $before = Get-GitVersion (Find-GitForWindows)
        if (-not $before) {
            $other = Find-OtherGit
            $otherVersion = if ($other) { Get-GitVersion $other.Path } else { $null }
            if ($otherVersion) {
                Write-Host ('[GIT] Git ' + (Format-GitVersion $otherVersion) + ' gasit prin ' + $other.Source +
                    ' (' + $other.Path + '), instalat altfel decat Git for Windows standard. ' +
                    'Il pastrez; actualizati-l cu programul care l-a instalat.')
                exit 0
            }
        }
        $winget = $env:LOTO_WINGET_EXE
        if (-not $winget) {
            $command = Get-Command winget -CommandType Application -ErrorAction SilentlyContinue |
                Select-Object -First 1
            if ($command) { $winget = $command.Source }
        }
        if (-not $winget) {
            if ($before) {
                Write-Host ('[GIT] Git for Windows ' + (Format-GitVersion $before) +
                    ' - winget lipseste, nu pot verifica actualizarile.')
            } else {
                Write-Host ('[GIT] [ATENTIE] Git for Windows lipseste si winget nu e disponibil. Instalati-l de pe ' + $manual)
            }
            exit 0
        }
        $wingetArgs = @('--id', 'Git.Git', '-e', '--source', 'winget', '--silent',
                        '--accept-package-agreements', '--accept-source-agreements')
        if ($before) {
            Write-Host ('[GIT] Git for Windows ' + (Format-GitVersion $before) +
                ' - verific actualizarile cu winget (o actualizare poate cere confirmare de administrator)...')
            & $winget upgrade @wingetArgs
        } else {
            Write-Host '[GIT] Git for Windows lipseste - il instalez cu winget (Windows poate cere confirmare de administrator)...'
            & $winget install @wingetArgs
        }
        $code = $LASTEXITCODE
        $after = Get-GitVersion (Find-GitForWindows)
        # winget: 0x8A15002B = nicio actualizare aplicabila, 0x8A150014 = pachet
        # negasit printre cele instalate (Git pus pe alta cale decat winget).
        # Orice alt cod cu versiunea neschimbata: verificarea sau instalarea a
        # esuat, ori confirmarea de administrator a fost refuzata; winget nu
        # spune care, deci mesajul nu presupune una anume.
        if (-not $after) {
            Write-Host ('[GIT] [ATENTIE] Git for Windows nu s-a instalat - winget cod ' + $code +
                '. Instalati-l manual de pe ' + $manual)
        } elseif (-not $before) {
            Write-Host ('[GIT] [OK] Git for Windows ' + (Format-GitVersion $after) + ' instalat.')
        } elseif ($after -gt $before) {
            Write-Host ('[GIT] [OK] Git for Windows actualizat: ' + (Format-GitVersion $before) +
                ' -> ' + (Format-GitVersion $after) + '.')
        } elseif ($code -eq 0 -or $code -eq -1978335189) {
            Write-Host ('[GIT] [OK] Git for Windows ' + (Format-GitVersion $after) + ' este la zi.')
        } elseif ($code -eq -1978335212) {
            Write-Host ('[GIT] Git for Windows ' + (Format-GitVersion $after) +
                ' nu e gestionat de winget - verificati versiunea pe ' + $manual)
        } else {
            Write-Host ('[GIT] [ATENTIE] Actualizarea Git nu s-a aplicat - winget cod ' + $code +
                ' (verificare esuata, confirmare de administrator refuzata sau instalare esuata). Raman pe Git ' +
                (Format-GitVersion $after) + '; reincercati la urmatoarea rulare sau instalati manual de pe ' + $manual)
        }
    } catch {
        Write-Host ('[GIT] [ATENTIE] Verificarea Git a esuat: ' + $_.Exception.Message)
    }
    exit 0
}

function Resolve-LotoGit {
    # Explorer/CMD need not inherit the PATH supplied by an editor or Codex.
    # Resolve here for BOTH Sync and PushHistory; do not change Windows settings.
    $candidates = @()
    if ($env:LOTO_GIT_EXE) { $candidates += $env:LOTO_GIT_EXE.Trim('"') }
    # PATH order, except the Codex portable git (an editor's PATH): it goes after
    # Git for Windows, which EnsureGit installs, because it can vanish with the app.
    $codexOnPath = @()
    foreach ($command in @(Get-Command git.exe -CommandType Application -All -ErrorAction SilentlyContinue)) {
        if ($command.Source -match '[\\/]codex-runtimes[\\/]') { $codexOnPath += $command.Source }
        else { $candidates += $command.Source }
    }

    foreach ($key in @('HKCU:\Software\GitForWindows', 'HKLM:\Software\GitForWindows',
                       'HKLM:\Software\WOW6432Node\GitForWindows')) {
        $install = (Get-ItemProperty -LiteralPath $key -ErrorAction SilentlyContinue).InstallPath
        if ($install) { $candidates += Join-Path $install 'cmd\git.exe' }
    }
    foreach ($base in @($env:ProgramW6432, $env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if ($base) { $candidates += Join-Path $base 'Git\cmd\git.exe' }
    }
    if ($env:LOCALAPPDATA) {
        $candidates += Join-Path $env:LOCALAPPDATA 'Programs\Git\cmd\git.exe'
    }
    $candidates += $codexOnPath
    # The local machine currently has this portable Git, without a global install.
    if ($env:USERPROFILE) {
        $candidates += Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe'
    }
    foreach ($candidate in $candidates) {
        if ([IO.Path]::IsPathRooted($candidate) -and
            (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return [IO.Path]::GetFullPath($candidate)
        }
    }
    return $null
}

Set-Location -LiteralPath $ProjectDir
$gitExe = Resolve-LotoGit
if (-not $gitExe) {
    Write-Host '[GIT] Git nu a fost gasit. Instaleaza Git for Windows sau seteaza LOTO_GIT_EXE; pastrez codul si istoricul local.'
    if ($Mode -eq 'Detect') { exit 1 }
    exit 0
}
Write-Host ('[GIT] Executabil: ' + $gitExe)

# Pe un folder sincronizat in cloud, fisierele pot fi doar descarcate la cerere,
# iar git status le citeste pe toate: limita de 45 s nu ajunge la prima trecere.
$cloudFolder = (Get-Location).Path -match 'My Drive|Google Drive|OneDrive|Dropbox'
$script:GitTimeoutSeconds = if ($cloudFolder) { 180 } else { 45 }
# Numai pentru teste: o limita mica face reproductibila oprirea la timeout.
if ($env:LOTO_GIT_TIMEOUT_SECONDS) { $script:GitTimeoutSeconds = [int]$env:LOTO_GIT_TIMEOUT_SECONDS }
$script:HistoryRefused = $false

function Request-OfflinePin {
    # attrib +P cere furnizorului cloud sa pastreze fisierele pe disc
    # (Disponibil offline). O data reusit, marcajul din .git evita repetarea.
    $marker = Join-Path (Get-Location).Path '.git\loto-offline-pin'
    if (Test-Path -LiteralPath $marker -PathType Leaf) { return }
    Write-Host '[GIT] Folder sincronizat in cloud - cer fixarea fisierelor pe disc (Disponibil offline)...'
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = "$env:SystemRoot\System32\attrib.exe"
    $info.Arguments = '+P /S /D "' + (Join-Path (Get-Location).Path '*') + '"'
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = [System.Diagnostics.Process]::Start($info)
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit(300000)) {
        & "$env:SystemRoot\System32\taskkill.exe" /F /T /PID $process.Id 2>&1 | Out-Null
        $process.Dispose()
        Write-Host '[GIT] Fixarea offline nu s-a terminat in 5 minute; continui fara ea.'
        return
    }
    $text = ($stdout.Result + $stderr.Result).Trim()
    $code = $process.ExitCode
    $process.Dispose()
    if ($code -eq 0 -and -not $text) {
        Set-Content -LiteralPath $marker -Value (Get-Date -Format 's') -Encoding ASCII
        Write-Host '[GIT] Fixare offline ceruta. Drive descarca fisierele in fundal.'
    } else {
        Write-Host '[GIT] Google Drive nu a acceptat fixarea automata. Manual: click dreapta pe folderul proiectului > Acces offline > Disponibil offline.'
    }
}

function Invoke-LotoProcess {
    # $null la depasirea limitei: apelantul spune ce comanda a expirat.
    param([string]$Exe, [string[]]$ArgList, [int]$TimeoutSeconds)
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $Exe
    $info.WorkingDirectory = (Get-Location).Path
    # Arguments are fixed below; quote separately, never run through a shell.
    $info.Arguments = (($ArgList | ForEach-Object { '"' + $_.Replace('"', '\"') + '"' }) -join ' ')
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.RedirectStandardInput = $true
    # Git scrie caile si mesajele in UTF-8 (diacritice in numele fisierelor).
    $info.StandardOutputEncoding = New-Object Text.UTF8Encoding $false
    $info.StandardErrorEncoding = New-Object Text.UTF8Encoding $false
    # Git children/hooks must find the same executable even when CMD has no Git in PATH.
    $info.EnvironmentVariables['PATH'] = (Split-Path -Parent $gitExe) + ';' + $env:PATH
    $info.EnvironmentVariables['GIT_TERMINAL_PROMPT'] = '0'
    $info.EnvironmentVariables['GCM_INTERACTIVE'] = 'Never'
    $info.EnvironmentVariables['LOTO_SKIP_AUTO_PUSH'] = '1'
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $info
    [void]$process.Start()
    $process.StandardInput.Close()
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        # Only this invocation and its children; never an application worker.
        if ($env:SystemRoot) {
            & "$env:SystemRoot\System32\taskkill.exe" /F /T /PID $process.Id 2>&1 | Out-Null
        } else {
            $process.Kill($true)  # PowerShell 7 in afara Windows (teste)
            [void]$process.WaitForExit(10000)
        }
        $process.Dispose()
        return $null
    }
    $result = [pscustomobject]@{
        Code = $process.ExitCode
        Text = ($stdout.Result + $stderr.Result).Trim()
        # Numai stdout, netaiat: inregistrarile -z ale git, fara avertismente.
        Out = $stdout.Result
    }
    $process.Dispose()
    return $result
}

function Invoke-LotoGit {
    param([string[]]$GitArgs, [int]$TimeoutSeconds = 0)
    if ($TimeoutSeconds -le 0) { $TimeoutSeconds = $script:GitTimeoutSeconds }
    $result = Invoke-LotoProcess -Exe $gitExe -ArgList $GitArgs -TimeoutSeconds $TimeoutSeconds
    if ($null -eq $result) {
        $workDir = (Get-Location).Path
        $message = 'Git a depasit ' + $TimeoutSeconds + ' s la "git ' + ($GitArgs -join ' ') + '"; operatia s-a oprit.'
        if ($workDir -match 'My Drive|Google Drive|OneDrive|Dropbox') {
            $message += ' Proiectul este intr-un folder sincronizat in cloud (' + $workDir + '); acolo git citeste lent fiecare fisier. Tineti repository-ul pe disc local.'
        }
        throw $message
    }
    return $result
}

function Clear-StaleGitLocks {
    # Un alt git.exe tine lock-ul. Fara proces, fisierul e ramas (Drive il
    # sincronizeaza) si blocheaza fetch/push cu "packed-refs.lock: File exists".
    if (Get-Process -Name 'git' -ErrorAction SilentlyContinue) { return }
    $dir = Invoke-LotoGit -GitArgs @('rev-parse', '--git-dir')
    if ($dir.Code -ne 0) { return }
    $gitDir = $dir.Text.Trim()
    if (-not [IO.Path]::IsPathRooted($gitDir)) {
        $gitDir = Join-Path (Get-Location).Path $gitDir
    }
    foreach ($name in @('packed-refs.lock', 'index.lock', 'HEAD.lock', 'config.lock')) {
        $path = Join-Path $gitDir $name
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
            Write-Host ('[GIT] Lock vechi eliminat: ' + $name)
        }
    }
    $refs = Join-Path $gitDir 'refs'
    if (Test-Path -LiteralPath $refs -PathType Container) {
        Get-ChildItem -LiteralPath $refs -Recurse -Filter '*.lock' -File -ErrorAction SilentlyContinue |
            ForEach-Object {
                Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue
                Write-Host ('[GIT] Lock vechi eliminat: ' + $_.Name)
            }
    }
}

function Invoke-LotoGitRetry {
    param([string[]]$GitArgs, [int]$TimeoutSeconds = 0)
    $result = Invoke-LotoGit -GitArgs $GitArgs -TimeoutSeconds $TimeoutSeconds
    if ($result.Code -ne 0 -and $result.Text -match 'File exists|Another git process') {
        Clear-StaleGitLocks
        $result = Invoke-LotoGit -GitArgs $GitArgs -TimeoutSeconds $TimeoutSeconds
    }
    return $result
}

function Test-HistoryRows {
    # Validarea proiectului (verifica_istoric.py: registrul loteriilor, antet,
    # date, valid_draw_matrix) pe CSV-urile trecute de git. Intoarce motivul pe
    # cale, 'ok' = valid; $null fara Python, iar atunci raman verificarile git.
    param([string[]]$Paths)
    $checker = Join-Path (Split-Path -Parent $PSScriptRoot) 'verifica_istoric.py'
    if (-not $PythonExe -or -not (Test-Path -LiteralPath $PythonExe -PathType Leaf) -or
        -not (Test-Path -LiteralPath $checker -PathType Leaf)) {
        Write-Host '[GIT] Validarea istoricului cu Python nu e disponibila (lipseste Python-ul venv-ului sau verifica_istoric.py); raman verificarile git.'
        return $null
    }
    $verdicts = @{}
    $failure = $null
    try {
        $check = Invoke-LotoProcess -Exe $PythonExe -ArgList (@($checker) + $Paths) -TimeoutSeconds $script:GitTimeoutSeconds
        if ($null -eq $check) {
            $failure = 'a depasit ' + $script:GitTimeoutSeconds + ' s'
        } else {
            $lines = @($check.Out -split "`r?`n" | Where-Object { $_ })
            if ($check.Code -ne 0 -or $lines.Count -ne $Paths.Count) {
                $failure = 'cod ' + $check.Code
                $last = @($check.Text -split "`r?`n")[-1]
                if ($last) { $failure += ': ' + $last }
            } else {
                for ($i = 0; $i -lt $Paths.Count; $i++) { $verdicts[$Paths[$i]] = $lines[$i] }
            }
        }
    } catch {
        $failure = $_.Exception.Message
    }
    if ($failure) {
        # O validare care nu a rulat nu accepta nimic; fisierele se reiau la
        # urmatoarea pornire.
        foreach ($path in $Paths) { $verdicts[$path] = 'validarea istoricului nu a rulat (' + $failure + ')' }
    }
    return $verdicts
}

function Select-HistoryAppends {
    # Actualizatoarele (update_csv.py, update_externe.py) numai adauga randuri
    # la CSV-urile urmarite. Orice alta schimbare pregatita de add -u e refuzata
    # cu fisierul si motivul: rand sters sau trunchiat, CSV rescris de Excel,
    # fisier sters, binar sau care nu e CSV. Intoarce caile acceptate.
    $stat = Invoke-LotoGit -GitArgs @('diff', '--cached', '--numstat', '--no-renames', '-z', '--', '_ISTORIC')
    if ($stat.Code -ne 0) { throw $stat.Text }
    $refused = [ordered]@{}
    $appends = @()
    foreach ($record in $stat.Out.Split([char]0)) {
        # adaugate <TAB> sterse <TAB> cale; '-' la fisierele binare.
        $fields = $record -split "`t", 3
        if ($fields.Count -ne 3) { continue }
        $path = $fields[2]
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            $refused[$path] = 'fisier sters'
        } elseif ($path -notlike '*.csv') {
            $refused[$path] = 'nu e CSV'
        } elseif ($fields[0] -eq '-') {
            $refused[$path] = 'continut binar'
        } elseif ([int]$fields[1] -gt 0) {
            $refused[$path] = 'linii sterse sau modificate: ' + $fields[1] + ' (actualizatoarele doar adauga randuri)'
        } else {
            $appends += $path
        }
    }
    $accepted = @()
    if ($appends.Count -gt 0) {
        $verdicts = Test-HistoryRows -Paths $appends
        foreach ($path in $appends) {
            if ($null -eq $verdicts -or $verdicts[$path] -eq 'ok') { $accepted += $path }
            else { $refused[$path] = $verdicts[$path] }
        }
    }
    if ($refused.Count -gt 0) {
        foreach ($path in $refused.Keys) {
            Write-Host ('[GIT] [REFUZAT] ' + $path + ' - ' + $refused[$path])
        }
        # Pe disc raman neatinse; din index ies, ca un commit manual ulterior
        # sa nu preia pe tacute o schimbare refuzata.
        $reset = Invoke-LotoGit -GitArgs (@('reset', '-q', '--') + @($refused.Keys))
        if ($reset.Code -ne 0) { throw $reset.Text }
        $script:HistoryRefused = $true
    }
    return $accepted
}

function Get-ChangedPaths {
    # Caile din `git diff --name-only -z` (fara redenumiri: ambele cai).
    param([string[]]$DiffArgs)
    $diff = Invoke-LotoGit -GitArgs (@('diff', '--name-only', '--no-renames', '-z') + $DiffArgs)
    if ($diff.Code -ne 0) { throw $diff.Text }
    return @($diff.Out.Split([char]0) | Where-Object { $_ })
}

function Format-PathList {
    param([string[]]$Paths, [int]$Max = 8)
    $shown = @($Paths | Select-Object -First $Max) -join ', '
    if ($Paths.Count -gt $Max) { $shown += ' si inca ' + ($Paths.Count - $Max) }
    return $shown
}

# Rezultatele Re-Bench-ului local: raman cele ale statiei, din care s-a
# calculat decizia ei (best_methods.json, runtime). Nu se combina doua bench-uri.
$script:LocalWinsPrefix = 'bench_results/'
# Fisierele puse deoparte in rularea curenta: blocul finally le pune la loc
# chiar daca Set-Aside s-a oprit la jumatate.
$script:AsideItems = $null
$script:AsideDir = $null

function New-PathSet {
    param([string[]]$Paths)
    # Ordinal: git deosebeste majusculele; o redenumire Foo -> foo are doua cai.
    $set = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
    foreach ($path in $Paths) { if ($path) { [void]$set.Add($path) } }
    return ,$set
}

function Get-LocalChanges {
    # Fisierele urmarite schimbate fata de HEAD: pe disc sau numai in index.
    $seen = New-PathSet @()
    $paths = @()
    foreach ($path in @(Get-ChangedPaths -DiffArgs @('HEAD')) + @(Get-ChangedPaths -DiffArgs @('--cached'))) {
        if ($seen.Add($path)) { $paths += $path }
    }
    return $paths
}

function Invoke-LotoGitPaths {
    # Comanda git pe o lista de cai literale, din fisier: linia de comanda
    # Windows (32 KB) nu limiteaza numarul fisierelor.
    param([string[]]$GitArgs, [string[]]$Paths)
    $gitDir = (Invoke-LotoGit -GitArgs @('rev-parse', '--absolute-git-dir')).Text
    $specFile = Join-Path $gitDir ('loto-sync-paths-' + $PID + '-' + [guid]::NewGuid().ToString('N'))
    $spec = ($Paths | ForEach-Object { ':(literal)' + $_ }) -join [char]0
    [IO.File]::WriteAllText($specFile, $spec, (New-Object Text.UTF8Encoding $false))
    try {
        return Invoke-LotoGit -GitArgs ($GitArgs + @(('--pathspec-from-file=' + $specFile), '--pathspec-file-nul'))
    } finally {
        Remove-Item -LiteralPath $specFile -Force -ErrorAction SilentlyContinue
    }
}

function Copy-LotoFile {
    param([string]$From, [string]$To)
    $parent = Split-Path -Parent $To
    if ($parent) { [void](New-Item -ItemType Directory -Force -Path $parent) }
    [IO.File]::Copy($From, $To, $true)
}

function Test-SameBytes {
    param([string]$A, [string]$B)
    $x = [IO.File]::ReadAllBytes($A)
    $y = [IO.File]::ReadAllBytes($B)
    if ($x.Length -ne $y.Length) { return $false }
    for ($i = 0; $i -lt $x.Length; $i++) { if ($x[$i] -ne $y[$i]) { return $false } }
    return $true
}

function Get-AsideBlockers {
    # Ce nu poate fi pus deoparte fara pierderi: un folder in locul fisierului
    # urmarit sau un fisier in locul unui folder din cale (checkout-ul fortat
    # le-ar sterge continutul neurmarit), ori o versiune in index diferita si
    # de HEAD, si de cea de pe disc (copia ia numai discul).
    param([string[]]$Paths, [string[]]$AllLocal)
    $root = (Get-Location).Path
    $diskVsIndex = New-PathSet @(Get-ChangedPaths -DiffArgs @())
    $indexVsHead = New-PathSet @(Get-ChangedPaths -DiffArgs @('--cached'))
    $wanted = New-PathSet $Paths
    $blocked = @()
    $folded = @{}
    foreach ($path in @($AllLocal) + @($Paths)) {
        # Pe Windows, Foo.txt si foo.txt sunt acelasi fisier pe disc: o
        # redenumire doar de majuscule nu se poate pune deoparte pe o singura cale.
        $key = $path.ToLowerInvariant()
        if ($folded.ContainsKey($key) -and $folded[$key] -cne $path -and
            ($wanted.Contains($path) -or $wanted.Contains($folded[$key]))) {
            $blocked += ($folded[$key] + ' / ' + $path + ' (redenumire doar de majuscule)')
        } elseif (-not $folded.ContainsKey($key)) { $folded[$key] = $path }
    }
    foreach ($path in $Paths) {
        if (Test-Path -LiteralPath (Join-Path $root $path) -PathType Container) {
            $blocked += ($path + ' (pe disc e un folder)')
            continue
        }
        $parts = @($path -split '/')
        $prefix = $root
        $clash = $null
        for ($i = 0; $i -lt $parts.Count - 1; $i++) {
            $prefix = Join-Path $prefix $parts[$i]
            if (Test-Path -LiteralPath $prefix -PathType Leaf) { $clash = ($parts[0..$i] -join '/'); break }
        }
        if ($clash) { $blocked += ($path + ' (pe disc ' + $clash + ' e fisier, nu folder)'); continue }
        if ($diskVsIndex.Contains($path) -and $indexVsHead.Contains($path)) {
            $blocked += ($path + ' (alta versiune in index decat pe disc)')
        }
    }
    return $blocked
}

function Set-Aside {
    # 1) copie octet cu octet a fiecarui fisier local (local\), 2) fisierele revin
    # la HEAD ca actualizarea sa treaca, 3) copia versiunii din HEAD, in forma de
    # pe disc (base\), pentru fuziunea in trei de dupa actualizare.
    param([string[]]$Paths, [string]$BackupDir)
    $root = (Get-Location).Path
    $tree = Invoke-LotoGit -GitArgs @('ls-tree', '-r', '--name-only', '-z', 'HEAD')
    if ($tree.Code -ne 0) { throw $tree.Text }
    $inHead = New-PathSet @($tree.Out.Split([char]0))
    if (Test-Path -LiteralPath $BackupDir) { throw ('Folderul de copie exista deja: ' + $BackupDir) }
    [void](New-Item -ItemType Directory -Force -Path $BackupDir)
    [IO.File]::WriteAllText((Join-Path $BackupDir 'PENDING'), (($Paths -join "`n") + "`n"), (New-Object Text.UTF8Encoding $false))
    $items = @()
    foreach ($path in $Paths) {
        $full = Join-Path $root $path
        $local = $null
        if (Test-Path -LiteralPath $full -PathType Leaf) {
            $local = Join-Path (Join-Path $BackupDir 'local') $path
            Copy-LotoFile -From $full -To $local
        }
        $items += [pscustomobject]@{ Path = $path; Local = $local; Base = $null; InHead = $inHead.Contains($path) }
    }
    $script:AsideItems = $items
    $tracked = @($items | Where-Object { $_.InHead } | ForEach-Object { $_.Path })
    $added = @($items | Where-Object { -not $_.InHead } | ForEach-Object { $_.Path })
    if ($tracked.Count -gt 0) {
        $back = Invoke-LotoGitPaths -GitArgs @('checkout', 'HEAD') -Paths $tracked
        if ($back.Code -ne 0) { throw $back.Text }
    }
    if ($added.Count -gt 0) {
        $unstage = Invoke-LotoGitPaths -GitArgs @('rm', '--cached', '-q', '--ignore-unmatch') -Paths $added
        if ($unstage.Code -ne 0) { throw $unstage.Text }
        foreach ($path in $added) {
            $full = Join-Path $root $path
            if (Test-Path -LiteralPath $full -PathType Leaf) { Remove-Item -LiteralPath $full -Force }
        }
    }
    foreach ($item in $items) {
        $full = Join-Path $root $item.Path
        if ($item.InHead -and (Test-Path -LiteralPath $full -PathType Leaf)) {
            $item.Base = Join-Path (Join-Path $BackupDir 'base') $item.Path
            Copy-LotoFile -From $full -To $item.Base
        }
    }
    return $items
}

# Octet cu octet ca text: Latin-1 pastreaza fiecare octet, iar CR/LF raman CR/LF.
function Read-Bytes8 { param([string]$Path) return [Text.Encoding]::GetEncoding(28591).GetString([IO.File]::ReadAllBytes($Path)) }
function Write-Bytes8 { param([string]$Path, [string]$Text) [IO.File]::WriteAllBytes($Path, [Text.Encoding]::GetEncoding(28591).GetBytes($Text)) }

function Merge-AsideFile {
    # Fuziune in trei pe copii temporare: versiunea noua <- base -> local.
    # Sfarsitul de linie nu e o modificare (CRLF din checkout cu autocrlf, LF
    # salvat de un editor): cele trei se compara cu LF, iar rezultatul ia
    # sfarsitul de linie al versiunii noi. Intoarce calea rezultatului sau $null.
    param([string]$New, [string]$Base, [string]$Local, [string]$Dir)
    [void](New-Item -ItemType Directory -Force -Path $Dir)
    $texts = [ordered]@{ new = (Read-Bytes8 $New); base = (Read-Bytes8 $Base); local = (Read-Bytes8 $Local) }
    $crlf = $texts.new.Contains("`r`n")
    $normalize = @($texts.Values | Where-Object { $_.Contains("`r`n") }).Count -gt 0
    foreach ($key in @($texts.Keys)) {
        $text = $texts[$key]
        if ($normalize) { $text = $text.Replace("`r`n", "`n") }
        Write-Bytes8 (Join-Path $Dir $key) $text
    }
    $result = Join-Path $Dir 'new'
    $merge = Invoke-LotoGit -GitArgs @('merge-file', '-q', $result, (Join-Path $Dir 'base'), (Join-Path $Dir 'local'))
    if ($merge.Code -ne 0) { return $null }
    if ($normalize -and $crlf) { Write-Bytes8 $result ((Read-Bytes8 $result).Replace("`n", "`r`n")) }
    return $result
}

function Restore-Aside {
    # Pune la loc fisierele puse deoparte. Fara actualizare: exact cum erau.
    # Dupa actualizare: bench_results ia copia locala; un fisier neatins de
    # actualizare isi ia copia locala; celelalte se combina (Merge-AsideFile);
    # la conflict ramane origin/main, iar copia locala ramane in BackupDir\local.
    # -KeepBackup: copia nu se sterge (sincronizare oprita de o eroare).
    param([object[]]$Items, [string]$BackupDir, [bool]$Updated, [bool]$KeepBackup = $false)
    $root = (Get-Location).Path
    $report = [ordered]@{ Merged = @(); Kept = @(); Conflicts = @(); Gone = @(); LocalDeleted = @(); Failed = @() }
    $restage = @()
    $index = 0
    foreach ($item in $Items) {
        $index++
        $full = Join-Path $root $item.Path
        try {
            if (-not $Updated) {
                if ($item.Local) {
                    Copy-LotoFile -From $item.Local -To $full
                    if (-not $item.InHead) { $restage += $item.Path }
                } elseif (Test-Path -LiteralPath $full -PathType Leaf) {
                    Remove-Item -LiteralPath $full -Force
                }
                continue
            }
            if ($item.Path.StartsWith($script:LocalWinsPrefix, [StringComparison]::OrdinalIgnoreCase)) {
                if ($item.Local) {
                    Copy-LotoFile -From $item.Local -To $full
                    if (-not $item.InHead) { $restage += $item.Path }
                } elseif (Test-Path -LiteralPath $full -PathType Leaf) { Remove-Item -LiteralPath $full -Force }
                $report.Kept += $item.Path
                continue
            }
            $exists = Test-Path -LiteralPath $full -PathType Leaf
            if (-not $item.Local) {
                # Sters local. Neatins de actualizare (la rebase, oricare
                # fisier local trece pe aici): ramane sters; schimbat: ramane.
                if (-not $exists) { $report.Merged += $item.Path }
                elseif ($item.Base -and (Test-SameBytes $item.Base $full)) {
                    Remove-Item -LiteralPath $full -Force
                    $report.Merged += $item.Path
                } else { $report.LocalDeleted += $item.Path }
                continue
            }
            if (-not $item.InHead) {
                # Adaugat local: revine daca actualizarea nu aduce acelasi fisier.
                if (-not $exists) {
                    Copy-LotoFile -From $item.Local -To $full
                    $restage += $item.Path
                    $report.Merged += $item.Path
                } elseif (Test-SameBytes $item.Local $full) { $report.Merged += $item.Path }
                else { $report.Conflicts += $item.Path }
                continue
            }
            if (-not $exists) { $report.Gone += $item.Path; continue }
            if (Test-SameBytes $item.Base $full) {
                # Actualizarea nu l-a atins: copia locala, exact (si binar).
                Copy-LotoFile -From $item.Local -To $full
                $report.Merged += $item.Path
                continue
            }
            $merged = Merge-AsideFile -New $full -Base $item.Base -Local $item.Local -Dir (Join-Path (Join-Path $BackupDir 'merge') ([string]$index))
            if ($merged) {
                Copy-LotoFile -From $merged -To $full
                $report.Merged += $item.Path
            } else {
                $report.Conflicts += $item.Path
            }
        } catch {
            $report.Failed += $item.Path
        }
    }
    if ($restage.Count -gt 0) {
        # -f: erau in index inainte, chiar daca .gitignore le acopera.
        $add = Invoke-LotoGitPaths -GitArgs @('add', '-f') -Paths $restage
        if ($add.Code -ne 0) { $report.Failed += $restage }
    }
    Remove-Item -LiteralPath (Join-Path $BackupDir 'merge') -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $BackupDir 'PENDING') -Force -ErrorAction SilentlyContinue
    $utf8 = New-Object Text.UTF8Encoding $false
    if ($report.Failed.Count -gt 0) {
        [IO.File]::WriteAllText((Join-Path $BackupDir 'RESTORE-FAILED'), (($report.Failed -join "`n") + "`n"), $utf8)
    }
    $lost = @($report.Conflicts | ForEach-Object { 'conflict: ' + $_ }) + @($report.Gone | ForEach-Object { 'sters pe origin/main: ' + $_ })
    if ($lost.Count -gt 0) {
        # Reamintit la fiecare pornire pana cand utilizatorul sterge folderul.
        [IO.File]::WriteAllText((Join-Path $BackupDir 'CONFLICTS'), (($lost -join "`n") + "`n"), $utf8)
    } elseif ($report.Failed.Count -eq 0 -and -not $KeepBackup) {
        Remove-AsideDir -BackupDir $BackupDir
    }
    $script:AsideItems = $null
    return $report
}

function Test-RebaseState {
    foreach ($state in @('rebase-merge', 'rebase-apply')) {
        $statePath = Invoke-LotoGit -GitArgs @('rev-parse', '--git-path', $state)
        if ($statePath.Code -eq 0 -and (Test-Path -LiteralPath $statePath.Text)) { return $true }
    }
    return $false
}

function Clear-InterruptedRebase {
    # Dupa o eroare in timpul rebase-ului (ex. git oprit la depasirea timpului):
    # anuleaza-l. $true numai daca nu mai e rebase si HEAD e iar pe main;
    # altfel fisierele puse deoparte NU se scriu peste un arbore la jumatate.
    try {
        if (Test-RebaseState) {
            $abort = Invoke-LotoGitRetry -GitArgs @('rebase', '--abort') -TimeoutSeconds 600
            if ($abort.Code -ne 0) { return $false }
        }
        $branch = Invoke-LotoGit -GitArgs @('symbolic-ref', '--quiet', '--short', 'HEAD')
        return ($branch.Code -eq 0 -and $branch.Text -eq 'main' -and -not (Test-RebaseState))
    } catch {
        return $false
    }
}

function Get-Lines {
    # Iesirea unei comenzi git, ca lista de linii nevide.
    param([string[]]$GitArgs)
    $result = Invoke-LotoGit -GitArgs $GitArgs
    if ($result.Code -ne 0) { return @() }
    return @($result.Out -split "`r?`n" | Where-Object { $_ })
}

function Get-WithdrawnCommits {
    # Commit-urile locale (HEAD, nu origin/main) care au fost candva pe
    # origin/main si nu mai sunt: istoria de acolo s-a rescris (force-push).
    # Un push sau un rebase le-ar publica din nou. Se tin minte in
    # refs/loto/withdrawn/, ca regula sa nu expire odata cu reflog-ul.
    $former = @(Get-Lines @('reflog', 'show', '--format=%H', '-n', '200', 'refs/remotes/origin/main')) +
        @(Get-Lines @('for-each-ref', '--format=%(objectname)', 'refs/loto/withdrawn'))
    $former = @($former | Select-Object -Unique)
    if ($former.Count -eq 0) { return @() }
    $ahead = @(Get-Lines @('rev-list', 'HEAD', '--not', 'origin/main'))
    if ($ahead.Count -eq 0) { return @() }
    $fresh = New-PathSet @(Get-Lines (@('rev-list', 'HEAD', '--not', 'origin/main') + $former))
    $withdrawn = @($ahead | Where-Object { -not $fresh.Contains($_) })
    foreach ($sha in $withdrawn) {
        [void](Invoke-LotoGit -GitArgs @('update-ref', ('refs/loto/withdrawn/' + $sha), $sha))
    }
    return $withdrawn
}

function Test-RedundantHistory {
    # Commit-uri locale numai pe _ISTORIC/*.csv ale caror randuri sunt toate
    # deja pe origin/main (alta statie le-a trimis): se pot lasa deoparte fara
    # pierderi, altfel rebase-ul s-ar opri la fiecare pornire pe aceleasi randuri.
    $names = @(Get-Lines @('diff', '--name-only', '--no-renames', 'origin/main...HEAD'))
    if ($names.Count -eq 0) { return $false }
    foreach ($name in $names) {
        if ($name -notlike '_ISTORIC/*.csv') { return $false }
        $mine = Invoke-LotoGit -GitArgs @('show', ('HEAD:' + $name))
        $theirs = Invoke-LotoGit -GitArgs @('show', ('origin/main:' + $name))
        if ($mine.Code -ne 0 -or $theirs.Code -ne 0) { return $false }
        $upstream = New-PathSet @($theirs.Out -split "`r?`n")
        foreach ($line in @($mine.Out -split "`r?`n" | Where-Object { $_ })) {
            if (-not $upstream.Contains($line)) { return $false }
        }
    }
    return $true
}

function Get-UntrackedCollisions {
    # Fisiere neurmarite aflate pe caile pe care origin/main le aduce. Cele
    # identice cu versiunea de pe origin/main (ramase dintr-o actualizare oprita
    # sau copiate de mana) se pot sterge: continutul vine oricum. Intoarce
    # @{ Same = ...; Different = ... }.
    $same = @()
    $different = @()
    foreach ($path in @(Get-ChangedPaths -DiffArgs @('--diff-filter=A', 'HEAD', 'origin/main'))) {
        $full = Join-Path (Get-Location).Path $path
        if (-not (Test-Path -LiteralPath $full)) { continue }
        $known = Invoke-LotoGit -GitArgs @('ls-files', '--', (':(literal)' + $path))
        if ($known.Code -eq 0 -and $known.Text) { continue }  # in index: e o modificare locala
        $isSame = $false
        if (Test-Path -LiteralPath $full -PathType Leaf) {
            $mine = Invoke-LotoGit -GitArgs @('hash-object', ('--path=' + $path), '--', $full)
            $theirs = Invoke-LotoGit -GitArgs @('rev-parse', ('origin/main:' + $path))
            $isSame = ($mine.Code -eq 0 -and $theirs.Code -eq 0 -and $mine.Text -eq $theirs.Text)
        }
        if ($isSame) { $same += $path } else { $different += $path }
    }
    return @{ Same = $same; Different = $different }
}

function Enter-SyncLock {
    # Un singur Sync/PushHistory pe repository: doua lansatoare pornite deodata
    # ar copia si pune la loc aceleasi fisiere peste ele. Lacatul e un fisier
    # deschis exclusiv; sistemul il elibereaza si daca procesul e oprit brusc.
    $gitDir = (Invoke-LotoGit -GitArgs @('rev-parse', '--absolute-git-dir')).Text
    $path = Join-Path $gitDir 'loto-sync.lock'
    try {
        $script:SyncLock = [IO.File]::Open($path, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        return $true
    } catch {
        return $false
    }
}

function Resolve-SyncLeftovers {
    # Ce a lasat o sincronizare anterioara. Ruleaza inaintea verificarii
    # ramurii: un rebase intrerupt lasa HEAD detasat, iar mesajul trebuie sa
    # apara. O copie PENDING (fereastra inchisa la jumatate) se pune la loc
    # automat, prin aceeasi fuziune, cand arborele e pe main; conflictele si
    # punerile la loc esuate se reamintesc pana la stergerea folderului.
    $gitDir = (Invoke-LotoGit -GitArgs @('rev-parse', '--absolute-git-dir')).Text
    $backupRoot = Join-Path $gitDir 'loto-sync-backup'
    $rebasing = Test-RebaseState
    if ($rebasing) {
        Write-Host '[GIT] [ATENTIE] Un rebase a ramas neterminat (HEAD detasat). Rulati git rebase --abort (daca git spune ca exista .git\index.lock: inchideti programele care folosesc git si stergeti fisierul), apoi reporniti.'
    }
    if (-not (Test-Path -LiteralPath $backupRoot -PathType Container)) { return }
    $branch = Invoke-LotoGit -GitArgs @('symbolic-ref', '--quiet', '--short', 'HEAD')
    $onMain = ($branch.Code -eq 0 -and $branch.Text -eq 'main')
    foreach ($dir in @(Get-ChildItem -LiteralPath $backupRoot -Directory)) {
        $localDir = Join-Path $dir.FullName 'local'
        $pending = Join-Path $dir.FullName 'PENDING'
        if (Test-Path -LiteralPath $pending -PathType Leaf) {
            if ($rebasing -or -not $onMain) {
                Write-Host ('[GIT] [ATENTIE] O sincronizare anterioara s-a intrerupt; modificarile locale de atunci sunt in ' + $localDir + '. Le pun la loc la pornirea urmatoare, dupa git rebase --abort.')
                continue
            }
            $items = @()
            foreach ($path in @([IO.File]::ReadAllLines($pending) | Where-Object { $_ })) {
                $local = Join-Path $localDir $path
                $base = Join-Path (Join-Path $dir.FullName 'base') $path
                $hasBase = Test-Path -LiteralPath $base -PathType Leaf
                $items += [pscustomobject]@{
                    Path = $path
                    Local = $(if (Test-Path -LiteralPath $local -PathType Leaf) { $local } else { $null })
                    Base = $(if ($hasBase) { $base } else { $null })
                    InHead = $hasBase
                }
            }
            Write-Host ('[GIT] O sincronizare anterioara s-a intrerupt; pun la loc modificarile locale din ' + $localDir + '.')
            $report = Restore-Aside -Items $items -BackupDir $dir.FullName -Updated $true
            Write-AsideReport -Report $report -BackupDir $dir.FullName
            continue
        }
        if (Test-Path -LiteralPath (Join-Path $dir.FullName 'RESTORE-FAILED') -PathType Leaf) {
            Write-Host ('[GIT] [ATENTIE] La o sincronizare anterioara unele fisiere nu au putut fi puse la loc; copiile sunt in ' + $localDir + '. Dupa recuperare, stergeti folderul ' + $dir.FullName + '.')
        }
        $marker = Join-Path $dir.FullName 'CONFLICTS'
        if (Test-Path -LiteralPath $marker -PathType Leaf) {
            $lines = @([IO.File]::ReadAllLines($marker) | Where-Object { $_ })
            Write-Host ('[GIT] Versiuni locale pastrate dupa o sincronizare (' + (Format-PathList $lines 4) + ') in ' + $localDir + '. Dupa ce le recuperati, stergeti folderul ' + $dir.FullName + '.')
        }
    }
}

function Remove-AsideDir {
    # Copia fara conflicte nu mai trebuie; folderul comun dispare cand e gol.
    param([string]$BackupDir)
    Remove-Item -LiteralPath $BackupDir -Recurse -Force -ErrorAction SilentlyContinue
    $parent = Split-Path -Parent $BackupDir
    if ((Test-Path -LiteralPath $parent -PathType Container) -and
        -not (Get-ChildItem -LiteralPath $parent -Force | Select-Object -First 1)) {
        Remove-Item -LiteralPath $parent -Force -ErrorAction SilentlyContinue
    }
}

function Write-AsideReport {
    param($Report, [string]$BackupDir)
    $localDir = Join-Path $BackupDir 'local'
    if ($Report.Merged.Count -gt 0) {
        Write-Host ('[GIT] Modificarile locale au fost combinate cu actualizarea: ' + (Format-PathList $Report.Merged) + '.')
    }
    if ($Report.Kept.Count -gt 0) {
        Write-Host ('[GIT] Rezultatele Re-Bench locale raman: ' + (Format-PathList $Report.Kept) + '.')
    }
    if ($Report.Conflicts.Count -gt 0) {
        Write-Host ('[GIT] Conflict in ' + (Format-PathList $Report.Conflicts) + ': am pus versiunea de pe origin/main; versiunea locala este in ' + $localDir + '.')
    }
    if ($Report.Gone.Count -gt 0) {
        Write-Host ('[GIT] Sterse pe origin/main: ' + (Format-PathList $Report.Gone) + '; versiunea locala este in ' + $localDir + '.')
    }
    if ($Report.LocalDeleted.Count -gt 0) {
        Write-Host ('[GIT] Stergerea locala a fisierelor ' + (Format-PathList $Report.LocalDeleted) + ' nu s-a pastrat: au fost schimbate pe origin/main.')
    }
    if ($Report.Failed.Count -gt 0) {
        Write-Host ('[GIT] [ATENTIE] Nu am putut pune la loc ' + (Format-PathList $Report.Failed) + '; copia locala este in ' + $localDir + '. Dupa recuperare, stergeti folderul ' + $BackupDir + '.')
    }
}

function Push-LotoMain {
    $push = Invoke-LotoGitRetry -GitArgs @('push', 'origin', 'main')
    if ($push.Text) { Write-Host $push.Text }
    if ($push.Code -ne 0) {
        Write-Host '[GIT] Push esuat - commit-urile raman locale; se reincearca la urmatoarea pornire.'
        return
    }
    Write-Host '[GIT] Push origin/main reusit.'
}

try {
    if ($Mode -eq 'Detect') {
        $version = Invoke-LotoGit -GitArgs @('--version')
        if ($version.Code -ne 0) { throw $version.Text }
        Write-Host ('[GIT] ' + $version.Text)
        exit 0
    }
    $root = Invoke-LotoGit -GitArgs @('rev-parse', '--show-toplevel')
    if ($root.Code -ne 0 -or
        [IO.Path]::GetFullPath($root.Text) -ne (Get-Location).Path.TrimEnd('\')) {
        throw 'Directorul lansatorului nu este radacina repository-ului.'
    }
    if (-not (Enter-SyncLock)) {
        Write-Host '[GIT] Alt lansator sincronizeaza acum acest repository; il las sa termine si nu modific nimic.'
        exit 0
    }
    if ($Mode -eq 'Sync') { Resolve-SyncLeftovers }
    $branch = Invoke-LotoGit -GitArgs @('symbolic-ref', '--quiet', '--short', 'HEAD')
    if ($branch.Code -ne 0 -or $branch.Text -ne 'main') {
        Write-Host '[GIT] Ramura curenta nu este main - nu modific repository-ul.'
        exit 0
    }
    foreach ($state in @('MERGE_HEAD', 'rebase-merge', 'rebase-apply', 'CHERRY_PICK_HEAD', 'REVERT_HEAD')) {
        $statePath = Invoke-LotoGit -GitArgs @('rev-parse', '--git-path', $state)
        if ($statePath.Code -ne 0 -or (Test-Path -LiteralPath $statePath.Text)) {
            throw 'Exista o operatie Git neterminata. Pastrez starea pentru rezolvare manuala.'
        }
    }
    if ($cloudFolder) {
        # Fixarea e un ajutor, nu o conditie: un esec aici nu opreste sincronizarea.
        try { Request-OfflinePin } catch { Write-Host ('[GIT] Fixare offline esuata: ' + $_.Exception.Message) }
    }
    Clear-StaleGitLocks
    $hooks = Invoke-LotoGit -GitArgs @('config', 'core.hooksPath', 'scripts/git-hooks')
    if ($hooks.Code -ne 0) { throw $hooks.Text }

    if ($Mode -eq 'Sync') {
        $gitDir = (Invoke-LotoGit -GitArgs @('rev-parse', '--absolute-git-dir')).Text
        $backupRoot = Join-Path $gitDir 'loto-sync-backup'
        $local = @(Get-LocalChanges)
        if ($local.Count -gt 0) {
            Write-Host ('[GIT] Modificari locale necomise (' + $local.Count + '): ' + (Format-PathList $local) + '.')
        }
        $fetch = Invoke-LotoGitRetry -GitArgs @('fetch', 'origin')
        if ($fetch.Code -ne 0) {
            Write-Host '[GIT] Fetch esuat - codul si fisierele locale raman neschimbate.'
            if ($fetch.Text) { Write-Host $fetch.Text }
            exit 0
        }
        $ahead = Invoke-LotoGit -GitArgs @('rev-list', '--count', 'origin/main..HEAD')
        $behind = Invoke-LotoGit -GitArgs @('rev-list', '--count', 'HEAD..origin/main')
        if ($ahead.Code -ne 0 -or $behind.Code -ne 0) { throw 'origin/main indisponibil.' }
        $nAhead = [int]$ahead.Text
        $nBehind = [int]$behind.Text
        if ($nAhead -eq 0 -and $nBehind -eq 0) {
            Write-Host '[GIT] Cod la zi.'
            exit 0
        }

        # Planul: ff (numai commit-uri noi pe origin/main), push (numai locale),
        # rebase (ambele), drop (main trece pe origin/main fara pierderi: commit-urile
        # locale au fost retrase de pe origin/main sau sunt randuri de istoric
        # deja acolo; vechiul main ramane intr-o referinta de rezerva).
        $plan = 'ff'
        $manual = $null
        $dropWhy = $null
        if ($nAhead -gt 0) {
            $merges = Invoke-LotoGit -GitArgs @('rev-list', '--merges', '--count', 'origin/main..HEAD')
            $withdrawn = @(Get-WithdrawnCommits)
            if ($merges.Code -ne 0 -or [int]$merges.Text -gt 0) {
                $manual = 'contin un commit de merge'
            } elseif ($withdrawn.Count -gt 0 -and $withdrawn.Count -eq $nAhead) {
                $plan = 'drop'
                $dropWhy = 'au fost retrase de pe origin/main'
            } elseif ($withdrawn.Count -gt 0) {
                $manual = 'contin ' + $withdrawn.Count + ' commit-uri retrase de pe origin/main (istoria de acolo s-a rescris) amestecate cu commit-uri proprii'
            } elseif ($nBehind -eq 0) {
                $plan = 'push'
            } else {
                # Un commit nou de pe origin/main care a fost varful lui main aici
                # inseamna amend/reset al unui commit trimis: rebase-ul l-ar pierde.
                # Reflog-ul ramurii, nu al HEAD (un rebase anulat nu conteaza).
                $upstream = New-PathSet @(Get-Lines @('rev-list', 'HEAD..origin/main'))
                foreach ($sha in @(Get-Lines @('reflog', 'show', '--format=%H', '-n', '200', 'refs/heads/main'))) {
                    if ($upstream.Contains($sha)) { $manual = 'rescriu un commit deja trimis (amend sau reset)'; break }
                }
                if (-not $manual) {
                    if (Test-RedundantHistory) {
                        $plan = 'drop'
                        $dropWhy = 'contin numai randuri de istoric care sunt deja pe origin/main'
                    } else {
                        $plan = 'rebase'
                    }
                }
            }
        }
        if ($manual) {
            Write-Host ('[GIT] main local are ' + $nAhead + ' commit-uri care ' + $manual + '; origin/main are ' + $nBehind + ' commit-uri noi. Integrarea ramane manuala; codul local ramane neschimbat.')
            exit 0
        }
        if ($plan -eq 'push') {
            Write-Host ('[GIT] Cod la zi; trimit ' + $nAhead + ' commit-uri locale.')
            Push-LotoMain
            exit 0
        }

        if ($plan -eq 'ff') {
            # Fast-forward-ul pastreaza singur modificarile din fisierele pe care
            # nu le atinge; deoparte merg numai cele schimbate si pe origin/main.
            $incoming = New-PathSet @(Get-ChangedPaths -DiffArgs @('HEAD', 'origin/main'))
            $aside = @($local | Where-Object { $incoming.Contains($_) })
        } else {
            # Rebase si trecerea pe origin/main cer arborele curat.
            $aside = @($local)
        }
        if ($aside.Count -gt 0) {
            $blockers = @(Get-AsideBlockers -Paths $aside -AllLocal $local)
            if ($blockers.Count -gt 0) {
                Write-Host ('[GIT] Nu pot pune deoparte fara pierderi: ' + (Format-PathList $blockers) + '. Comiteti sau mutati aceste fisiere (la index: git add sau git restore --staged), apoi reporniti. Codul local ramane neschimbat.')
                exit 0
            }
        }
        if ($plan -ne 'rebase') {
            # Fisiere neurmarite pe care origin/main le aduce: cele identice se
            # sterg (vin oricum); celelalte ar fi suprascrise de trecerea pe
            # origin/main si opresc sync-ul, iar ff-ul le refuza oricum.
            $collide = Get-UntrackedCollisions
            if ($collide.Different.Count -gt 0) {
                Write-Host ('[GIT] Fisiere neurmarite pe care origin/main le aduce cu alt continut: ' + (Format-PathList $collide.Different) + '. Mutati-le sau stergeti-le, apoi reporniti. Codul local ramane neschimbat.')
                exit 0
            }
            foreach ($path in $collide.Same) { Remove-Item -LiteralPath (Join-Path (Get-Location).Path $path) -Force }
            if ($collide.Same.Count -gt 0) {
                Write-Host ('[GIT] Sterse inainte de actualizare (identice cu origin/main): ' + (Format-PathList $collide.Same) + '.')
            }
        }

        $stamp = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + $PID
        $backupDir = Join-Path $backupRoot $stamp
        $startHead = (Invoke-LotoGit -GitArgs @('rev-parse', 'HEAD')).Text
        $target = (Invoke-LotoGit -GitArgs @('rev-parse', 'origin/main')).Text
        $updated = $false
        $failure = $null
        $crashed = $false
        $outcome = $null
        $report = $null
        try {
            if ($aside.Count -gt 0) {
                if ($plan -eq 'ff') {
                    Write-Host ('[GIT] Schimbate si local, si pe origin/main: ' + (Format-PathList $aside) + '. Le pun deoparte (copie in ' + $backupDir + ') si le combin dupa actualizare.')
                } else {
                    Write-Host ('[GIT] Pun deoparte modificarile locale (' + $aside.Count + ' fisiere) pe durata integrarii; copie in ' + $backupDir + '.')
                }
                [void](Set-Aside -Paths $aside -BackupDir $backupDir)
                $asideSet = New-PathSet $aside
                $still = @(Get-LocalChanges | Where-Object { $asideSet.Contains($_) })
                if ($still.Count -gt 0) { throw ('Nu am putut pune deoparte: ' + (Format-PathList $still) + '.') }
            }
            if ($plan -eq 'rebase') {
                Write-Host ('[GIT] Repun ' + $nAhead + ' commit-uri locale peste origin/main (' + $nBehind + ' commit-uri noi).')
                $integrate = Invoke-LotoGitRetry -GitArgs @('rebase', 'origin/main')
                if ($integrate.Code -ne 0) {
                    $conflicts = @()
                    try { $conflicts = @(Get-ChangedPaths -DiffArgs @('--diff-filter=U')) } catch { }
                    $failure = 'Commit-urile locale nu se pot repune peste origin/main'
                    if ($conflicts.Count -gt 0) { $failure += ' (conflict in ' + (Format-PathList $conflicts) + ')' }
                    elseif ($integrate.Text) { Write-Host $integrate.Text }
                }
            } elseif ($plan -eq 'drop') {
                $keep = 'refs/loto-sync/main-' + $stamp
                $saved = Invoke-LotoGit -GitArgs @('update-ref', $keep, 'HEAD')
                if ($saved.Code -ne 0) { throw $saved.Text }
                Write-Host ('[GIT] main local trece pe origin/main: cele ' + $nAhead + ' commit-uri locale ' + $dropWhy + '; vechiul main ramane in ' + $keep + '.')
                $integrate = Invoke-LotoGit -GitArgs @('reset', '-q', '--keep', 'origin/main')
                if ($integrate.Code -ne 0) {
                    $failure = 'Trecerea pe origin/main nu a reusit'
                    if ($integrate.Text) { Write-Host $integrate.Text }
                }
            } else {
                $integrate = Invoke-LotoGit -GitArgs @('merge', '--ff-only', 'origin/main')
                if ($integrate.Code -ne 0) {
                    $failure = 'Actualizarea nu a reusit'
                    if ($integrate.Text) { Write-Host $integrate.Text }
                }
            }
            $updated = -not $failure
        } catch {
            $failure = $_.Exception.Message
            $crashed = $true
        } finally {
            # Starea reala, nu ce credem: un rebase oprit se anuleaza intai; apoi
            # HEAD trebuie sa fie pe main, la commit-ul de start (nimic aplicat)
            # sau la cel nou (aplicat). Altfel nimic nu se scrie peste arbore.
            $outcome = 'stranded'
            if ($updated -or -not (Test-RebaseState) -or (Clear-InterruptedRebase)) {
                $now = (Invoke-LotoGit -GitArgs @('rev-parse', 'HEAD')).Text
                $branch = Invoke-LotoGit -GitArgs @('symbolic-ref', '--quiet', '--short', 'HEAD')
                if ($branch.Code -eq 0 -and $branch.Text -eq 'main' -and -not (Test-RebaseState)) {
                    if ($updated) { $outcome = 'updated' }
                    elseif ($now -eq $startHead) { $outcome = 'unchanged' }
                    elseif ($plan -ne 'rebase' -and $now -eq $target) { $outcome = 'updated'; $updated = $true }
                }
            }
            if ($null -ne $script:AsideItems) {
                if ($outcome -ne 'stranded') {
                    $report = Restore-Aside -Items $script:AsideItems -BackupDir $backupDir -Updated ($outcome -eq 'updated') -KeepBackup $crashed
                }
            } elseif (Test-Path -LiteralPath $backupDir -PathType Container) {
                # Set-Aside s-a oprit inainte sa atinga vreun fisier: copia e inutila.
                Remove-AsideDir -BackupDir $backupDir
            }
        }
        if ($outcome -eq 'updated' -and $plan -eq 'drop') {
            Write-Host '[GIT] main local este acum origin/main: aplicatia si lansatoarele, impreuna.'
        } elseif ($outcome -eq 'updated') {
            Write-Host ('[GIT] Actualizat la origin/main (' + $nBehind + ' commit-uri noi): aplicatia si lansatoarele, impreuna.')
        } elseif ($outcome -eq 'stranded') {
            Write-Host ('[GIT] [ATENTIE] ' + $failure + '. Repository-ul a ramas la jumatatea operatiei (verificati cu git status).')
            if ($null -ne $script:AsideItems) {
                Write-Host ('[GIT] Modificarile locale sunt in ' + (Join-Path $backupDir 'local') + '; se pun la loc automat la pornirea de dupa git rebase --abort.')
            } else {
                Write-Host '[GIT] Pasi: git rebase --abort (daca exista .git\index.lock: inchideti programele care folosesc git si stergeti-l), apoi reporniti.'
            }
        } elseif ($crashed) {
            Write-Host ('[GIT] Sincronizarea s-a oprit: ' + $failure)
        } else {
            Write-Host ('[GIT] ' + $failure + '. Codul local ramane neschimbat.')
        }
        if ($report) {
            if ($outcome -eq 'updated' -or $report.Failed.Count -gt 0) { Write-AsideReport -Report $report -BackupDir $backupDir }
            else { Write-Host '[GIT] Modificarile locale au fost puse la loc, neschimbate.' }
            if ($crashed -and $report.Failed.Count -eq 0) {
                Write-Host ('[GIT] Copia lor ramane si in ' + (Join-Path $backupDir 'local') + '.')
            }
        }
        $untouched = $local.Count - $aside.Count
        if ($outcome -eq 'updated' -and $untouched -gt 0) {
            Write-Host ('[GIT] Modificarile locale din celelalte ' + $untouched + ' fisiere au ramas neatinse.')
        }
        if ($outcome -eq 'updated' -and $plan -eq 'rebase') { Push-LotoMain }
    } else {
        $changes = Invoke-LotoGit -GitArgs @('status', '--porcelain', '-z', '--', '_ISTORIC')
        if ($changes.Code -ne 0) { throw $changes.Text }
        if ($changes.Text) {
            foreach ($entry in $changes.Out.Split([char]0)) {
                if ($entry.StartsWith('?? ', [StringComparison]::Ordinal)) {
                    Write-Host ('[GIT] [REFUZAT] ' + $entry.Substring(3) + ' - neurmarit: commit-ul automat ia numai fisierele urmarite')
                    $script:HistoryRefused = $true
                }
            }
            # -u, nu -A: o copie de conflict din cloud sau un fisier nou nu ajung pe main.
            $add = Invoke-LotoGit -GitArgs @('add', '-u', '--', '_ISTORIC')
            if ($add.Code -ne 0) { throw $add.Text }
            $accepted = @(Select-HistoryAppends)
            if ($script:HistoryRefused) {
                Write-Host '[GIT] Ce e refuzat ramane local, necomis, iar pornirea continua. O schimbare voita se verifica (git status / git diff -- _ISTORIC) si se comite manual.'
            }
            if ($accepted.Count -gt 0) {
                # --only protects code already staged by the user from the auto-commit;
                # explicit paths keep refused files out (it reads the working tree).
                $commit = Invoke-LotoGit -GitArgs (@('commit', '--only', '-m', 'auto: update istoric extrageri', '--') + $accepted)
                if ($commit.Text) { Write-Host $commit.Text }
                if ($commit.Code -ne 0) { throw 'Commit istoric esuat.' }
            }
        }
        # Fetch inainte de numarare: Sync putut fi sarit, iar origin/main local e vechi.
        $fetch = Invoke-LotoGitRetry -GitArgs @('fetch', 'origin')
        if ($fetch.Code -ne 0) { throw $fetch.Text }
        if ($fetch.Text) { Write-Host $fetch.Text }
        $ahead = Invoke-LotoGit -GitArgs @('rev-list', '--count', 'origin/main..HEAD')
        $behind = Invoke-LotoGit -GitArgs @('rev-list', '--count', 'HEAD..origin/main')
        if ($ahead.Code -ne 0 -or $behind.Code -ne 0) { throw 'origin/main indisponibil.' }
        if ([int]$ahead.Text -eq 0) {
            if ($script:HistoryRefused) { Write-Host '[GIT] Nimic de trimis pe origin/main.' }
            else { Write-Host '[GIT] Istoric la zi.' }
            exit 0
        }
        # Inainte de rebase: dupa el, commit-urile retrase ar avea alte SHA-uri.
        $withdrawn = @(Get-WithdrawnCommits)
        if ($withdrawn.Count -gt 0) {
            throw ('main local are ' + $withdrawn.Count + ' commit-uri retrase de pe origin/main (istoria de acolo s-a rescris); nu le trimit din nou. Integrarea ramane manuala.')
        }
        if ([int]$behind.Text -gt 0) {
            $dirtyNow = Invoke-LotoGit -GitArgs @('status', '--porcelain', '--untracked-files=no')
            if ($dirtyNow.Code -ne 0) { throw $dirtyNow.Text }
            if ($dirtyNow.Text) {
                throw 'main local si origin/main au divergat, iar exista modificari necomise. Commit-ul de istoric ramane local.'
            }
            $names = Invoke-LotoGit -GitArgs @('diff', '--name-only', 'origin/main...HEAD')
            if ($names.Code -ne 0) { throw $names.Text }
            $outside = @($names.Text -split "`r?`n" | Where-Object {
                $_ -and $_ -notlike '_ISTORIC/*' -and $_ -ne '_ISTORIC'
            })
            if ($outside.Count -gt 0) {
                throw 'main local are commit-uri in afara _ISTORIC. Integrarea cu origin/main ramane manuala.'
            }
            Write-Host '[GIT] Repun commit-urile de istoric peste origin/main.'
            $rebase = Invoke-LotoGitRetry -GitArgs @('rebase', 'origin/main')
            if ($rebase.Text) { Write-Host $rebase.Text }
            if ($rebase.Code -ne 0) {
                Invoke-LotoGit -GitArgs @('rebase', '--abort') | Out-Null
                throw 'Rebase esuat - commit-urile raman locale, repository-ul nu ramane in rebase.'
            }
        }
        $push = Invoke-LotoGitRetry -GitArgs @('push', 'origin', 'main')
        if ($push.Text) { Write-Host $push.Text }
        if ($push.Code -ne 0) { throw 'Push esuat - commit-urile raman locale pentru reincercare.' }
        Write-Host '[GIT] Push origin/main reusit.'
    }
    exit 0
} catch {
    Write-Host ('[GIT] ' + $_.Exception.Message)
    exit 1
}

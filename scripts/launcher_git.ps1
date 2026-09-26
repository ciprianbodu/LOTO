param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Sync', 'PushHistory', 'Cleanup', 'Detect', 'EnsureGit')]
    [string]$Mode,
    [Parameter(Mandatory = $true)]
    [string]$ProjectDir,
    [string]$SnapshotDir
)

# Sync is called ONLY from an immutable launcher copy outside the repository.
# No downloaded fragments, forced reset, checkout, stash, or user-file deletion.
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

function Invoke-LotoGit {
    param([string[]]$GitArgs, [int]$TimeoutSeconds = 0)
    if ($TimeoutSeconds -le 0) { $TimeoutSeconds = $script:GitTimeoutSeconds }
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $gitExe
    $info.WorkingDirectory = (Get-Location).Path
    # Arguments are fixed below; quote separately, never run through a shell.
    $info.Arguments = (($GitArgs | ForEach-Object { '"' + $_.Replace('"', '\"') + '"' }) -join ' ')
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.RedirectStandardInput = $true
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
        # Only this Git invocation and its children; never an application worker.
        & "$env:SystemRoot\System32\taskkill.exe" /F /T /PID $process.Id 2>&1 | Out-Null
        $process.Dispose()
        $message = 'Git a depasit ' + $TimeoutSeconds + ' s la "git ' + ($GitArgs -join ' ') + '"; operatia s-a oprit.'
        if ($info.WorkingDirectory -match 'My Drive|Google Drive|OneDrive|Dropbox') {
            $message += ' Proiectul este intr-un folder sincronizat in cloud (' + $info.WorkingDirectory + '); acolo git citeste lent fiecare fisier. Tineti repository-ul pe disc local.'
        }
        throw $message
    }
    $result = [pscustomobject]@{
        Code = $process.ExitCode
        Text = ($stdout.Result + $stderr.Result).Trim()
    }
    $process.Dispose()
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
        $dirty = Invoke-LotoGit -GitArgs @('status', '--porcelain', '--untracked-files=no')
        if ($dirty.Code -ne 0) { throw $dirty.Text }
        if ($dirty.Text) {
            # Nu facem merge peste fisiere necomise, dar fetch-ul aduce
            # origin/main. Altfel push-ul de istoric vede o referinta veche.
            Write-Host '[GIT] Modificari locale necomise - pastrez fisierele locale.'
            $fetch = Invoke-LotoGitRetry -GitArgs @('fetch', 'origin')
            if ($fetch.Code -ne 0) {
                Write-Host '[GIT] Fetch esuat - fisierele locale raman neschimbate.'
                exit 0
            }
            $behind = Invoke-LotoGit -GitArgs @('rev-list', '--count', 'HEAD..origin/main')
            if ($behind.Code -eq 0 -and [int]$behind.Text -gt 0) {
                Write-Host '[GIT] origin/main are commit-uri noi. Nu le aplic peste modificarile necomise.'
            }
            exit 0
        }
        $fetch = Invoke-LotoGitRetry -GitArgs @('fetch', 'origin')
        if ($fetch.Code -ne 0) { throw $fetch.Text }
        $ahead = Invoke-LotoGit -GitArgs @('rev-list', '--count', 'origin/main..HEAD')
        $behind = Invoke-LotoGit -GitArgs @('rev-list', '--count', 'HEAD..origin/main')
        if ($ahead.Code -ne 0 -or $behind.Code -ne 0) { throw 'origin/main indisponibil.' }
        if ([int]$behind.Text -eq 0) {
            Write-Host '[GIT] Cod la zi; eventualele commit-uri locale sunt pastrate.'
            exit 0
        }
        if ([int]$ahead.Text -gt 0) {
            Write-Host '[GIT] main local si origin/main au divergat - este necesara integrarea manuala.'
            exit 0
        }
        $merge = Invoke-LotoGit -GitArgs @('merge', '--ff-only', 'origin/main')
        if ($merge.Text) { Write-Host $merge.Text }
        if ($merge.Code -ne 0) { throw 'Actualizarea nu a reusit; fisierele locale sunt pastrate.' }
        Write-Host '[GIT] Aplicatia si lansatoarele au fost actualizate impreuna.'
    } else {
        $changes = Invoke-LotoGit -GitArgs @('status', '--porcelain', '--', '_ISTORIC')
        if ($changes.Code -ne 0) { throw $changes.Text }
        if ($changes.Text) {
            $add = Invoke-LotoGit -GitArgs @('add', '-A', '--', '_ISTORIC')
            if ($add.Code -ne 0) { throw $add.Text }
            # --only protects code already staged by the user from the auto-commit.
            $commit = Invoke-LotoGit -GitArgs @('commit', '--only', '-m', 'auto: update istoric extrageri', '--', '_ISTORIC')
            if ($commit.Text) { Write-Host $commit.Text }
            if ($commit.Code -ne 0) { throw 'Commit istoric esuat.' }
        }
        # Fetch inainte de numarare: Sync putut fi sarit, iar origin/main local e vechi.
        $fetch = Invoke-LotoGitRetry -GitArgs @('fetch', 'origin')
        if ($fetch.Code -ne 0) { throw $fetch.Text }
        if ($fetch.Text) { Write-Host $fetch.Text }
        $ahead = Invoke-LotoGit -GitArgs @('rev-list', '--count', 'origin/main..HEAD')
        $behind = Invoke-LotoGit -GitArgs @('rev-list', '--count', 'HEAD..origin/main')
        if ($ahead.Code -ne 0 -or $behind.Code -ne 0) { throw 'origin/main indisponibil.' }
        if ([int]$ahead.Text -eq 0) {
            Write-Host '[GIT] Istoric la zi.'
            exit 0
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

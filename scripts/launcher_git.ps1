param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Sync', 'PushHistory', 'Cleanup', 'Detect')]
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
        $snapshot = [IO.Path]::GetFullPath($SnapshotDir).TrimEnd('\')
        $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
        if ((Split-Path $snapshot) -eq $tempRoot -and
            (Split-Path $snapshot -Leaf) -match '^loto-launch-\d+-\d+$') {
            # Remove only our three known files; never recursively delete extras.
            foreach ($name in @('START_8000.bat', 'ACTUALIZARI.bat', 'launcher_git.ps1')) {
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

function Resolve-LotoGit {
    # Explorer/CMD need not inherit the PATH supplied by an editor or Codex.
    # Resolve here for BOTH Sync and PushHistory; do not change Windows settings.
    $candidates = @()
    if ($env:LOTO_GIT_EXE) { $candidates += $env:LOTO_GIT_EXE.Trim('"') }
    $command = Get-Command git.exe -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($command) { $candidates += $command.Source }

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

function Invoke-LotoGit {
    param([string[]]$GitArgs, [int]$TimeoutSeconds = 45)
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
        throw 'Git a depasit timpul de asteptare; operatia s-a oprit.'
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
    param([string[]]$GitArgs, [int]$TimeoutSeconds = 45)
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

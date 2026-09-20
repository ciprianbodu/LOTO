param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Sync', 'PushHistory', 'Cleanup')]
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

Set-Location -LiteralPath $ProjectDir
$gitExe = (Get-Command git.exe -ErrorAction SilentlyContinue).Source
if (-not $gitExe) {
    Write-Host '[GIT] Git indisponibil - pastrez codul si istoricul local.'
    exit 0
}

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

try {
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
    $hooks = Invoke-LotoGit -GitArgs @('config', 'core.hooksPath', 'scripts/git-hooks')
    if ($hooks.Code -ne 0) { throw $hooks.Text }

    if ($Mode -eq 'Sync') {
        $dirty = Invoke-LotoGit -GitArgs @('status', '--porcelain', '--untracked-files=no')
        if ($dirty.Code -ne 0) { throw $dirty.Text }
        if ($dirty.Text) {
            Write-Host '[GIT] Modificari locale necomise - pastrez versiunea locala integral.'
            exit 0
        }
        $fetch = Invoke-LotoGit -GitArgs @('fetch', 'origin')
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
        # Retry a previous failed push even if the CSV has no new rows today.
        $ahead = Invoke-LotoGit -GitArgs @('rev-list', '--count', 'origin/main..HEAD')
        if ($ahead.Code -eq 0 -and [int]$ahead.Text -eq 0) {
            Write-Host '[GIT] Istoric la zi.'
            exit 0
        }
        $push = Invoke-LotoGit -GitArgs @('push', 'origin', 'main')
        if ($push.Text) { Write-Host $push.Text }
        if ($push.Code -ne 0) { throw 'Push esuat - commit-urile raman locale pentru reincercare.' }
        Write-Host '[GIT] Push origin/main reusit.'
    }
    exit 0
} catch {
    Write-Host ('[GIT] ' + $_.Exception.Message)
    exit 1
}

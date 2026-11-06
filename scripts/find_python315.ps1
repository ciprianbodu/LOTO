$ErrorActionPreference = 'SilentlyContinue'

$candidates = [System.Collections.Generic.List[string]]::new()
$seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)

function Add-Candidate([string] $Path) {
    if (-not [string]::IsNullOrWhiteSpace($Path)) {
        $Path = $Path.Trim()
        if ($seen.Add($Path)) {
            $candidates.Add($Path)
        }
    }
}

# Resolve the launcher's concrete executable; stale registrations are rejected
# by the same executable/version probe as every other candidate below.
if (Get-Command py -ErrorAction SilentlyContinue) {
    try {
        Add-Candidate (& py -3.15 -c 'import sys; print(sys.executable)' 2>$null)
    }
    catch {}
}

Add-Candidate $env:LOTO_PYTHON315
Add-Candidate 'D:\_BUILD\Python315\python.exe'
if ($env:LOCALAPPDATA) {
    Add-Candidate (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python315\python.exe')
}
foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
    if ($base) {
        Add-Candidate (Join-Path $base 'Python315\python.exe')
    }
}

foreach ($root in @(
    'HKCU:\Software\Python\PythonCore\3.15\InstallPath',
    'HKLM:\Software\Python\PythonCore\3.15\InstallPath',
    'HKLM:\Software\WOW6432Node\Python\PythonCore\3.15\InstallPath'
)) {
    try {
        $installPath = (Get-Item -LiteralPath $root).GetValue('')
        Add-Candidate (Join-Path $installPath 'python.exe')
    }
    catch {}
}

foreach ($commandName in @('python3.15', 'python')) {
    foreach ($command in @(Get-Command $commandName -All -ErrorAction SilentlyContinue)) {
        Add-Candidate $command.Source
    }
}

$best = $null
foreach ($candidate in $candidates) {
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        continue
    }
    try {
        $result = @(& $candidate -c "import sys; v=sys.version_info; print(sys.executable + '|' + '.'.join(map(str, v[:3]))) if v[:2] == (3, 15) and v.releaselevel == 'final' else sys.exit(1)" 2>$null)
        if ($LASTEXITCODE -ne 0 -or $result.Count -ne 1) {
            continue
        }
        $line = [string]$result[0]
        if ($line -match '^(?<executable>.+)\|(?<version>3\.15\.\d+)$') {
            $version = [version]$Matches.version
            if ($null -eq $best -or $version -gt $best.Version) {
                $best = [pscustomobject]@{ Version = $version; Line = $line }
            }
        }
    }
    catch {}
}

if ($null -ne $best) {
    Write-Output $best.Line
    exit 0
}
exit 1

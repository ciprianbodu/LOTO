$ErrorActionPreference = 'SilentlyContinue'

$candidates = [System.Collections.Generic.List[string]]::new()

function Add-Candidate([string] $Path) {
    if (-not [string]::IsNullOrWhiteSpace($Path) -and -not $candidates.Contains($Path)) {
        $candidates.Add($Path)
    }
}

# A launcher can survive after its registered interpreter was removed.
# Resolve its concrete executable and validate that executable below.
if (Get-Command py -ErrorAction SilentlyContinue) {
    try {
        Add-Candidate (& py -3.14 -c 'import sys; print(sys.executable)' 2>$null)
    }
    catch {}
}

Add-Candidate (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python314\python.exe')

foreach ($root in @(
    'HKCU:\Software\Python\PythonCore\3.14\InstallPath',
    'HKLM:\Software\Python\PythonCore\3.14\InstallPath',
    'HKLM:\Software\WOW6432Node\Python\PythonCore\3.14\InstallPath'
)) {
    try {
        $installPath = (Get-Item -LiteralPath $root).GetValue('')
        Add-Candidate (Join-Path $installPath 'python.exe')
    }
    catch {}
}

foreach ($commandName in @('python3.14', 'python')) {
    try {
        Add-Candidate (Get-Command $commandName -ErrorAction Stop).Source
    }
    catch {}
}

foreach ($candidate in $candidates) {
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        continue
    }
    try {
        $result = & $candidate -c "import sys; assert sys.version_info[:2] == (3, 14); print(sys.executable + '|' + sys.version.split()[0])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $result -match '^.+\|3\.14\.\d+$') {
            Write-Output $result
            exit 0
        }
    }
    catch {}
}

exit 1

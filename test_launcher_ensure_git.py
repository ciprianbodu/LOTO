"""`launcher_git.ps1 -Mode EnsureGit` cu winget si git simulate.

Ruleaza cu PowerShell 7 (`pwsh` in PATH sau LOTO_PWSH) pe Linux/macOS: git-ul
simulat e un script shell, care pe Windows nu poate trece drept git.exe.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
HELPER = ROOT / "scripts" / "launcher_git.ps1"
PWSH = os.environ.get("LOTO_PWSH") or shutil.which("pwsh")
pytestmark = pytest.mark.skipif(
    os.name == "nt" or not PWSH, reason="git simulat ca script shell; cere pwsh"
)

FAKE_WINGET = r"""
Add-Content -LiteralPath (Join-Path $env:FAKE_DIR 'winget.log') -Value ($args -join ' ')
if ($env:FAKE_NEWVER -and ($args[0] -eq 'upgrade' -or $args[0] -eq 'install')) {
    $dir = Join-Path $env:FAKE_PF 'Git/cmd'
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $git = Join-Path $dir 'git.exe'
    Set-Content -LiteralPath $git -Value "#!/bin/sh`necho 'git version $env:FAKE_NEWVER'"
    & chmod +x $git
}
exit [int]$env:FAKE_RC
"""


def _fake_git(path, version):
    path.parent.mkdir(parents=True)
    path.write_text(f'#!/bin/sh\necho "git version {version}"\n')
    path.chmod(0o755)


def _run(tmp_path, *, installed=None, rc=0, new=None, winget=True, path_git=None,
         chosen_git=None, mode="EnsureGit"):
    """`path_git` = (folder relativ, versiune) pentru un git.exe pus in PATH;
    `chosen_git` = acelasi lucru, indicat prin LOTO_GIT_EXE."""
    pf = tmp_path / "pf"
    pf.mkdir()
    if installed:
        _fake_git(pf / "Git" / "cmd" / "git.exe", installed)
    search = ["/usr/bin", "/bin"]
    if path_git:
        folder, version = path_git
        _fake_git(tmp_path / folder / "git.exe", version)
        search.insert(0, str(tmp_path / folder))
    chosen = ""
    if chosen_git:
        folder, version = chosen_git
        _fake_git(tmp_path / folder / "git.exe", version)
        chosen = str(tmp_path / folder / "git.exe")
    fake = tmp_path / "winget.ps1"
    fake.write_text(FAKE_WINGET)
    env = {
        "PATH": os.pathsep.join(search),
        "HOME": str(tmp_path),
        "ProgramFiles": str(pf),
        "FAKE_DIR": str(tmp_path),
        "FAKE_PF": str(pf),
        "FAKE_RC": str(rc),
        "FAKE_NEWVER": new or "",
        "LOTO_WINGET_EXE": str(fake) if winget else "",
        "LOTO_GIT_EXE": chosen,
    }
    out = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(HELPER), "-Mode", mode, "-ProjectDir", str(tmp_path)],
        env=env, capture_output=True, text=True, timeout=120,
    )
    log = tmp_path / "winget.log"
    return out.returncode, out.stdout, log.read_text().strip() if log.exists() else ""


def test_missing_git_is_installed_with_winget(tmp_path):
    rc, out, log = _run(tmp_path, installed=None, new="2.51.0.windows.1")
    assert rc == 0
    assert log.startswith("install --id Git.Git -e --source winget --silent")
    assert "Git for Windows 2.51.0.windows.1 instalat" in out


def test_older_git_is_upgraded(tmp_path):
    rc, out, log = _run(tmp_path, installed="2.50.1.windows.1", new="2.51.0.windows.1")
    assert rc == 0 and log.startswith("upgrade --id Git.Git -e")
    assert "actualizat: 2.50.1.windows.1 -> 2.51.0.windows.1" in out


@pytest.mark.parametrize("code", [0, -1978335189])
def test_current_git_is_reported_up_to_date(tmp_path, code):
    rc, out, _ = _run(tmp_path, installed="2.51.0.windows.1", rc=code)
    assert rc == 0 and "2.51.0.windows.1 este la zi" in out


def test_git_not_managed_by_winget_is_reported(tmp_path):
    rc, out, _ = _run(tmp_path, installed="2.49.0.windows.1", rc=-1978335212)
    assert rc == 0 and "nu e gestionat de winget" in out


def test_winget_error_never_blocks(tmp_path):
    rc, out, _ = _run(tmp_path, installed="2.51.0.windows.1", rc=1)
    assert rc == 0 and "cod 1" in out and "Raman pe Git 2.51.0.windows.1" in out


def test_declined_or_failed_upgrade_is_not_reported_as_a_failed_check(tmp_path):
    """Confirmarea de administrator refuzata sau instalatorul esuat: winget a gasit
    versiunea noua, deci mesajul nu are voie sa spuna ca verificarea n-a mers."""
    rc, out, _ = _run(tmp_path, installed="2.50.1.windows.1", rc=-1978335226)
    assert rc == 0 and "nu a putut verifica" not in out
    assert "Actualizarea Git nu s-a aplicat - winget cod -1978335226" in out
    assert "confirmare de administrator refuzata" in out and "Raman pe Git 2.50.1" in out


def test_git_on_path_installed_another_way_is_kept(tmp_path):
    """Git din scoop sau alt folder: nu se instaleaza un al doilea Git."""
    rc, out, log = _run(tmp_path, path_git=("scoop/shims", "2.50.0"))
    assert rc == 0 and log == ""
    assert "Git 2.50.0 gasit prin PATH" in out and "Il pastrez" in out


def test_git_chosen_with_loto_git_exe_is_kept(tmp_path):
    """Lansatorul recomanda LOTO_GIT_EXE; Sync il foloseste primul, deci EnsureGit
    nu instaleaza peste el un al doilea Git."""
    rc, out, log = _run(tmp_path, chosen_git=("PortableGit/cmd", "2.49.0.windows.1"))
    assert rc == 0 and log == ""
    assert "gasit prin LOTO_GIT_EXE" in out and "Il pastrez" in out


def test_codex_git_chosen_with_loto_git_exe_does_not_count(tmp_path):
    folder = ".cache/codex-runtimes/codex-primary-runtime/dependencies/native/git/cmd"
    rc, out, log = _run(
        tmp_path, chosen_git=(folder, "2.47.1.windows.1"), new="2.51.0.windows.1"
    )
    assert rc == 0 and log.startswith("install --id Git.Git -e")


def test_sync_prefers_git_for_windows_over_codex_git_on_path(tmp_path):
    """Resolve-LotoGit (Sync, PushHistory, Detect): Git-ul Codex din PATH-ul unui
    editor vine dupa Git for Windows, cel verificat de EnsureGit."""
    folder = ".cache/codex-runtimes/codex-primary-runtime/dependencies/native/git/cmd"
    _, out, _ = _run(
        tmp_path, installed="2.51.0.windows.1", path_git=(folder, "2.47.1.windows.1"),
        mode="Detect",
    )
    assert "[GIT] Executabil: " + str(tmp_path / "pf" / "Git" / "cmd" / "git.exe") in out


def test_sync_keeps_a_non_codex_git_on_path_first(tmp_path):
    _, out, _ = _run(
        tmp_path, installed="2.51.0.windows.1", path_git=("scoop/shims", "2.50.0"),
        mode="Detect",
    )
    assert "[GIT] Executabil: " + str(tmp_path / "scoop" / "shims" / "git.exe") in out


def test_codex_portable_git_on_path_does_not_count(tmp_path):
    folder = ".cache/codex-runtimes/codex-primary-runtime/dependencies/native/git/cmd"
    rc, out, log = _run(
        tmp_path, path_git=(folder, "2.47.1.windows.1"), new="2.51.0.windows.1"
    )
    assert rc == 0 and log.startswith("install --id Git.Git -e")
    assert "Git for Windows 2.51.0.windows.1 instalat" in out


def test_failed_install_and_missing_winget_point_to_manual_install(tmp_path):
    rc, out, _ = _run(tmp_path, installed=None, rc=5)
    assert rc == 0 and "nu s-a instalat - winget cod 5" in out
    other = tmp_path / "nowinget"
    other.mkdir()
    rc, out, log = _run(other, installed=None, winget=False)
    assert rc == 0 and log == ""
    assert "winget nu e disponibil" in out and "git-scm.com/download/win" in out


def test_cleanup_removes_the_git_check_marker_with_the_temp_copy(tmp_path):
    """Fara 'git-checked' in lista, folderul temporar ramanea dupa fiecare rulare."""
    boot = tmp_path / "loto-launch-12-34"
    boot.mkdir()
    for name in ("ACTUALIZARI.bat", "launcher_git.ps1", "git-checked"):
        (boot / name).write_text("x")
    out = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(HELPER), "-Mode", "Cleanup",
         "-ProjectDir", str(ROOT), "-SnapshotDir", str(boot)],
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "TMPDIR": str(tmp_path)},
        capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert not boot.exists()

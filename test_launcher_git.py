"""Real Windows/Git integration, isolated from the user's repo and network."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parent
HELPER = ROOT / 'scripts' / 'launcher_git.ps1'
POWERSHELL = (Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' /
              'WindowsPowerShell' / 'v1.0' / 'powershell.exe')
pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows launcher integration')

# Istoric real din registru: verifica_istoric.py il valideaza ca pe cel versionat.
DRAWS = '_ISTORIC/loto_6_49.csv'
README = '_ISTORIC/externe/README.md'
HEADER = 'date,n1,n2,n3,n4,n5,n6\n'
ROWS = '01-10-2026,1,2,3,4,5,6\n04-10-2026,7,8,9,10,11,12\n'
NEW_ROW = '05-10-2026,13,21,28,34,40,49\n'


def git(cwd, *args):
    result = subprocess.run(
        ['git', '-c', 'core.hooksPath=NUL', *args], cwd=cwd,
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def commit(repo, message):
    git(repo, 'add', '-A')
    git(repo, 'commit', '-m', message)


def append(repo, text, name=DRAWS):
    """Ca update_csv.py / update_externe.py: randuri noi la finalul fisierului."""
    path = repo / name
    path.write_bytes(path.read_bytes() + text.encode())


@pytest.fixture
def repos(tmp_path):
    origin = tmp_path / 'origin.git'
    git(tmp_path, 'init', '--bare', str(origin))
    seed = tmp_path / 'seed'
    git(tmp_path, 'clone', str(origin), str(seed))
    git(seed, 'checkout', '-b', 'main')
    for key, value in [('user.name', 'Audit test'), ('user.email', 'audit@example.invalid')]:
        git(seed, 'config', key, value)
    (seed / '_ISTORIC' / 'externe').mkdir(parents=True)
    # Ca in repo: LF pe CSV-uri, indiferent de core.autocrlf al statiei.
    (seed / '.gitattributes').write_text('_ISTORIC/*.csv text eol=lf\n')
    (seed / DRAWS).write_bytes((HEADER + ROWS).encode())
    (seed / README).write_bytes(b'Sursele istoricelor externe.\n')
    (seed / 'code.txt').write_text('old\n')
    (seed / 'scripts').mkdir()
    shutil.copyfile(HELPER, seed / 'scripts' / HELPER.name)
    commit(seed, 'initial')
    git(seed, 'push', '-u', 'origin', 'main')
    local = tmp_path / 'My Drive (audit)' / 'local'
    git(tmp_path, 'clone', '-b', 'main', str(origin), str(local))
    for key, value in [('user.name', 'Audit test'), ('user.email', 'audit@example.invalid')]:
        git(local, 'config', key, value)
    return local, seed, origin


@pytest.fixture
def gitless_env():
    """Model Explorer's stale PATH while supplying an installed Git explicitly."""
    git_exe = shutil.which('git')
    if not git_exe:
        pytest.skip('Git is required to prepare the isolated repositories')
    env = os.environ.copy()
    system_root = Path(os.environ['SystemRoot'])
    env['PATH'] = os.pathsep.join(map(str, [
        system_root / 'System32', POWERSHELL.parent, system_root,
    ]))
    env['LOTO_GIT_EXE'] = git_exe
    assert shutil.which('git', path=env['PATH']) is None
    return env


def run_helper(local, mode='Sync', env=None, *, git_processes=None, python=True):
    args = [str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass']
    # Ca START_8000 / ACTUALIZARI: PushHistory primeste Python-ul venv-ului.
    python_exe = sys.executable if mode == 'PushHistory' and python else None
    if git_processes is None:
        args += ['-File', str(HELPER), '-Mode', mode, '-ProjectDir', str(local)]
        if python_exe:
            args += ['-PythonExe', python_exe]
    else:
        # Numai clonele fixture-ului folosesc acest inventar simulat. Helperul
        # și comenzile Git sunt reale; procesele Git ale editorului nu pot
        # schimba sensul testului și nu sunt nici oprite, nici inspectate aici.
        inventory = 'present' if git_processes else 'empty'
        result = (
            "[pscustomobject]@{ ProcessName = 'git'; Id = -1 }"
            if git_processes else 'return'
        )
        def ps_literal(value):
            return "'" + str(value).replace("'", "''") + "'"
        command = (
            "function global:Get-Process { "
            "[CmdletBinding()] param([string[]] $Name); "
            "if ($Name -ne 'git') { throw 'Unexpected process query in test' }; "
            f"Write-Host '[TEST] Git process inventory: {inventory}'; "
            f"{result} }}; "
            f"& {ps_literal(HELPER)} -Mode {ps_literal(mode)} "
            f"-ProjectDir {ps_literal(local)}"
        )
        if python_exe:
            command += f" -PythonExe {ps_literal(python_exe)}"
        args += ['-Command', command]
    p = subprocess.run(
        args, capture_output=True, text=True, errors='replace', timeout=60, env=env,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    if git_processes is not None:
        # Nu acceptăm un test verde dacă helperul a ocolit inventarul: markerul
        # este emis exclusiv de funcția Get-Process consultată de gardă.
        assert f'[TEST] Git process inventory: {inventory}' in p.stdout
    return p.stdout


def advance(seed):
    (seed / 'code.txt').write_text('new\n')
    commit(seed, 'remote update')
    git(seed, 'push', 'origin', 'main')


@pytest.mark.parametrize('git_on_path', [True, False])
def test_fast_forward_updates_whole_checkout_and_keeps_local_files(
    repos, gitless_env, git_on_path,
):
    local, seed, _ = repos
    extra = local / 'personal.bat'
    extra.write_text('do not delete')
    advance(seed)
    run_helper(local, env=None if git_on_path else gitless_env)
    assert git(local, 'rev-parse', 'HEAD') == git(seed, 'rev-parse', 'HEAD')
    assert (local / 'code.txt').read_text() == 'new\n'
    assert extra.read_text() == 'do not delete'


@pytest.mark.parametrize('state', ['dirty', 'ahead', 'diverged', 'other_branch'])
def test_sync_preserves_user_work(repos, state):
    local, seed, _ = repos
    if state == 'other_branch':
        git(local, 'checkout', '-b', 'personal')
    (local / 'code.txt').write_text('personal\n')
    if state != 'dirty':
        commit(local, 'personal work')
    if state != 'ahead':
        advance(seed)
    head = git(local, 'rev-parse', 'HEAD')
    run_helper(local)
    assert git(local, 'rev-parse', 'HEAD') == head
    assert (local / 'code.txt').read_text() == 'personal\n'


@pytest.mark.parametrize('git_on_path', [True, False])
def test_history_auto_commit_does_not_include_staged_code(
    repos, gitless_env, git_on_path,
):
    local, _, origin = repos
    (local / 'code.txt').write_text('unfinished code\n')
    git(local, 'add', 'code.txt')
    append(local, NEW_ROW)
    out = run_helper(local, 'PushHistory', env=None if git_on_path else gitless_env)
    assert git(local, 'show', 'HEAD:code.txt') == 'old'
    assert git(local, 'diff', '--cached', '--name-only') == 'code.txt'
    assert git(origin, 'show', f'main:{DRAWS}') == (HEADER + ROWS + NEW_ROW).strip()
    assert '[REFUZAT]' not in out


def test_sync_with_local_edits_fetches_without_touching_files(repos):
    local, seed, _ = repos
    (local / 'code.txt').write_text('personal\n')
    advance(seed)
    out = run_helper(local)
    assert 'pastrez fisierele locale' in out
    assert 'commit-uri noi' in out
    assert git(local, 'rev-parse', 'HEAD') != git(seed, 'rev-parse', 'HEAD')
    assert git(local, 'rev-parse', 'origin/main') == git(seed, 'rev-parse', 'HEAD')
    assert (local / 'code.txt').read_text() == 'personal\n'


def test_stale_packed_refs_lock_does_not_block_fast_forward(repos):
    local, seed, _ = repos
    advance(seed)
    lock = local / '.git' / 'packed-refs.lock'
    lock.write_text('', encoding='utf-8')
    output = run_helper(local, git_processes=False)
    assert '[GIT] Lock vechi eliminat: packed-refs.lock' in output
    assert not lock.exists()
    assert git(local, 'rev-parse', 'HEAD') == git(seed, 'rev-parse', 'HEAD')


def test_git_process_preserves_packed_refs_lock(repos):
    local, seed, _ = repos
    advance(seed)
    lock = local / '.git' / 'packed-refs.lock'
    lock.write_text('owned by a running Git process', encoding='utf-8')
    output = run_helper(local, git_processes=True)
    assert lock.read_text(encoding='utf-8') == 'owned by a running Git process'
    assert '[GIT] Lock vechi eliminat:' not in output


def test_history_push_replays_draw_commit_onto_newer_main(repos):
    local, seed, origin = repos
    advance(seed)
    append(local, NEW_ROW)
    out = run_helper(local, 'PushHistory')
    assert 'Repun commit-urile de istoric' in out
    assert git(origin, 'show', f'main:{DRAWS}') == (HEADER + ROWS + NEW_ROW).strip()
    assert git(origin, 'show', 'main:code.txt') == 'new'
    assert git(local, 'rev-parse', 'HEAD') == git(origin, 'rev-parse', 'main')
    assert not (local / '.git' / 'rebase-merge').exists()


def test_history_push_leaves_diverged_code_commits_untouched(repos):
    local, seed, origin = repos
    (local / 'code.txt').write_text('personal\n', encoding='utf-8')
    commit(local, 'personal work')
    advance(seed)
    append(local, NEW_ROW)
    head = git(local, 'rev-parse', 'HEAD')
    p = subprocess.run(
        [str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
         str(HELPER), '-Mode', 'PushHistory', '-ProjectDir', str(local),
         '-PythonExe', sys.executable],
        capture_output=True, text=True, errors='replace', timeout=60,
    )
    assert p.returncode != 0, p.stdout + p.stderr
    assert 'in afara _ISTORIC' in (p.stdout + p.stderr)
    assert git(local, 'log', '-1', '--format=%s') == 'auto: update istoric extrageri'
    assert git(local, 'rev-parse', 'HEAD') != head
    assert git(origin, 'rev-parse', 'main') == git(seed, 'rev-parse', 'HEAD')
    assert not (local / '.git' / 'rebase-merge').exists()
    assert (local / 'code.txt').read_text() == 'personal\n'


def test_history_retries_unpushed_commit_with_no_csv_changes(repos):
    local, _, origin = repos
    append(local, NEW_ROW)
    commit(local, 'local only')
    run_helper(local, 'PushHistory')
    assert git(origin, 'rev-parse', 'main') == git(local, 'rev-parse', 'HEAD')


def _worktree(local):
    path = local / DRAWS
    return path.read_bytes() if path.exists() else None


@pytest.mark.parametrize('change, reason', [
    # Rand sters (si unul nou adaugat dupa el, cum ar face actualizatorul).
    ('deleted_row', 'linii sterse sau modificate: 1'),
    # Ultimul rand taiat la jumatate.
    ('truncated_row', 'linii sterse sau modificate: 1'),
    # Excel: separator ';', date ZZ.LL.AAAA - antetul si fiecare rand se schimba.
    ('excel', 'linii sterse sau modificate: 3'),
    ('deleted_file', 'fisier sters'),
])
def test_history_refuses_anything_but_appended_rows(repos, change, reason):
    local, _, origin = repos
    path = local / DRAWS
    first, last = ROWS.splitlines()
    if change == 'deleted_row':
        path.write_bytes((HEADER + first + '\n' + NEW_ROW).encode())
    elif change == 'truncated_row':
        path.write_bytes((HEADER + first + '\n' + last[:12]).encode())
    elif change == 'excel':
        excel = (HEADER + ROWS).replace(',', ';').replace('-10-', '.10.')
        path.write_bytes(excel.encode())
    else:
        path.unlink()
    on_disk = _worktree(local)
    head = git(local, 'rev-parse', 'HEAD')

    out = run_helper(local, 'PushHistory')  # exit 0: pornirea continua

    assert f'[GIT] [REFUZAT] {DRAWS} - {reason}' in out
    assert 'Ce e refuzat ramane local, necomis' in out
    assert '[GIT] Nimic de trimis pe origin/main.' in out
    assert git(local, 'rev-parse', 'HEAD') == head
    assert git(origin, 'rev-parse', 'main') == head
    # Schimbarea ramane pe disc, neatinsa, dar nu si in index.
    assert _worktree(local) == on_disk
    assert git(local, 'diff', '--cached', '--name-only') == ''
    assert git(local, 'diff', '--name-only') == DRAWS


def test_history_commits_only_tracked_files(repos):
    """Copia de conflict din cloud nu ajunge pe main; extragerea noua, da."""
    local, _, origin = repos
    conflict = local / '_ISTORIC' / 'loto_6_49 (1).csv'
    conflict.write_bytes((HEADER + ROWS + NEW_ROW).encode())
    append(local, NEW_ROW)

    out = run_helper(local, 'PushHistory')

    assert '[GIT] [REFUZAT] _ISTORIC/loto_6_49 (1).csv - neurmarit' in out
    assert git(origin, 'show', f'main:{DRAWS}') == (HEADER + ROWS + NEW_ROW).strip()
    pushed = git(origin, 'ls-tree', '-r', '--name-only', 'main').splitlines()
    assert sorted(pushed) == ['.gitattributes', README, DRAWS, 'code.txt',
                              'scripts/launcher_git.ps1']
    assert conflict.read_bytes() == (HEADER + ROWS + NEW_ROW).encode()
    assert git(local, 'ls-files', '--', '_ISTORIC/loto_6_49 (1).csv') == ''
    assert git(local, 'rev-parse', 'HEAD') == git(origin, 'rev-parse', 'main')


def test_history_refuses_only_the_bad_file(repos):
    local, _, origin = repos
    (local / README).write_bytes(b'Editat de mana.\n')
    append(local, NEW_ROW)

    out = run_helper(local, 'PushHistory')

    assert f'[GIT] [REFUZAT] {README} - nu e CSV' in out
    assert git(origin, 'show', f'main:{DRAWS}') == (HEADER + ROWS + NEW_ROW).strip()
    assert git(origin, 'show', f'main:{README}') == 'Sursele istoricelor externe.'
    assert git(local, 'diff', '--name-only') == README
    assert git(local, 'diff', '--cached', '--name-only') == ''


@pytest.mark.parametrize('row, reason', [
    ('10.09.2026;1;2;3;4;5;6\n', 'randul 4: astept 7 valori separate prin virgula'),
    ('05-10-2026,1,2,3,4,5,50\n', 'randul 4: numere invalide pentru 6/49'),
    ('5 oct 2026,1,2,3,4,5,6\n', 'randul 4: data nu e ZZ-LL-AAAA'),
])
def test_history_validation_refuses_malformed_appended_row(repos, row, reason):
    """Numai adaugari, deci git le lasa; validarea proiectului le opreste."""
    local, _, origin = repos
    append(local, row)
    head = git(local, 'rev-parse', 'HEAD')

    out = run_helper(local, 'PushHistory')

    assert f'[GIT] [REFUZAT] {DRAWS} - {reason}' in out
    assert git(origin, 'rev-parse', 'main') == head
    assert git(local, 'diff', '--cached', '--name-only') == ''


def test_history_validation_that_cannot_run_commits_nothing(repos):
    """Python care nu porneste nu valideaza nimic; extragerea se comite data viitoare."""
    local, _, origin = repos
    append(local, NEW_ROW)
    head = git(local, 'rev-parse', 'HEAD')
    broken = dict(os.environ, PYTHONHOME=str(local.parent / 'no-python-home'))

    out = run_helper(local, 'PushHistory', env=broken)

    assert f'[GIT] [REFUZAT] {DRAWS} - validarea istoricului nu a rulat (cod ' in out
    assert git(origin, 'rev-parse', 'main') == head
    run_helper(local, 'PushHistory')
    assert git(origin, 'show', f'main:{DRAWS}') == (HEADER + ROWS + NEW_ROW).strip()


def test_history_without_python_keeps_the_git_checks(repos):
    local, _, origin = repos
    (local / README).write_bytes(b'Editat de mana.\n')
    append(local, NEW_ROW)

    out = run_helper(local, 'PushHistory', python=False)

    assert 'Validarea istoricului cu Python nu e disponibila' in out
    assert f'[GIT] [REFUZAT] {README} - nu e CSV' in out
    assert git(origin, 'show', f'main:{DRAWS}') == (HEADER + ROWS + NEW_ROW).strip()


def test_refused_change_does_not_block_pushing_earlier_history(repos):
    local, _, origin = repos
    append(local, NEW_ROW)
    commit(local, 'local only')
    (local / DRAWS).write_bytes((HEADER + NEW_ROW).encode())

    out = run_helper(local, 'PushHistory')

    assert f'[GIT] [REFUZAT] {DRAWS} - linii sterse sau modificate: 2' in out
    assert git(origin, 'rev-parse', 'main') == git(local, 'rev-parse', 'HEAD')
    assert git(origin, 'show', f'main:{DRAWS}') == (HEADER + ROWS + NEW_ROW).strip()
    assert git(local, 'diff', '--name-only') == DRAWS


@pytest.fixture
def fake_winget(tmp_path):
    """winget simulat: ACTUALIZARI verifica Git la fiecare rulare, iar testul nu
    are voie sa actualizeze Git-ul real al statiei."""
    log = tmp_path / 'winget.log'
    exe = tmp_path / 'winget.cmd'
    exe.write_text(f'@echo %*>>"{log}"\r\n@exit /b 0\r\n', encoding='ascii')
    return exe, log


def _without_git_check(bootstrap):
    """Bootstrap-ul de dinaintea verificarii Git (prima rulare dupa actualizare)."""
    return '\n'.join(
        line for line in bootstrap.split('\n')
        if 'EnsureGit' not in line and 'git-checked' not in line
    )


@pytest.mark.parametrize('launcher', ['START_8000.bat', 'ACTUALIZARI.bat'])
@pytest.mark.parametrize('git_on_path', [True, False])
@pytest.mark.parametrize('old_has_git_check', [True, False])
def test_cmd_self_update_executes_new_launcher_with_spaces(
    repos, launcher, gitless_env, git_on_path, fake_winget, old_has_git_check,
):
    if launcher == 'START_8000.bat' and not old_has_git_check:
        pytest.skip('START_8000 nu verifica Git; varianta ar repeta cazul de mai sus')
    local, seed, _ = repos
    # Keep the real bootstrap, replace application startup with a harmless marker.
    bootstrap = (ROOT / launcher).read_text(encoding='utf-8').split('\n:main\n')[0]
    old_bootstrap = bootstrap if old_has_git_check else _without_git_check(bootstrap)
    (seed / launcher).write_text(
        old_bootstrap + '\n:main\necho OLD> "%PROJECT_DIR%ran.txt"\nexit /b 0\n',
        encoding='utf-8', newline='\r\n',
    )
    commit(seed, 'old launcher')
    git(seed, 'push', 'origin', 'main')
    git(local, 'pull', '--ff-only')
    (seed / launcher).write_text(
        bootstrap + '\n:main\nREM Different byte offsets after update\n'
        'echo NEW> "%PROJECT_DIR%ran.txt"\nexit /b 0\n',
        encoding='utf-8', newline='\r\n',
    )
    commit(seed, 'new launcher')
    git(seed, 'push', 'origin', 'main')
    winget, winget_log = fake_winget
    env = dict(os.environ if git_on_path else gitless_env)
    env['LOTO_WINGET_EXE'] = str(winget)
    p = subprocess.run(
        ['cmd.exe', '/d', '/c', launcher], cwd=local,
        capture_output=True, text=True, errors='replace', timeout=60, env=env,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    assert (local / 'ran.txt').read_text().strip() == 'NEW', p.stdout + p.stderr
    assert git(local, 'rev-parse', 'HEAD') == git(seed, 'rev-parse', 'HEAD')
    # ACTUALIZARI verifica Git o singura data pe rulare: inainte de sync sau, daca
    # versiunea veche nu avea pasul, dupa sync. START_8000 nu il face deloc.
    # Se numara rularile EnsureGit (linia de antet), nu apelurile winget: un Git
    # din PATH instalat altfel (scoop) e pastrat fara winget.
    runs = p.stdout.count('[GIT] Verific Git for Windows')
    assert runs == (1 if launcher == 'ACTUALIZARI.bat' else 0), p.stdout + p.stderr
    if launcher == 'START_8000.bat':
        assert not winget_log.exists()


@pytest.mark.parametrize('launcher', ['START_8000.bat', 'ACTUALIZARI.bat'])
def test_history_push_does_not_depend_on_git_in_path(launcher):
    active_lines = [
        line.strip().lower()
        for line in (ROOT / launcher).read_text(encoding='utf-8').splitlines()
        if not line.lstrip().lower().startswith('rem ')
    ]
    assert not any(line.startswith('where git') for line in active_lines)
    assert 'call :push_istoric' in active_lines


def test_detect_without_git_in_path_reports_version_without_repo_writes(
    repos, gitless_env,
):
    local, _, _ = repos
    config_path = local / '.git' / 'config'
    config_before = config_path.read_bytes()
    head_before = git(local, 'rev-parse', 'HEAD')
    status_before = git(local, 'status', '--porcelain')

    output = run_helper(local, 'Detect', env=gitless_env)

    assert 'git version' in output.lower()
    assert config_path.read_bytes() == config_before
    assert git(local, 'rev-parse', 'HEAD') == head_before
    assert git(local, 'status', '--porcelain') == status_before

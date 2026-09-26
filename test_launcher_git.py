"""Real Windows/Git integration, isolated from the user's repo and network."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parent
HELPER = ROOT / 'scripts' / 'launcher_git.ps1'
POWERSHELL = (Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' /
              'WindowsPowerShell' / 'v1.0' / 'powershell.exe')
pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows launcher integration')


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


@pytest.fixture
def repos(tmp_path):
    origin = tmp_path / 'origin.git'
    git(tmp_path, 'init', '--bare', str(origin))
    seed = tmp_path / 'seed'
    git(tmp_path, 'clone', str(origin), str(seed))
    git(seed, 'checkout', '-b', 'main')
    for key, value in [('user.name', 'Audit test'), ('user.email', 'audit@example.invalid')]:
        git(seed, 'config', key, value)
    (seed / '_ISTORIC').mkdir()
    (seed / '_ISTORIC' / 'draws.csv').write_text('old\n')
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


def run_helper(local, mode='Sync', env=None):
    p = subprocess.run(
        [str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
         str(HELPER), '-Mode', mode, '-ProjectDir', str(local)],
        capture_output=True, text=True, errors='replace', timeout=60, env=env,
    )
    assert p.returncode == 0, p.stdout + p.stderr
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
    (local / '_ISTORIC' / 'draws.csv').write_text('new draw\n')
    run_helper(local, 'PushHistory', env=None if git_on_path else gitless_env)
    assert git(local, 'show', 'HEAD:code.txt') == 'old'
    assert git(local, 'diff', '--cached', '--name-only') == 'code.txt'
    assert git(origin, 'show', 'main:_ISTORIC/draws.csv') == 'new draw'


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
    run_helper(local)
    assert not lock.exists()
    assert git(local, 'rev-parse', 'HEAD') == git(seed, 'rev-parse', 'HEAD')


def test_history_push_replays_draw_commit_onto_newer_main(repos):
    local, seed, origin = repos
    advance(seed)
    (local / '_ISTORIC' / 'draws.csv').write_text('local draw\n', encoding='utf-8')
    out = run_helper(local, 'PushHistory')
    assert 'Repun commit-urile de istoric' in out
    assert git(origin, 'show', 'main:_ISTORIC/draws.csv') == 'local draw'
    assert git(origin, 'show', 'main:code.txt') == 'new'
    assert git(local, 'rev-parse', 'HEAD') == git(origin, 'rev-parse', 'main')
    assert not (local / '.git' / 'rebase-merge').exists()


def test_history_push_leaves_diverged_code_commits_untouched(repos):
    local, seed, origin = repos
    (local / 'code.txt').write_text('personal\n', encoding='utf-8')
    commit(local, 'personal work')
    advance(seed)
    (local / '_ISTORIC' / 'draws.csv').write_text('draw\n', encoding='utf-8')
    head = git(local, 'rev-parse', 'HEAD')
    p = subprocess.run(
        [str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
         str(HELPER), '-Mode', 'PushHistory', '-ProjectDir', str(local)],
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
    (local / '_ISTORIC' / 'draws.csv').write_text('retry draw\n')
    commit(local, 'local only')
    run_helper(local, 'PushHistory')
    assert git(origin, 'rev-parse', 'main') == git(local, 'rev-parse', 'HEAD')


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
    calls = winget_log.read_text().splitlines() if winget_log.exists() else []
    assert len(calls) == (launcher == 'ACTUALIZARI.bat'), p.stdout + p.stderr


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

"""Real Windows/Git integration, isolated from the user's repo and network."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parent
HELPER = ROOT / 'scripts' / 'launcher_git.ps1'
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


def run_helper(local, mode='Sync'):
    p = subprocess.run(
        ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
         str(HELPER), '-Mode', mode, '-ProjectDir', str(local)],
        capture_output=True, text=True, errors='replace', timeout=60,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    return p.stdout


def advance(seed):
    (seed / 'code.txt').write_text('new\n')
    commit(seed, 'remote update')
    git(seed, 'push', 'origin', 'main')


def test_fast_forward_updates_whole_checkout_and_keeps_local_files(repos):
    local, seed, _ = repos
    extra = local / 'personal.bat'
    extra.write_text('do not delete')
    advance(seed)
    run_helper(local)
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


def test_history_auto_commit_does_not_include_staged_code(repos):
    local, _, origin = repos
    (local / 'code.txt').write_text('unfinished code\n')
    git(local, 'add', 'code.txt')
    (local / '_ISTORIC' / 'draws.csv').write_text('new draw\n')
    run_helper(local, 'PushHistory')
    assert git(local, 'show', 'HEAD:code.txt') == 'old'
    assert git(local, 'diff', '--cached', '--name-only') == 'code.txt'
    assert git(origin, 'show', 'main:_ISTORIC/draws.csv') == 'new draw'


def test_history_retries_unpushed_commit_with_no_csv_changes(repos):
    local, _, origin = repos
    (local / '_ISTORIC' / 'draws.csv').write_text('retry draw\n')
    commit(local, 'local only')
    run_helper(local, 'PushHistory')
    assert git(origin, 'rev-parse', 'main') == git(local, 'rev-parse', 'HEAD')


@pytest.mark.parametrize('launcher', ['START_8000.bat', 'ACTUALIZARI.bat'])
def test_cmd_self_update_executes_new_launcher_with_spaces(repos, launcher):
    local, seed, _ = repos
    # Keep the real bootstrap, replace application startup with a harmless marker.
    bootstrap = (ROOT / launcher).read_text(encoding='utf-8').split('\n:main\n')[0]
    (seed / launcher).write_text(
        bootstrap + '\n:main\necho OLD> "%PROJECT_DIR%ran.txt"\nexit /b 0\n',
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
    p = subprocess.run(
        ['cmd.exe', '/d', '/c', launcher], cwd=local,
        capture_output=True, text=True, errors='replace', timeout=60,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    assert (local / 'ran.txt').read_text().strip() == 'NEW', p.stdout + p.stderr
    assert git(local, 'rev-parse', 'HEAD') == git(seed, 'rev-parse', 'HEAD')

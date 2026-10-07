"""Real Windows/Git integration, isolated from the user's repo and network.

On Linux/macOS the helper runs under PowerShell 7 (`pwsh` in PATH or LOTO_PWSH),
with Git given through LOTO_GIT_EXE; the CMD launchers stay Windows-only."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parent
HELPER = ROOT / 'scripts' / 'launcher_git.ps1'
WINDOWS = os.name == 'nt'
POWERSHELL = (
    Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' /
    'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
    if WINDOWS else os.environ.get('LOTO_PWSH') or shutil.which('pwsh')
)
pytestmark = pytest.mark.skipif(
    not WINDOWS and not POWERSHELL, reason='launcher helper needs PowerShell'
)
windows_only = pytest.mark.skipif(not WINDOWS, reason='CMD launcher / Windows PATH')


@pytest.fixture(autouse=True)
def git_for_helper(monkeypatch):
    """Outside Windows the helper finds Git only through LOTO_GIT_EXE."""
    if not WINDOWS:
        monkeypatch.setenv('LOTO_GIT_EXE', shutil.which('git'))

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
    if not WINDOWS:
        pytest.skip('Windows PATH model')
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
    repos, request, git_on_path,
):
    local, seed, _ = repos
    extra = local / 'personal.bat'
    extra.write_text('do not delete')
    advance(seed)
    run_helper(local, env=None if git_on_path else request.getfixturevalue('gitless_env'))
    assert git(local, 'rev-parse', 'HEAD') == git(seed, 'rev-parse', 'HEAD')
    assert (local / 'code.txt').read_text() == 'new\n'
    assert extra.read_text() == 'do not delete'


@pytest.mark.parametrize('state', ['diverged', 'other_branch'])
def test_sync_preserves_user_work(repos, state):
    """Commit-uri locale in conflict cu origin/main sau alta ramura: neatinse."""
    local, seed, _ = repos
    if state == 'other_branch':
        git(local, 'checkout', '-b', 'personal')
    (local / 'code.txt').write_text('personal\n')
    commit(local, 'personal work')
    advance(seed)
    head = git(local, 'rev-parse', 'HEAD')
    out = run_helper(local)
    assert git(local, 'rev-parse', 'HEAD') == head
    assert (local / 'code.txt').read_text() == 'personal\n'
    assert not (local / '.git' / 'rebase-merge').exists()
    if state == 'diverged':
        assert 'nu se pot repune peste origin/main (conflict in code.txt)' in out


@pytest.mark.parametrize('git_on_path', [True, False])
def test_history_auto_commit_does_not_include_staged_code(
    repos, request, git_on_path,
):
    local, _, origin = repos
    (local / 'code.txt').write_text('unfinished code\n')
    git(local, 'add', 'code.txt')
    append(local, NEW_ROW)
    env = None if git_on_path else request.getfixturevalue('gitless_env')
    out = run_helper(local, 'PushHistory', env=env)
    assert git(local, 'show', 'HEAD:code.txt') == 'old'
    assert git(local, 'diff', '--cached', '--name-only') == 'code.txt'
    assert git(origin, 'show', f'main:{DRAWS}') == (HEADER + ROWS + NEW_ROW).strip()
    assert '[REFUZAT]' not in out


def tracked(seed, local, name, text):
    """Fisier urmarit in ambele clone, inainte de scenariu."""
    path = seed / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    commit(seed, f'add {name}')
    git(seed, 'push', 'origin', 'main')
    git(local, 'pull', '-q', '--ff-only')


def remote_change(seed, name, text, message='remote edit'):
    (seed / name).write_text(text, encoding='utf-8')
    commit(seed, message)
    git(seed, 'push', 'origin', 'main')


def backup_root(local):
    return local / '.git' / 'loto-sync-backup'


def local_copies(local):
    """Copiile locale pastrate: {cale: continut}."""
    root = backup_root(local)
    found = {}
    for path in root.glob('*/local/**/*') if root.exists() else []:
        if path.is_file():
            found[path.relative_to(path.parents[len(path.relative_to(root).parts) - 3]).as_posix()] = \
                path.read_text(encoding='utf-8')
    return found


def test_sync_applies_update_and_keeps_edits_in_files_it_does_not_touch(repos):
    local, seed, _ = repos
    tracked(seed, local, 'notes.txt', 'a\n')
    (local / 'notes.txt').write_text('local note\n')
    advance(seed)
    out = run_helper(local)
    assert git(local, 'rev-parse', 'HEAD') == git(seed, 'rev-parse', 'HEAD')
    assert (local / 'code.txt').read_text() == 'new\n'
    assert (local / 'notes.txt').read_text() == 'local note\n'
    assert 'Nu le aplic' not in out
    assert 'Actualizat la origin/main (1 commit-uri noi)' in out
    assert 'celelalte 1 fisiere au ramas neatinse' in out
    assert not backup_root(local).exists()


def test_sync_merges_a_local_edit_into_the_updated_file(repos):
    local, seed, _ = repos
    tracked(seed, local, 'code.txt', 'one\ntwo\nthree\nfour\nfive\n')
    remote_change(seed, 'code.txt', 'ONE\ntwo\nthree\nfour\nfive\n')
    (local / 'code.txt').write_text('one\ntwo\nthree\nfour\nFIVE\n')
    git(local, 'add', 'code.txt')  # o schimbare pusa si in index
    out = run_helper(local)
    assert git(local, 'rev-parse', 'HEAD') == git(seed, 'rev-parse', 'HEAD')
    assert (local / 'code.txt').read_text() == 'ONE\ntwo\nthree\nfour\nFIVE\n'
    assert 'combinate cu actualizarea: code.txt' in out
    assert git(local, 'diff', '--cached', '--name-only') == ''
    assert not backup_root(local).exists()


def test_sync_conflict_takes_the_update_and_keeps_the_local_copy(repos):
    local, seed, _ = repos
    (local / 'code.txt').write_text('personal\n')
    advance(seed)
    out = run_helper(local)
    assert git(local, 'rev-parse', 'HEAD') == git(seed, 'rev-parse', 'HEAD')
    assert (local / 'code.txt').read_text() == 'new\n'
    assert git(local, 'status', '--porcelain', '--untracked-files=no') == ''
    assert 'Conflict in code.txt: am pus versiunea de pe origin/main' in out
    assert local_copies(local) == {'code.txt': 'personal\n'}
    assert not list(backup_root(local).glob('*/PENDING'))
    # Fara markeri de conflict nicaieri in arborele de lucru.
    assert '<<<<<<<' not in (local / 'code.txt').read_text()


def test_sync_keeps_the_local_rebench_results(repos):
    """bench_results nu se combina: doua Re-Bench-uri nu se amesteca."""
    local, seed, _ = repos
    tracked(seed, local, 'bench_results/folds.csv', 'method,rate\nm1,1\nm2,1\nm3,1\n')
    remote_change(seed, 'bench_results/folds.csv', 'method,rate\nm1,2\nm2,1\nm3,1\n')
    (local / 'bench_results' / 'folds.csv').write_text('method,rate\nm1,1\nm2,1\nm3,3\n')
    out = run_helper(local)
    assert git(local, 'rev-parse', 'HEAD') == git(seed, 'rev-parse', 'HEAD')
    assert (local / 'bench_results' / 'folds.csv').read_text() == 'method,rate\nm1,1\nm2,1\nm3,3\n'
    assert 'Rezultatele Re-Bench locale raman: bench_results/folds.csv' in out
    assert git(local, 'diff', '--cached', '--name-only') == ''


def test_sync_replays_local_commits_keeps_edits_and_pushes(repos):
    local, seed, origin = repos
    tracked(seed, local, 'notes.txt', 'a\n')
    tracked(seed, local, 'todo.txt', 'x\n')
    (local / 'notes.txt').write_text('committed locally\n')
    commit(local, 'local work')
    (local / 'todo.txt').write_text('uncommitted\n')
    advance(seed)
    out = run_helper(local)
    assert 'Repun 1 commit-uri locale peste origin/main' in out
    assert git(local, 'rev-parse', 'HEAD~1') == git(seed, 'rev-parse', 'HEAD')
    assert git(origin, 'rev-parse', 'main') == git(local, 'rev-parse', 'HEAD')
    assert (local / 'code.txt').read_text() == 'new\n'
    assert (local / 'notes.txt').read_text() == 'committed locally\n'
    assert (local / 'todo.txt').read_text() == 'uncommitted\n'
    assert not backup_root(local).exists()


def test_replay_keeps_local_additions_and_deletions(repos):
    """La rebase toate fisierele locale trec prin copie: un fisier nou pus in
    index si o stergere locala, neatinse de origin/main, raman cum erau."""
    local, seed, origin = repos
    tracked(seed, local, 'gone.txt', 'to delete\n')
    (local / 'notes.txt').write_text('committed locally\n')
    commit(local, 'local work')
    (local / 'brand_new.txt').write_text('staged new file\n')
    git(local, 'add', 'brand_new.txt')
    git(local, 'rm', '-q', 'gone.txt')
    advance(seed)
    out = run_helper(local)
    assert git(origin, 'rev-parse', 'main') == git(local, 'rev-parse', 'HEAD')
    assert (local / 'brand_new.txt').read_text() == 'staged new file\n'
    assert git(local, 'diff', '--cached', '--name-only') == 'brand_new.txt'
    assert not (local / 'gone.txt').exists()
    assert 'Sterse pe origin/main' not in out and 'nu s-a pastrat' not in out
    assert not backup_root(local).exists()


def test_sync_pushes_commits_that_are_only_local(repos):
    local, _, origin = repos
    (local / 'code.txt').write_text('personal\n')
    commit(local, 'personal work')
    out = run_helper(local)
    assert 'Cod la zi' in out
    assert git(origin, 'rev-parse', 'main') == git(local, 'rev-parse', 'HEAD')


def test_sync_file_deleted_upstream_keeps_the_local_copy(repos):
    local, seed, _ = repos
    tracked(seed, local, 'old.txt', 'a\n')
    git(seed, 'rm', '-q', 'old.txt')
    git(seed, 'commit', '-q', '-m', 'remove old')
    git(seed, 'push', 'origin', 'main')
    (local / 'old.txt').write_text('edited\n')
    out = run_helper(local)
    assert git(local, 'rev-parse', 'HEAD') == git(seed, 'rev-parse', 'HEAD')
    assert not (local / 'old.txt').exists()
    assert 'Sterse pe origin/main: old.txt' in out
    assert local_copies(local) == {'old.txt': 'edited\n'}


def test_sync_staged_deletion_of_an_updated_file(repos):
    local, seed, _ = repos
    tracked(seed, local, 'notes.txt', 'a\n')
    remote_change(seed, 'notes.txt', 'b\n')
    git(local, 'rm', '-q', 'notes.txt')
    out = run_helper(local)
    assert git(local, 'rev-parse', 'HEAD') == git(seed, 'rev-parse', 'HEAD')
    assert (local / 'notes.txt').read_text() == 'b\n'
    assert 'Stergerea locala a fisierelor notes.txt nu s-a pastrat' in out


def test_sync_with_non_ascii_file_names(repos):
    local, seed, _ = repos
    name = 'note\u0219\u0103 extragere.txt'
    tracked(seed, local, name, 'one\ntwo\nthree\nfour\n')
    remote_change(seed, name, 'ONE\ntwo\nthree\nfour\n')
    (local / name).write_text('one\ntwo\nthree\nFOUR\n', encoding='utf-8')
    out = run_helper(local)
    assert git(local, 'rev-parse', 'HEAD') == git(seed, 'rev-parse', 'HEAD')
    assert (local / name).read_text(encoding='utf-8') == 'ONE\ntwo\nthree\nFOUR\n'
    assert 'combinate cu actualizarea' in out


def test_rebase_that_cannot_start_puts_the_local_edits_back(repos):
    """Rebase-ul refuzat de la inceput nu lasa stare de rebase; nu se apeleaza
    --abort, iar modificarile puse deoparte revin."""
    local, seed, origin = repos
    tracked(seed, local, 'notes.txt', 'a\n')
    (local / 'code.txt').write_text('committed locally\n')
    commit(local, 'local work')
    (seed / 'added.txt').write_text('remote\n')
    commit(seed, 'remote adds a file')
    git(seed, 'push', 'origin', 'main')
    (local / 'added.txt').write_text('mine, untracked\n')
    (local / 'notes.txt').write_text('uncommitted\n')
    git(local, 'rm', '-q', DRAWS)
    (local / 'staged.txt').write_text('staged new\n')
    git(local, 'add', 'staged.txt')
    head = git(local, 'rev-parse', 'HEAD')
    out = run_helper(local)
    assert 'Commit-urile locale nu se pot repune peste origin/main' in out
    assert git(local, 'rev-parse', 'HEAD') == head
    assert (local / 'notes.txt').read_text() == 'uncommitted\n'
    assert (local / 'added.txt').read_text() == 'mine, untracked\n'
    assert not (local / DRAWS).exists()
    assert (local / 'staged.txt').read_text() == 'staged new\n'
    assert 'staged.txt' in git(local, 'diff', '--cached', '--name-only').splitlines()
    assert not (local / '.git' / 'rebase-merge').exists()
    assert git(origin, 'rev-parse', 'main') == git(seed, 'rev-parse', 'HEAD')
    assert not backup_root(local).exists()


def hook(local, name, body):
    """Hook in clona locala (helper-ul seteaza core.hooksPath=scripts/git-hooks)."""
    path = local / 'scripts' / 'git-hooks' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\n' + body + '\n', newline='\n')
    path.chmod(0o755)


def test_the_put_aside_copy_is_marked_pending_during_the_update(repos):
    local, seed, _ = repos
    hook(local, 'post-merge', 'ls .git/loto-sync-backup/*/PENDING > pending_seen.txt 2>&1')
    (local / 'code.txt').write_text('personal\n')
    advance(seed)
    run_helper(local)
    assert '/PENDING' in (local / 'pending_seen.txt').read_text()
    assert not list(backup_root(local).glob('*/PENDING'))


def test_a_failed_restore_keeps_the_copy_and_says_so(repos):
    local, seed, _ = repos
    # Copia locala dispare in timpul actualizarii: punerea la loc esueaza.
    hook(local, 'post-merge', 'rm -f .git/loto-sync-backup/*/local/code.txt')
    (local / 'code.txt').write_text('personal\n')
    advance(seed)
    out = run_helper(local)
    assert '[ATENTIE] Nu am putut pune la loc code.txt' in out
    assert list(backup_root(local).glob('*/RESTORE-FAILED'))
    (local / 'scripts' / 'git-hooks' / 'post-merge').unlink()
    again = run_helper(local)
    assert 'unele fisiere nu au putut fi puse la loc' in again


def test_a_file_turned_into_a_folder_is_not_put_aside(repos):
    """Checkout-ul fortat ar sterge continutul neurmarit al folderului."""
    local, seed, _ = repos
    tracked(seed, local, 'notes', 'tracked file\n')
    remote_change(seed, 'notes', 'remote edit\n')
    (local / 'notes').unlink()
    (local / 'notes').mkdir()
    (local / 'notes' / 'chapter1.txt').write_text('untracked work\n')
    head = git(local, 'rev-parse', 'HEAD')
    out = run_helper(local)
    assert 'Nu pot pune deoparte fara pierderi: notes (pe disc e un folder)' in out
    assert git(local, 'rev-parse', 'HEAD') == head
    assert (local / 'notes' / 'chapter1.txt').read_text() == 'untracked work\n'
    assert not backup_root(local).exists()


def test_a_staged_version_that_differs_from_the_disk_is_not_put_aside(repos):
    local, seed, _ = repos
    (local / 'code.txt').write_text('staged\n')
    git(local, 'add', 'code.txt')
    (local / 'code.txt').write_text('edited after staging\n')
    advance(seed)
    out = run_helper(local)
    assert 'code.txt (alta versiune in index decat pe disc)' in out
    assert git(local, 'show', ':code.txt') == 'staged'
    assert (local / 'code.txt').read_text() == 'edited after staging\n'


def test_line_endings_alone_are_not_a_conflict(repos):
    """Checkout CRLF (ca autocrlf pe Windows) si o editare salvata cu LF."""
    local, seed, _ = repos
    (seed / '.gitattributes').write_text('_ISTORIC/*.csv text eol=lf\ncrlf.txt text eol=crlf\n')
    commit(seed, 'crlf attribute')
    git(seed, 'push', 'origin', 'main')
    git(local, 'pull', '-q', '--ff-only')
    tracked(seed, local, 'crlf.txt', 'one\ntwo\nthree\nfour\n')
    assert (local / 'crlf.txt').read_bytes() == b'one\r\ntwo\r\nthree\r\nfour\r\n'
    remote_change(seed, 'crlf.txt', 'ONE\ntwo\nthree\nfour\n')
    (local / 'crlf.txt').write_bytes(b'one\ntwo\nthree\nFOUR\n')
    out = run_helper(local)
    assert 'combinate cu actualizarea: crlf.txt' in out
    assert (local / 'crlf.txt').read_bytes() == b'ONE\r\ntwo\r\nthree\r\nFOUR\r\n'


def test_replay_restores_a_binary_edit_the_update_did_not_touch(repos):
    local, seed, origin = repos
    tracked(seed, local, 'blob.bin', 'plain\n')
    (local / 'notes.txt').write_text('committed locally\n')
    commit(local, 'local work')
    (local / 'blob.bin').write_bytes(b'\x00\x01binary\xff\r\n')
    advance(seed)
    run_helper(local)
    assert git(origin, 'rev-parse', 'main') == git(local, 'rev-parse', 'HEAD')
    assert (local / 'blob.bin').read_bytes() == b'\x00\x01binary\xff\r\n'


def test_an_aborted_rebase_does_not_block_a_later_sync(repos):
    """Rebase-ul anulat lasa varful vechi de pe origin in reflog-ul HEAD; nu e
    un amend local, iar sync-ul urmator repune commit-ul cand se poate."""
    local, seed, origin = repos
    (local / 'code.txt').write_text('personal\n')
    commit(local, 'personal work')
    advance(seed)  # conflict cu commit-ul local
    first = run_helper(local)
    assert 'conflict in code.txt' in first
    remote_change(seed, 'code.txt', 'old\n', 'revert remote edit')
    out = run_helper(local)
    assert 'rescriu un commit' not in out
    assert git(origin, 'rev-parse', 'main') == git(local, 'rev-parse', 'HEAD')
    assert (local / 'code.txt').read_text() == 'personal\n'


def test_an_interrupted_rebase_names_the_copy_and_the_steps(repos):
    local, seed, _ = repos
    (local / 'code.txt').write_text('personal\n')
    commit(local, 'personal work')
    advance(seed)
    git(local, 'fetch', '-q', 'origin')
    subprocess.run(['git', '-c', 'core.hooksPath=NUL', 'rebase', 'origin/main'], cwd=local,
                   capture_output=True, text=True, timeout=30)
    assert (local / '.git' / 'rebase-merge').exists()
    pending = backup_root(local) / '20260101-000000'
    (pending / 'local').mkdir(parents=True)
    (pending / 'local' / 'notes.txt').write_text('uncommitted\n')
    (pending / 'PENDING').write_text('notes.txt\n')
    out = run_helper(local)
    assert 'O sincronizare anterioara s-a intrerupt' in out
    assert 'git rebase --abort' in out
    assert (pending / 'local' / 'notes.txt').read_text() == 'uncommitted\n'


def test_a_rebase_stopped_by_the_time_limit_is_undone_before_restoring(repos):
    """Git oprit la limita de timp in mijlocul rebase-ului: rebase-ul se
    anuleaza inainte ca modificarile puse deoparte sa fie scrise la loc."""
    local, seed, _ = repos
    tracked(seed, local, 'notes.txt', 'a\n')
    (local / 'other.txt').write_text('committed locally\n')
    commit(local, 'local work')
    (local / 'notes.txt').write_text('uncommitted\n')
    advance(seed)
    hook(local, 'post-checkout', 'if [ "$3" = "1" ]; then sleep 30; fi')
    head = git(local, 'rev-parse', 'HEAD')
    out = run_helper(local, env=dict(os.environ, LOTO_GIT_TIMEOUT_SECONDS='4'))
    assert 'Sincronizarea s-a oprit' in out
    assert git(local, 'symbolic-ref', '--short', 'HEAD') == 'main'
    assert git(local, 'rev-parse', 'HEAD') == head
    assert not (local / '.git' / 'rebase-merge').exists()
    assert (local / 'notes.txt').read_text() == 'uncommitted\n'
    assert local_copies(local) == {'notes.txt': 'uncommitted\n'}


def test_a_conflicting_replay_with_local_edits_leaves_everything_as_it_was(repos):
    local, seed, origin = repos
    tracked(seed, local, 'notes.txt', 'a\n')
    (local / 'code.txt').write_text('personal\n')
    commit(local, 'personal work')
    (local / 'notes.txt').write_text('uncommitted\n')
    advance(seed)
    head = git(local, 'rev-parse', 'HEAD')
    out = run_helper(local)
    assert 'conflict in code.txt' in out
    assert git(local, 'symbolic-ref', '--short', 'HEAD') == 'main'
    assert git(local, 'rev-parse', 'HEAD') == head
    assert not (local / '.git' / 'rebase-merge').exists()
    assert (local / 'notes.txt').read_text() == 'uncommitted\n'
    assert (local / 'code.txt').read_text() == 'personal\n'
    assert git(origin, 'rev-parse', 'main') == git(seed, 'rev-parse', 'HEAD')
    assert not backup_root(local).exists()


def test_an_untracked_file_the_update_brings_differently_stops_it(repos):
    """origin/main aduce un fisier care exista local neurmarit, cu alt continut:
    sync-ul se opreste inainte sa atinga ceva si numeste fisierul."""
    local, seed, _ = repos
    (seed / 'code.txt').write_text('new\n')
    (seed / 'added.txt').write_text('remote\n')
    commit(seed, 'remote update')
    git(seed, 'push', 'origin', 'main')
    (local / 'added.txt').write_text('mine, untracked\n')
    (local / 'code.txt').write_text('personal\n')
    head = git(local, 'rev-parse', 'HEAD')
    out = run_helper(local)
    assert 'aduce cu alt continut: added.txt' in out
    assert git(local, 'rev-parse', 'HEAD') == head
    assert (local / 'code.txt').read_text() == 'personal\n'
    assert (local / 'added.txt').read_text() == 'mine, untracked\n'
    assert not backup_root(local).exists()


def test_an_identical_untracked_leftover_does_not_block_the_update(repos):
    """Ramas dintr-o actualizare oprita la jumatate: identic cu origin/main."""
    local, seed, _ = repos
    (seed / 'added.txt').write_text('remote\n')
    commit(seed, 'remote adds a file')
    git(seed, 'push', 'origin', 'main')
    (local / 'added.txt').write_text('remote\n')
    out = run_helper(local)
    assert 'identice cu origin/main): added.txt' in out
    assert git(local, 'rev-parse', 'HEAD') == git(seed, 'rev-parse', 'HEAD')


def test_commits_withdrawn_from_origin_are_dropped_not_pushed_back(repos):
    """origin/main derulat inapoi (force-push fara commit nou): statia nu
    retrimite commit-ul retras, ci trece pe origin/main; vechiul main ramane
    intr-o referinta de rezerva. PushHistory nu-l retrimite nici el."""
    local, seed, origin = repos
    advance(seed)
    git(local, 'pull', '-q', '--ff-only')
    withdrawn_head = git(local, 'rev-parse', 'HEAD')
    git(seed, 'reset', '-q', '--hard', 'HEAD~1')
    git(seed, 'push', '-q', '--force', 'origin', 'main')
    rewound = git(seed, 'rev-parse', 'HEAD')
    (local / 'notes.txt').write_text('untracked note\n')
    out = run_helper(local)
    assert 'au fost retrase de pe origin/main' in out
    assert git(local, 'rev-parse', 'HEAD') == rewound == git(origin, 'rev-parse', 'main')
    assert (local / 'code.txt').read_text() == 'old\n'
    assert (local / 'notes.txt').read_text() == 'untracked note\n'
    kept = git(local, 'for-each-ref', '--format=%(objectname)', 'refs/loto-sync')
    assert kept == withdrawn_head
    out = run_helper(local, 'PushHistory')
    assert git(origin, 'rev-parse', 'main') == rewound


def test_local_commits_on_top_of_withdrawn_ones_stay_for_manual_integration(repos):
    local, seed, origin = repos
    advance(seed)
    git(local, 'pull', '-q', '--ff-only')
    (local / 'mine.txt').write_text('my own work\n')
    commit(local, 'own work')
    git(seed, 'reset', '-q', '--hard', 'HEAD~1')
    (seed / 'other.txt').write_text('replacement\n')
    commit(seed, 'replacement')
    git(seed, 'push', '-q', '--force', 'origin', 'main')
    head = git(local, 'rev-parse', 'HEAD')
    out = run_helper(local)
    assert 'commit-uri retrase de pe origin/main' in out and 'Integrarea ramane manuala' in out
    assert git(local, 'rev-parse', 'HEAD') == head
    assert git(origin, 'rev-parse', 'main') == git(seed, 'rev-parse', 'HEAD')
    p = subprocess.run(
        [str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
         str(HELPER), '-Mode', 'PushHistory', '-ProjectDir', str(local),
         '-PythonExe', sys.executable],
        capture_output=True, text=True, errors='replace', timeout=60,
    )
    assert 'nu le trimit din nou' in p.stdout
    assert git(origin, 'rev-parse', 'main') == git(seed, 'rev-parse', 'HEAD')


def test_the_auto_push_hook_does_not_push_withdrawn_commits(repos):
    local, seed, origin = repos
    advance(seed)
    git(local, 'pull', '-q', '--ff-only')
    git(seed, 'reset', '-q', '--hard', 'HEAD~1')
    git(seed, 'push', '-q', '--force', 'origin', 'main')
    git(local, 'fetch', '-q', 'origin')
    hooks = local / 'scripts' / 'git-hooks'
    hooks.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / 'scripts' / 'git-hooks' / 'post-commit', hooks / 'post-commit')
    (hooks / 'post-commit').chmod(0o755)
    (local / 'mine.txt').write_text('new work\n')
    git(local, 'add', 'mine.txt')
    p = subprocess.run(['git', '-c', 'core.hooksPath=scripts/git-hooks', 'commit', '-q', '-m', 'new'],
                       cwd=local, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    assert 'auto-push skipped' in p.stderr
    assert git(origin, 'rev-parse', 'main') == git(seed, 'rev-parse', 'HEAD')


def test_history_commits_already_upstream_do_not_block_the_update(repos):
    """Doua statii adauga aceleasi extrageri; commit-ul netrimis al uneia e
    redundant: main trece pe origin/main in loc sa se opreasca la conflict."""
    local, seed, origin = repos
    append(local, NEW_ROW)
    commit(local, 'auto: update istoric extrageri')
    append(seed, NEW_ROW + '07-10-2026,2,3,4,5,6,7\n')
    (seed / 'code.txt').write_text('new\n')
    commit(seed, 'other station: rows and code')
    git(seed, 'push', 'origin', 'main')
    out = run_helper(local)
    assert 'randuri de istoric care sunt deja pe origin/main' in out
    assert git(local, 'rev-parse', 'HEAD') == git(seed, 'rev-parse', 'HEAD')
    assert (local / 'code.txt').read_text() == 'new\n'
    assert (local / DRAWS).read_text().endswith('07-10-2026,2,3,4,5,6,7\n')


def test_a_rebase_that_cannot_be_undone_is_reported_not_hidden(repos):
    """Index blocat de alt proces git: rebase-ul si anularea lui esueaza. Sync
    nu spune ca nimic nu s-a schimbat, iar pornirile urmatoare numesc rebase-ul."""
    local, seed, _ = repos
    (local / 'other.txt').write_text('committed locally\n')
    commit(local, 'local work')
    advance(seed)
    hook(local, 'post-checkout', 'if [ "$3" = "1" ]; then : > "$(git rev-parse --git-dir)/index.lock"; fi')
    other_git = subprocess.Popen(['git', 'cat-file', '--batch'], cwd=local,
                                 stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    try:
        out = run_helper(local)
        again = run_helper(local)
    finally:
        other_git.kill()
        other_git.wait()
    assert 'Repository-ul a ramas la jumatatea operatiei' in out
    assert 'Codul local ramane neschimbat' not in out
    assert 'Un rebase a ramas neterminat' in again


def test_a_second_launcher_waits_for_the_first(repos):
    local, seed, _ = repos
    advance(seed)
    lock = local / '.git' / 'loto-sync.lock'
    holder = subprocess.Popen(
        [str(POWERSHELL), '-NoProfile', '-Command',
         f"$f = [IO.File]::Open('{lock}', 'OpenOrCreate', 'ReadWrite', 'None'); "
         "Write-Host held; Start-Sleep -Seconds 20; $f.Close()"],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout.readline().strip() == 'held'
        head = git(local, 'rev-parse', 'HEAD')
        out = run_helper(local)
        assert 'Alt lansator sincronizeaza acum' in out
        assert git(local, 'rev-parse', 'HEAD') == head
    finally:
        holder.kill()
        holder.wait()
    run_helper(local)
    assert git(local, 'rev-parse', 'HEAD') == git(seed, 'rev-parse', 'HEAD')


def interrupted(local, path, local_text, base_text):
    """Starea lasata de o fereastra inchisa dupa Set-Aside: PENDING, copia locala,
    versiunea din HEAD, iar fisierul de pe disc readus la HEAD."""
    folder = backup_root(local) / '20260101-000000-1'
    (folder / 'local').mkdir(parents=True)
    (folder / 'base').mkdir(parents=True)
    (folder / 'local' / path).write_text(local_text)
    (folder / 'base' / path).write_text(base_text)
    (folder / 'PENDING').write_text(path + '\n')
    return folder


def test_an_interrupted_sync_is_finished_at_the_next_start(repos):
    local, _, _ = repos
    folder = interrupted(local, 'code.txt', 'personal\n', 'old\n')
    out = run_helper(local)
    assert 'O sincronizare anterioara s-a intrerupt; pun la loc' in out
    assert (local / 'code.txt').read_text() == 'personal\n'
    assert not folder.exists()


def test_an_interrupted_sync_after_the_update_merges_the_copy(repos):
    local, seed, _ = repos
    tracked(seed, local, 'code.txt', 'one\ntwo\nthree\nfour\n')
    folder = interrupted(local, 'code.txt', 'one\ntwo\nthree\nFOUR\n', 'one\ntwo\nthree\nfour\n')
    (local / 'code.txt').write_text('ONE\ntwo\nthree\nfour\n')  # actualizarea ajunsese pe disc
    run_helper(local)
    assert (local / 'code.txt').read_text() == 'ONE\ntwo\nthree\nFOUR\n'
    assert not folder.exists()


def test_a_conflict_copy_is_recalled_at_every_start(repos):
    local, seed, _ = repos
    (local / 'code.txt').write_text('personal\n')
    advance(seed)
    run_helper(local)
    again = run_helper(local)
    assert 'Versiuni locale pastrate dupa o sincronizare (conflict: code.txt)' in again
    shutil.rmtree(backup_root(local))
    assert 'Versiuni locale pastrate' not in run_helper(local)


def test_a_case_only_rename_is_not_put_aside(repos):
    local, seed, _ = repos
    tracked(seed, local, 'Foo.txt', 'a\n')
    remote_change(seed, 'Foo.txt', 'b\n')
    git(local, 'mv', 'Foo.txt', 'foo.txt')
    head = git(local, 'rev-parse', 'HEAD')
    out = run_helper(local)
    assert '(redenumire doar de majuscule)' in out
    assert git(local, 'rev-parse', 'HEAD') == head
    assert git(local, 'diff', '--cached', '--name-only', '--no-renames').splitlines() == ['Foo.txt', 'foo.txt']


def test_a_force_added_ignored_file_stays_staged_after_a_replay(repos):
    local, seed, origin = repos
    tracked(seed, local, '.gitignore', '*.secret\n')
    (local / 'notes.txt').write_text('committed locally\n')
    commit(local, 'local work')
    (local / 'keys.secret').write_text('staged on purpose\n')
    git(local, 'add', '-f', 'keys.secret')
    advance(seed)
    run_helper(local)
    assert git(origin, 'rev-parse', 'main') == git(local, 'rev-parse', 'HEAD')
    assert 'keys.secret' in git(local, 'diff', '--cached', '--name-only').splitlines()


def test_local_merge_commits_are_not_replayed(repos):
    local, seed, origin = repos
    git(local, 'checkout', '-q', '-b', 'side')
    (local / 'side.txt').write_text('side\n')
    commit(local, 'side work')
    git(local, 'checkout', '-q', 'main')
    (local / 'main.txt').write_text('main\n')
    commit(local, 'main work')
    git(local, 'merge', '-q', '--no-ff', '-m', 'local merge', 'side')
    advance(seed)
    head = git(local, 'rev-parse', 'HEAD')
    out = run_helper(local)
    assert 'contin un commit de merge' in out
    assert git(local, 'rev-parse', 'HEAD') == head
    assert git(origin, 'rev-parse', 'main') == git(seed, 'rev-parse', 'HEAD')


def test_local_amend_of_a_pushed_commit_is_not_replayed(repos):
    local, seed, origin = repos
    (local / 'code.txt').write_text('first\n')
    commit(local, 'pushed')
    git(local, 'push', '-q', 'origin', 'main')
    git(local, 'commit', '-q', '--amend', '-m', 'pushed, reworded')
    head = git(local, 'rev-parse', 'HEAD')
    out = run_helper(local)
    assert 'rescriu un commit deja trimis' in out
    assert git(local, 'rev-parse', 'HEAD') == head
    assert git(origin, 'log', '-1', '--format=%s', 'main') == 'pushed'


def test_an_interrupted_sync_is_reported(repos):
    local, _, _ = repos
    pending = backup_root(local) / '20260101-000000'
    (pending / 'local').mkdir(parents=True)
    (pending / 'local' / 'code.txt').write_text('lost?\n')
    (pending / 'PENDING').write_text('code.txt\n')
    out = run_helper(local)
    assert 'O sincronizare anterioara s-a intrerupt' in out
    assert (pending / 'local' / 'code.txt').read_text() == 'lost?\n'


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
    # Copia randului anterior (AGENTS.md §4.1), ca 24-10-2024 la 5/40.
    ('05-10-2026,12,11,10,9,8,7\n', 'randul 4 repeta numerele randului 3'),
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


@windows_only
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

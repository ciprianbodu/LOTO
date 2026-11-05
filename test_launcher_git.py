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


@pytest.fixture(autouse=True)
def explorer_lookup(monkeypatch):
    """Lansatorul pornit din Explorer gaseste .bat-ul in folderul curent.

    Unele terminale de agent (Claude Code) seteaza
    NoDefaultCurrentDirectoryInExePath=1; atunci `cmd /c START_8000.bat` nu mai
    cauta in folderul curent si testul pica fara nicio legatura cu lansatorul.
    Explorer nu seteaza variabila, iar lansatoarele folosesc in interior numai
    cai complete."""
    monkeypatch.delenv('NoDefaultCurrentDirectoryInExePath', raising=False)


def installed_git():
    """Git-ul instalat, cum il vede Explorer: wrapper-ul `Git/cmd/git.exe`.

    Din Git Bash, PATH incepe cu `Git/ucrt64/bin` (sau `mingw64/bin`), iar
    `shutil.which` intoarce binarul MSYS. Acela are nevoie de PATH-ul shell-ului
    pentru `sh` si DLL-uri; cu PATH-ul minim al Explorer-ului vechi nu poate
    face fetch dintr-un remote local. Wrapper-ul isi pune singur PATH-ul."""
    found = shutil.which('git')
    if not found or not WINDOWS:
        return found
    path = Path(found)
    if path.parent.name.lower() == 'bin' and path.parent.parent.name.lower() in {
        'mingw64', 'ucrt64', 'clang64', 'clangarm64', 'mingw32', 'usr',
    }:
        wrapper = path.parents[2] / 'cmd' / 'git.exe'
        if wrapper.is_file():
            return str(wrapper)
    return found

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
    git_exe = installed_git()
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
        assert 'integrarea ramane manuala: git rebase origin/main' in out


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


def test_sync_reports_the_station_rebench_apart_from_local_edits(repos):
    """Re-Bench-ul statiei nu apare drept modificare locala necomisa, nici la
    numaratoarea de la final; o modificare de cod apare ca inainte."""
    local, seed, _ = repos
    tracked(seed, local, 'bench_results/folds.csv', 'method,rate\nm1,1\n')
    tracked(seed, local, 'notes.txt', 'a\n')
    (local / 'bench_results' / 'folds.csv').write_text('method,rate\nm1,2\n')
    out = run_helper(local)
    assert '[GIT] Cod la zi.' in out
    assert '[GIT] Re-Bench-ul statiei ramane local: bench_results/folds.csv.' in out
    assert 'Modificari locale necomise' not in out

    (local / 'notes.txt').write_text('local note\n')
    advance(seed)
    out = run_helper(local)
    assert git(local, 'rev-parse', 'HEAD') == git(seed, 'rev-parse', 'HEAD')
    assert '[GIT] Modificari locale necomise (1): notes.txt.' in out
    assert '[GIT] Re-Bench-ul statiei ramane local: bench_results/folds.csv.' in out
    assert 'celelalte 1 fisiere au ramas neatinse' in out
    assert (local / 'bench_results' / 'folds.csv').read_text() == 'method,rate\nm1,2\n'
    assert (local / 'notes.txt').read_text() == 'local note\n'


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


def test_a_replay_blocked_by_an_untracked_file_changes_nothing(repos):
    """origin/main aduce un fisier care exista local neurmarit: sync-ul se
    opreste inainte de rebase, fara sa atinga commit-uri, index sau disc."""
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
    assert 'aduce cu alt continut: added.txt' in out
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
    # PushHistory nu comite extrageri noi peste commit-ul retras.
    append(local, NEW_ROW)
    out = run_helper(local, 'PushHistory')
    assert 'nu comit extragerile noi peste ele' in out
    # Sync nu trece pe origin/main in acest caz: mesajul nu promite asta.
    assert 'Sync trece intai' not in out
    assert 'integrarea ramane manuala' in out
    assert git(local, 'rev-parse', 'HEAD') == head
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


def test_a_history_correction_is_replayed_not_dropped(repos):
    """Un rand sters de mana (corectura voita) nu e „deja pe origin/main”:
    commit-ul se repune peste extragerea noua a celeilalte statii."""
    local, seed, origin = repos
    first, last = ROWS.splitlines()
    (local / DRAWS).write_bytes((HEADER + last + '\n').encode())
    commit(local, 'remove a wrong row')
    append(seed, NEW_ROW)
    commit(seed, 'auto: update istoric extrageri')
    git(seed, 'push', 'origin', 'main')
    out = run_helper(local)
    assert 'deja pe origin/main' not in out
    assert git(origin, 'rev-parse', 'main') == git(local, 'rev-parse', 'HEAD')
    assert git(origin, 'show', f'main:{DRAWS}') == (HEADER + last + '\n' + NEW_ROW).strip()


def test_an_ignored_file_a_replay_would_overwrite_stops_it(repos):
    """Rebase-ul scrie peste fisierele ignorate pe care origin/main incepe sa le
    urmareasca: sync-ul se opreste inainte si il numeste."""
    local, seed, origin = repos
    tracked(seed, local, '.gitignore', '*.local\n')
    (local / 'station.local').write_text('this station only\n')
    (local / 'notes.txt').write_text('committed locally\n')
    commit(local, 'local work')
    (seed / 'station.local').write_text('from origin\n')
    git(seed, 'add', '-f', 'station.local')
    git(seed, 'commit', '-q', '-m', 'track station.local')
    git(seed, 'push', 'origin', 'main')
    head = git(local, 'rev-parse', 'HEAD')
    out = run_helper(local)
    assert 'aduce cu alt continut: station.local' in out
    assert git(local, 'rev-parse', 'HEAD') == head
    assert (local / 'station.local').read_text() == 'this station only\n'


def test_a_staged_untrack_is_not_put_aside(repos):
    local, seed, _ = repos
    git(local, 'rm', '-q', '--cached', 'code.txt')
    advance(seed)
    head = git(local, 'rev-parse', 'HEAD')
    out = run_helper(local)
    assert 'code.txt (scos din index cu git rm --cached)' in out
    assert git(local, 'rev-parse', 'HEAD') == head
    assert git(local, 'ls-files', 'code.txt') == ''
    assert (local / 'code.txt').read_text() == 'old\n'


def test_an_interruption_before_the_base_copy_still_restores_the_edit(repos):
    local, _, _ = repos
    folder = backup_root(local) / '20260101-000000-2'
    (folder / 'local').mkdir(parents=True)
    (folder / 'local' / 'code.txt').write_text('personal\n')
    (folder / 'HEAD').write_text(git(local, 'rev-parse', 'HEAD') + '\n')
    (folder / 'PENDING').write_text('code.txt\n')
    out = run_helper(local)
    assert 'Conflict' not in out
    assert (local / 'code.txt').read_text() == 'personal\n'
    assert not folder.exists()


def test_rows_already_upstream_are_dropped_after_an_upstream_correction(repos):
    """origin/main a corectat un rand vechi si a primit aceeasi extragere noua:
    commit-ul local care doar o adauga e redundant, nu un conflict permanent."""
    local, seed, _ = repos
    append(local, NEW_ROW)
    commit(local, 'auto: update istoric extrageri')
    first, last = ROWS.splitlines()
    corrected = '01-10-2026,1,2,3,4,5,7'
    (seed / DRAWS).write_bytes((HEADER + corrected + '\n' + last + '\n' + NEW_ROW
                                + '07-10-2026,2,3,4,5,6,7\n').encode())
    commit(seed, 'correct a row, add draws')
    git(seed, 'push', 'origin', 'main')
    out = run_helper(local)
    assert 'adauga numai randuri de istoric care sunt deja pe origin/main' in out
    assert git(local, 'rev-parse', 'HEAD') == git(seed, 'rev-parse', 'HEAD')
    assert corrected in (local / DRAWS).read_text()


def test_a_local_merge_with_nothing_new_upstream_is_pushed(repos):
    local, _, origin = repos
    git(local, 'checkout', '-q', '-b', 'side')
    (local / 'side.txt').write_text('side\n')
    commit(local, 'side work')
    git(local, 'checkout', '-q', 'main')
    (local / 'main.txt').write_text('main\n')
    commit(local, 'main work')
    git(local, 'merge', '-q', '--no-ff', '-m', 'local merge', 'side')
    out = run_helper(local)
    assert 'Integrarea ramane manuala' not in out
    assert git(origin, 'rev-parse', 'main') == git(local, 'rev-parse', 'HEAD')


def test_an_interrupted_sync_on_another_branch_names_the_branch_not_a_rebase(repos):
    local, _, _ = repos
    folder = interrupted(local, 'code.txt', 'personal\n', 'old\n')
    git(local, 'checkout', '-q', '-b', 'trying-a-pr')
    out = run_helper(local)
    assert 'git switch main' in out
    assert 'rebase --abort' not in out
    assert (folder / 'local' / 'code.txt').read_text() == 'personal\n'


def once_on_ref(local, ref, state, action):
    """reference-transaction: o singura data, la starea data a referintei."""
    hook(local, 'reference-transaction',
         f'[ "$1" = "{state}" ] || exit 0\n'
         f'grep -q " {ref}$" || exit 0\n'
         '[ -e "$(git rev-parse --git-dir)/hook-done" ] && exit 0\n'
         ': > "$(git rev-parse --git-dir)/hook-done"\n'
         + action)


def test_a_fast_forward_stopped_halfway_is_undone(repos):
    """Git scrie arborele, apoi nu muta main (aici: tranzactia refuzata; pe
    statie, un fisier blocat): fisierele scrise revin, cele aduse dispar."""
    local, seed, _ = repos
    (seed / 'added.txt').write_text('remote\n')
    advance(seed)
    once_on_ref(local, 'refs/heads/main', 'prepared', 'exit 1')
    head = git(local, 'rev-parse', 'HEAD')
    out = run_helper(local)
    assert 'Git se oprise la jumatate' in out
    assert git(local, 'rev-parse', 'HEAD') == head
    assert (local / 'code.txt').read_text() == 'old\n'
    assert not (local / 'added.txt').exists()
    assert git(local, 'status', '--porcelain', '--untracked-files=no') == ''


def test_a_file_saved_during_the_sync_is_not_reverted(repos):
    """Alt program (Re-Bench, editor) scrie un fisier urmarit dupa fetch: ff-ul
    esueaza, iar salvarea ramane; nu e o scriere a lui git."""
    local, seed, _ = repos
    advance(seed)
    once_on_ref(local, 'refs/remotes/origin/main', 'committed',
                'printf "saved during sync\\n" >> code.txt')
    out = run_helper(local)
    assert 'Git se oprise la jumatate' not in out
    assert (local / 'code.txt').read_text() == 'old\nsaved during sync\n'


def test_a_file_untracked_by_a_local_commit_stops_the_replay(repos):
    local, seed, _ = repos
    tracked(seed, local, 'settings.ini', 'station\n')
    git(local, 'rm', '-q', '--cached', 'settings.ini')
    git(local, 'commit', '-q', '-m', 'stop tracking settings.ini')
    (seed / 'other.txt').write_text('other station\n')
    commit(seed, 'unrelated')
    git(seed, 'push', 'origin', 'main')
    head = git(local, 'rev-parse', 'HEAD')
    out = run_helper(local)
    assert 'scoase din urmarire de commit-urile locale si pastrate pe disc: settings.ini' in out
    assert git(local, 'rev-parse', 'HEAD') == head
    assert (local / 'settings.ini').read_text() == 'station\n'


def test_an_identical_leftover_comes_back_when_the_replay_fails(repos):
    local, seed, _ = repos
    (local / 'code.txt').write_text('personal\n')
    commit(local, 'personal work')
    (seed / 'tool.py').write_text('print(1)\n')
    advance(seed)
    (local / 'tool.py').write_text('print(1)\n')
    out = run_helper(local)
    assert 'nu se pot repune peste origin/main' in out
    assert (local / 'tool.py').read_text() == 'print(1)\n'
    assert git(local, 'status', '--porcelain', 'tool.py') == '?? tool.py'


def test_an_interruption_during_the_revert_keeps_the_files_not_yet_reverted(repos):
    local, seed, _ = repos
    tracked(seed, local, 'notes.txt', 'notes\n')
    folder = backup_root(local) / '20260101-000000-3'
    (folder / 'local').mkdir(parents=True)
    (folder / 'local' / 'code.txt').write_text('personal code\n')
    (folder / 'local' / 'notes.txt').write_text('personal notes\n')
    (folder / 'HEAD').write_text(git(local, 'rev-parse', 'HEAD') + '\n')
    (folder / 'PENDING').write_text('code.txt\nnotes.txt\n')
    (local / 'notes.txt').write_text('personal notes\n')  # inca nereadus la HEAD
    out = run_helper(local)
    assert 'Conflict' not in out
    assert (local / 'code.txt').read_text() == 'personal code\n'
    assert (local / 'notes.txt').read_text() == 'personal notes\n'
    assert not folder.exists()


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


# --- Audit global 2026-10-22: lansatoarele (C11-C15, plus R1/R2) -------------


def run_helper_rc(local, mode='Sync', env=None, project_dir=None):
    """Ca run_helper, dar intoarce si codul de iesire (refuzurile ies cu 1)."""
    args = [str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass',
            '-File', str(HELPER), '-Mode', mode, '-ProjectDir', str(project_dir or local)]
    if mode == 'PushHistory':
        args += ['-PythonExe', sys.executable]
    p = subprocess.run(
        args, capture_output=True, text=True, errors='replace', timeout=60, env=env,
    )
    return p.returncode, p.stdout


SLOW_MODULES = 10


def slow_modules(seed, local,
                 smudge=r'c=$(cat); case "$c" in v2*) sleep 1;; esac; printf "%s\n" "$c"',
                 required=False):
    """Module v1 pe ambele clone. In clona statiei, git scrie lent fiecare
    modul v2 (ca Drive, unde git citeste si scrie lent fiecare fisier).
    smudge: alt filtru de scriere; required: un filtru esuat opreste git."""
    for i in range(SLOW_MODULES):
        (seed / f'mod{i:02}.py').write_text('v1\n')
    commit(seed, 'modules')
    git(seed, 'push', 'origin', 'main')
    git(local, 'pull', '-q', '--ff-only')
    (local / '.git' / 'info').mkdir(exist_ok=True)
    (local / '.git' / 'info' / 'attributes').write_text('*.py filter=slow\n')
    git(local, 'config', 'filter.slow.smudge', smudge)
    if required:
        git(local, 'config', 'filter.slow.clean', 'cat')
        git(local, 'config', 'filter.slow.required', 'true')


@pytest.mark.parametrize('plan', ['ff', 'drop'])
def test_an_update_stopped_by_the_time_limit_is_undone(repos, plan):
    """C11: git oprit la limita de timp in mijlocul ff/drop scrisese o parte din
    arbore (si stersese un modul inainte sa-l rescrie): totul revine la HEAD,
    fisierul adus dispare, lock-urile lui nu raman, iar mesajul o spune."""
    local, seed, _ = repos
    slow_modules(seed, local)
    if plan == 'drop':
        # Commit-ul local a fost retras de pe origin/main (force-push).
        advance(seed)
        git(local, 'pull', '-q', '--ff-only')
        git(seed, 'reset', '-q', '--hard', 'HEAD~1')
    for i in range(SLOW_MODULES):
        (seed / f'mod{i:02}.py').write_text('v2\n')
    (seed / 'added.txt').write_text('remote\n')
    commit(seed, 'v2')
    git(seed, 'push', '-q', '--force', 'origin', 'main')
    head = git(local, 'rev-parse', 'HEAD')
    code_before = (local / 'code.txt').read_text()

    out = run_helper(local, env=dict(os.environ, LOTO_GIT_TIMEOUT_SECONDS='4'))

    assert 'Sincronizarea s-a oprit: Git a depasit 4 s' in out
    if plan == 'drop':
        assert 'au fost retrase de pe origin/main' in out
    assert git(local, 'rev-parse', 'HEAD') == head
    modules = [(local / f'mod{i:02}.py').read_text() for i in range(SLOW_MODULES)]
    assert modules == ['v1\n'] * SLOW_MODULES
    assert (local / 'code.txt').read_text() == code_before
    assert not (local / 'added.txt').exists()
    for lock in ('index.lock', 'HEAD.lock', 'ORIG_HEAD.lock', 'refs/heads/main.lock'):
        assert not (local / '.git' / lock).exists(), lock
    assert 'Git se oprise la jumatate; am readus la versiunea de dinainte' in out
    assert git(local, 'status', '--porcelain', '--untracked-files=no') == ''
    # Pornirea urmatoare, fara limita mica, aduce actualizarea intreaga.
    run_helper(local)
    assert git(local, 'rev-parse', 'HEAD') == git(seed, 'rev-parse', 'HEAD')
    assert (local / 'mod00.py').read_text() == 'v2\n'


def push_v2_modules(seed, local):
    """origin/main aduce modulele v2 si un fisier nou; intoarce HEAD-ul statiei."""
    for i in range(SLOW_MODULES):
        (seed / f'mod{i:02}.py').write_text('v2\n')
    (seed / 'added.txt').write_text('remote\n')
    commit(seed, 'v2')
    git(seed, 'push', '-q', 'origin', 'main')
    return git(local, 'rev-parse', 'HEAD')


def test_an_undo_as_slow_as_the_update_has_its_own_time_limit(repos):
    """R5 (C11): pe Drive versiunea veche se scrie la fel de lent ca cea noua
    (aici chiar mai lent), deci anularea ar depasi limita care a oprit
    actualizarea. Are limita lunga, ca anularea unui rebase: fisierele revin,
    lock-urile nu raman, iar mesajele opririi nu se pierd."""
    local, seed, _ = repos
    slow_modules(seed, local,
                 smudge=r'c=$(cat); case "$c" in v1*) sleep 2;; *) sleep 1;; esac; printf "%s\n" "$c"')
    head = push_v2_modules(seed, local)

    code, out = run_helper_rc(local, env=dict(os.environ, LOTO_GIT_TIMEOUT_SECONDS='4'))

    assert code == 0, out
    assert 'Sincronizarea s-a oprit: Git a depasit 4 s la "git merge --ff-only origin/main"' in out
    assert 'Git se oprise la jumatate; am readus la versiunea de dinainte' in out
    assert '[ATENTIE]' not in out
    assert git(local, 'rev-parse', 'HEAD') == head
    modules = [(local / f'mod{i:02}.py').read_text() for i in range(SLOW_MODULES)]
    assert modules == ['v1\n'] * SLOW_MODULES
    assert not (local / 'added.txt').exists()
    for lock in ('index.lock', 'HEAD.lock', 'ORIG_HEAD.lock', 'refs/heads/main.lock'):
        assert not (local / '.git' / lock).exists(), lock
    assert git(local, 'status', '--porcelain', '--untracked-files=no') == ''


@pytest.mark.parametrize('cause', ['stopped', 'refused'])
def test_an_undo_that_fails_is_reported_not_claimed(repos, cause):
    """R5 (C11): ff-ul oprit la limita de timp sau refuzat la jumatate, iar
    anularea esueaza si ea (aici filtrul care scrie versiunea veche). Nu se
    spune ca fisierele au revenit: [ATENTIE] spune ce a ramas si ce e de facut."""
    local, seed, _ = repos
    fail_old = 'c=$(cat); case "$c" in v1*) exit 1;; esac; '
    env = None
    if cause == 'stopped':
        slow_modules(seed, local, smudge=fail_old + r'sleep 1; printf "%s\n" "$c"', required=True)
        env = dict(os.environ, LOTO_GIT_TIMEOUT_SECONDS='4')
    else:
        slow_modules(seed, local, smudge=fail_old + r'printf "%s\n" "$c"', required=True)
        once_on_ref(local, 'refs/heads/main', 'prepared', 'exit 1')
    head = push_v2_modules(seed, local)

    code, out = run_helper_rc(local, env=env)

    assert code == 0, out
    if cause == 'stopped':
        assert 'Sincronizarea s-a oprit: Git a depasit 4 s' in out
    else:
        assert '[GIT] Actualizarea nu a reusit.' in out
    assert 'am readus la versiunea de dinainte' not in out
    assert 'Codul local ramane neschimbat' not in out
    assert ('[GIT] [ATENTIE] Git se oprise la jumatate, iar readucerea la versiunea '
            'de dinainte nu s-a terminat (') in out
    assert 'smudge filter slow failed' in out
    assert 'mod00.py' in out
    assert 'Verificati cu git status, apoi reporniti' in out
    assert git(local, 'rev-parse', 'HEAD') == head
    if cause == 'stopped':
        # Cauza inlaturata: pornirea urmatoare reia actualizarea, cum spune mesajul.
        (local / '.git' / 'info' / 'attributes').unlink()
        run_helper(local)
        assert git(local, 'rev-parse', 'HEAD') == git(seed, 'rev-parse', 'HEAD')
        modules = [(local / f'mod{i:02}.py').read_text() for i in range(SLOW_MODULES)]
        assert modules == ['v2\n'] * SLOW_MODULES


def machine_marker():
    """Marcajul fixarii offline al acestui calculator ([Environment]::MachineName)."""
    import socket
    name = os.environ.get('COMPUTERNAME') if WINDOWS else None
    return 'loto-offline-pin-' + (name or socket.gethostname().split('.')[0])


@pytest.fixture
def fake_attrib(tmp_path):
    """attrib simulat ca pe Google Drive (G:): cod 0, fara mesaj, nimic fixat."""
    if WINDOWS:
        exe = tmp_path / 'attrib.cmd'
        exe.write_text('@exit /b 0\r\n', encoding='ascii')
    else:
        exe = tmp_path / 'attrib'
        exe.write_text('#!/bin/sh\nexit 0\n', encoding='ascii')
        exe.chmod(0o755)
    return exe


@pytest.mark.parametrize('other_station_marker', [False, True])
def test_an_offline_pin_that_drive_ignores_gives_the_manual_step(
    repos, fake_attrib, other_station_marker,
):
    """C12: attrib +P iese cu 0 pe G:, dar bitul PINNED nu ramane. Nu se spune
    ca Drive descarca fisierele, ci pasul manual; marcajul celeilalte statii
    (in .git-ul sincronizat) nu opreste cererea pe acest calculator."""
    local, _, _ = repos
    if other_station_marker:
        (local / '.git' / 'loto-offline-pin').write_text('2026-09-26T19:19:29\n')
    env = dict(os.environ, LOTO_ATTRIB_EXE=str(fake_attrib))

    out = run_helper(local, env=env)

    assert 'cer fixarea fisierelor pe disc' in out
    assert 'Google Drive nu a acceptat fixarea automata' in out
    assert 'Drive descarca fisierele in fundal' not in out
    markers = [p.name.lower() for p in (local / '.git').glob('loto-offline-pin-*')]
    assert markers == [machine_marker().lower()]
    # O data pe calculator: attrib fara efect nu se reia la fiecare pornire.
    assert 'cer fixarea' not in run_helper(local, env=env)


@windows_only
def test_a_real_offline_pin_is_requested_once_per_computer(repos):
    """C12: pe NTFS attrib +P ramane pe fisiere, desi scrie „Not resetting hidden
    file” pentru .git (ascuns de Git for Windows): fixarea e raportata reusita,
    o data, iar marcajul este al calculatorului, nu cel comun din .git."""
    local, _, _ = repos
    out = run_helper(local)
    assert 'Fixare offline ceruta. Drive descarca fisierele in fundal.' in out
    assert (local / '.git' / machine_marker()).is_file()
    assert not (local / '.git' / 'loto-offline-pin').exists()
    assert 'cer fixarea' not in run_helper(local)


def test_history_waits_for_sync_when_every_local_commit_was_withdrawn(repos):
    """C13, cazul neschimbat: toate commit-urile locale retrase -> Sync le lasa
    deoparte (drop), iar PushHistory spune ca Sync trece intai pe origin/main."""
    local, seed, _ = repos
    advance(seed)
    git(local, 'pull', '-q', '--ff-only')
    git(seed, 'reset', '-q', '--hard', 'HEAD~1')
    git(seed, 'push', '-q', '--force', 'origin', 'main')
    append(local, NEW_ROW)
    out = run_helper(local, 'PushHistory')
    assert ('[GIT] main local contine commit-uri retrase de pe origin/main; nu comit '
            'extragerile noi peste ele. Sync trece intai pe origin/main.') in out


def test_history_with_a_withdrawn_merge_commit_names_the_merge(repos):
    """R6 (C13): toate commit-urile locale sunt retrase, dar unul e de merge (un
    PR adus, apoi origin/main readus inapoi cu force-push). Sync lasa integrarea
    manuala pentru commit-ul de merge; PushHistory spune acelasi motiv, nu ca
    ar fi amestecate cu commit-uri proprii."""
    local, seed, _ = repos
    base = git(seed, 'rev-parse', 'HEAD')
    git(seed, 'checkout', '-q', '-b', 'feature')
    (seed / 'feat.txt').write_text('feature\n')
    commit(seed, 'feature work')
    git(seed, 'checkout', '-q', 'main')
    (seed / 'code.txt').write_text('main side\n')
    commit(seed, 'main side')
    git(seed, 'merge', '--no-ff', '-q', '-m', 'Merge branch feature', 'feature')
    git(seed, 'push', '-q', 'origin', 'main')
    git(local, 'pull', '-q', '--ff-only')
    git(seed, 'reset', '-q', '--hard', base)
    git(seed, 'push', '-q', '--force', 'origin', 'main')
    append(local, NEW_ROW)
    head = git(local, 'rev-parse', 'HEAD')

    out = run_helper(local, 'PushHistory')

    assert ('[GIT] main local contine commit-uri retrase de pe origin/main; nu comit '
            'extragerile noi peste ele. Commit-urile locale contin un commit de merge: '
            'integrarea ramane manuala, iar extragerile noi raman necomise pana atunci.') in out
    assert 'amestecate cu commit-uri proprii' not in out
    assert 'Sync trece intai' not in out
    assert git(local, 'rev-parse', 'HEAD') == head
    sync = run_helper(local)
    assert 'commit-uri care contin un commit de merge' in sync
    assert 'Integrarea ramane manuala' in sync


@pytest.mark.parametrize('mode', ['Sync', 'PushHistory'])
@pytest.mark.parametrize('case, expected', [
    ('owner', 'safe.directory'),
    ('config', 'bad config line'),
])
def test_a_repository_git_refuses_shows_gits_reason(repos, tmp_path, mode, case, expected):
    """C14: .git exista, dar git refuza checkout-ul (alt proprietar, config
    corupt): mesajul git, cu cauza si remedierea, nu „nu este radacina”."""
    local, _, _ = repos
    empty = tmp_path / 'empty.gitconfig'
    empty.write_text('')
    # Fara safe.directory=* din configurarea globala a statiei.
    env = dict(os.environ, GIT_CONFIG_GLOBAL=str(empty), GIT_CONFIG_NOSYSTEM='1')
    if case == 'owner':
        env['GIT_TEST_ASSUME_DIFFERENT_OWNER'] = '1'
    else:
        with open(local / '.git' / 'config', 'a', encoding='utf-8') as config:
            config.write('garbage [\n')
    code, out = run_helper_rc(local, mode, env=env)
    assert code == 1, out
    assert '[GIT] Git refuza repository-ul: ' in out
    assert expected in out
    assert 'nu este radacina repository-ului' not in out


@pytest.mark.parametrize('where', ['subfolder', 'not_a_repo'])
def test_a_folder_that_is_not_the_repository_root_keeps_the_old_message(repos, tmp_path, where):
    """C14, cazurile neschimbate: subfolder al repository-ului si folder fara .git."""
    local, _, _ = repos
    folder = local / 'sub' if where == 'subfolder' else tmp_path / 'not a repo'
    folder.mkdir()
    code, out = run_helper_rc(local, 'Sync', project_dir=folder)
    assert code == 1, out
    assert '[GIT] Directorul lansatorului nu este radacina repository-ului.' in out
    assert 'Git refuza repository-ul' not in out


def install_post_commit(local):
    hooks = local / 'scripts' / 'git-hooks'
    hooks.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / 'scripts' / 'git-hooks' / 'post-commit', hooks / 'post-commit')
    (hooks / 'post-commit').chmod(0o755)


def commit_with_hook(local, text='new work\n'):
    (local / 'mine.txt').write_text(text)
    git(local, 'add', 'mine.txt')
    p = subprocess.run(['git', '-c', 'core.hooksPath=scripts/git-hooks', 'commit', '-q', '-m', 'new'],
                       cwd=local, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return p.stderr


@pytest.mark.parametrize('fetched', [False, True])
def test_the_auto_push_hook_says_to_integrate_first_when_origin_moved(repos, fetched):
    """C15: cealalta statie a trimis extrageri, iar clona aceasta nu le are:
    push-ul e respins si un simplu „Retry” ar fi respins din nou. Si cu
    origin/main deja adus (non-fast-forward): commit-urile noi nu sunt o rescriere."""
    local, seed, origin = repos
    advance(seed)
    if fetched:
        git(local, 'fetch', '-q', 'origin')
    install_post_commit(local)
    err = commit_with_hook(local)
    assert '[rejected]' in err
    assert 'Retry: git push origin main' not in err
    assert '[git] auto-push respins: origin/main are commit-uri noi' in err
    assert 'START_8000.bat' in err and 'git pull --rebase origin main' in err
    assert git(origin, 'rev-parse', 'main') == git(seed, 'rev-parse', 'HEAD')


@pytest.mark.parametrize('case', ['ok', 'unreachable'])
def test_the_auto_push_hook_other_outcomes_are_unchanged(repos, tmp_path, case):
    """C15, cazurile neschimbate: push reusit si push esuat din alt motiv."""
    local, _, origin = repos
    install_post_commit(local)
    if case == 'unreachable':
        git(local, 'remote', 'set-url', 'origin', str(tmp_path / 'missing.git'))
    err = commit_with_hook(local)
    if case == 'ok':
        assert err.strip().splitlines()[-1] == '[git] auto-push origin/main ok'
        assert git(origin, 'rev-parse', 'main') == git(local, 'rev-parse', 'HEAD')
    else:
        assert err.strip().splitlines()[-1] == (
            '[git] auto-push failed; commit is local. Retry: git push origin main')
        assert 'auto-push respins' not in err


@pytest.mark.parametrize('case', ['rewrite', 'merge'])
def test_the_auto_push_hook_does_not_promise_what_sync_leaves_manual(repos, case):
    """R7 (C15): push respins, dar nu pentru ca origin/main ar avea doar
    commit-uri noi de integrat: un commit trimis, desfacut (reset) si refacut,
    sau un commit de merge netrimis (git merge nu ruleaza post-commit). Sync
    lasa integrarea manuala in ambele cazuri, deci hook-ul nu o promite si nu
    propune git pull --rebase (ar repune corectura peste commit-ul inlocuit)."""
    local, seed, origin = repos
    install_post_commit(local)
    if case == 'rewrite':
        assert commit_with_hook(local).strip().splitlines()[-1] == '[git] auto-push origin/main ok'
        pushed = git(local, 'rev-parse', 'HEAD')
        git(local, 'reset', '-q', '--soft', 'HEAD~1')
        err = commit_with_hook(local, 'corrected work\n')
        why = 'main local rescrie un commit deja trimis (amend sau reset)'
        sync_why = 'commit-uri care rescriu un commit deja trimis (amend sau reset)'
    else:
        git(local, 'checkout', '-q', '-b', 'side')
        (local / 'side.txt').write_text('side\n')
        commit(local, 'side work')
        git(local, 'checkout', '-q', 'main')
        git(local, 'merge', '-q', '--no-ff', '-m', 'local merge', 'side')
        advance(seed)
        pushed = git(seed, 'rev-parse', 'HEAD')
        err = commit_with_hook(local)
        why = 'main local are un commit de merge netrimis'
        sync_why = 'commit-uri care contin un commit de merge'
    assert '[rejected]' in err
    assert err.strip().splitlines()[-1] == (
        '[git] auto-push respins: ' + why + '; commit-ul ramane local, iar '
        'integrarea cu origin/main ramane manuala.')
    assert 'Sync integreaza' not in err and 'git pull --rebase' not in err
    assert git(origin, 'rev-parse', 'main') == pushed
    sync = run_helper(local)
    assert sync_why in sync and 'Integrarea ramane manuala' in sync


def test_a_failed_history_commit_leaves_nothing_staged(repos):
    """R1: commit-ul automat esueaza (aici un hook pre-commit): extragerile ies
    din index, raman pe disc, iar un commit manual ulterior nu le ia."""
    local, _, origin = repos
    hook(local, 'pre-commit', 'echo "commit oprit de test" >&2; exit 1')
    append(local, NEW_ROW)
    head = git(local, 'rev-parse', 'HEAD')

    code, out = run_helper_rc(local, 'PushHistory')

    assert code == 1, out
    assert '[GIT] Commit istoric esuat.' in out
    assert 'Git nu are nume si email' not in out
    assert git(local, 'rev-parse', 'HEAD') == head == git(origin, 'rev-parse', 'main')
    assert git(local, 'diff', '--cached', '--name-only') == ''
    assert git(local, 'diff', '--name-only') == DRAWS
    (local / 'code.txt').write_text('mine\n')
    git(local, 'add', 'code.txt')
    git(local, 'commit', '-q', '-m', 'code only')
    assert git(local, 'show', '--name-only', '--format=', 'HEAD') == 'code.txt'


def test_a_history_commit_without_identity_says_how_to_set_it(repos, tmp_path):
    """R2: pe un calculator fara user.name/user.email commit-ul automat esueaza;
    mesajul spune comanda, iar extragerile nu raman in index."""
    local, _, _ = repos
    for key in ('user.name', 'user.email'):
        git(local, 'config', '--unset', key)
    empty = tmp_path / 'empty.gitconfig'
    empty.write_text('')
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('GIT_AUTHOR_', 'GIT_COMMITTER_')) and key != 'EMAIL'}
    env['GIT_CONFIG_GLOBAL'] = str(empty)
    probe = subprocess.run(['git', 'var', 'GIT_COMMITTER_IDENT'], cwd=local, env=env,
                           capture_output=True, text=True, timeout=30)
    if probe.returncode == 0:
        pytest.skip('git deduce singur numele si emailul pe acest calculator')
    append(local, NEW_ROW)

    code, out = run_helper_rc(local, 'PushHistory', env=env)

    assert code == 1, out
    assert '[GIT] Git nu are nume si email pe acest calculator' in out
    assert '[GIT]   git config --global user.name "' in out
    assert '[GIT]   git config --global user.email "' in out
    assert out.rstrip().endswith('[GIT] Commit istoric esuat.')
    assert git(local, 'diff', '--cached', '--name-only') == ''


@pytest.fixture
def auth_required_url():
    """Remote HTTP care cere autentificare (401), ca GitHub fara credentiale salvate."""
    import http.server
    import threading

    class Deny(http.server.BaseHTTPRequestHandler):
        def _deny(self):
            self.send_response(401)
            self.send_header('WWW-Authenticate', 'Basic realm="test"')
            self.send_header('Content-Length', '0')
            self.end_headers()

        do_GET = do_POST = _deny

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Deny)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_address[1]}/repo.git'
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize('mode', ['Sync', 'PushHistory'])
@pytest.mark.parametrize('cause', ['auth', 'unreachable'])
def test_a_push_without_saved_github_login_says_how_to_save_it(
    repos, tmp_path, auth_required_url, mode, cause,
):
    """R2: push-ul cere autentificare, iar lansatorul nu poate intreba: mesajul
    spune pasul manual, o data. Un push esuat din alt motiv nu primeste sfatul."""
    local, _, origin = repos
    # fetch ramane pe origin; numai push-ul merge la remote-ul de test.
    push_url = auth_required_url if cause == 'auth' else str(tmp_path / 'missing.git')
    git(local, 'config', 'remote.origin.pushurl', push_url)
    # Fara GCM (credential.helper din configurarea sistemului) si fara askpass.
    git(local, 'config', 'credential.helper', '')
    env = {key: value for key, value in os.environ.items()
           if key.upper() not in {'GIT_ASKPASS', 'SSH_ASKPASS', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'}}
    env['NO_PROXY'] = '127.0.0.1'
    if mode == 'Sync':
        (local / 'mine.txt').write_text('own work\n')
        commit(local, 'own work')
    else:
        append(local, NEW_ROW)
    head_before = git(origin, 'rev-parse', 'main')

    code, out = run_helper_rc(local, mode, env=env)

    hint = '[GIT] Autentificarea GitHub nu este salvata pe acest calculator.'
    if cause == 'auth':
        assert 'could not read Username' in out
        assert hint in out and 'git push origin main' in out
    else:
        assert hint not in out
    if mode == 'Sync':
        assert code == 0, out
        assert '[GIT] Push esuat - commit-urile raman locale; se reincearca la urmatoarea pornire.' in out
    else:
        assert code == 1, out
        assert out.rstrip().endswith('[GIT] Push esuat - commit-urile raman locale pentru reincercare.')
    assert git(origin, 'rev-parse', 'main') == head_before

"""scripts/check_repository.py reads what git records: the index, or every commit with --history."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('check_repository', Path(__file__).parents[1] / 'scripts' / 'check_repository.py')
check_repository = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_repository)
TOKEN = 'gh' + 'p_' + 'a' * 30  # assembled so that this file itself passes the check


def git(root, *args):
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=t', '-c', 'user.email=t@example.com', *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    git(root, 'init', '-q')
    return root


def found(root, history=False):
    return dict(check_repository.problems(root, history))


def add(root, name, content):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode())
    git(root, 'add', name)


def test_a_clean_index_passes_and_an_empty_one_fails(repo):
    assert '(repository)' in found(repo)
    add(repo, 'README.md', 'hello\n')
    assert found(repo) == {}


@pytest.mark.parametrize(
    'name,content,reason',
    [
        ('notes.md', 'key = ' + TOKEN + '\n', 'credential'),
        ('notes.md', 'see /Us' + 'ers/someone\n', 'personal path'),
        ('notes.md', 'mail someone@' + 'mail.com\n', 'address'),
        ('notes.md', ('1' * 240 + '\r\n') * 2, 'fixed-width'),
        ('src/pkg/Data/rows.json', '{}\n', 'data or run directory'),
        ('src/jockeybench/data/raw/rows.py', 'x = 1\n', 'data or run directory'),
        ('Kyi/KYI240106.txt', 'x\n', 'JRDB archive'),
        ('a@b/rows.csv', 'x\n', 'file type'),
        ('blob.sqlite', b'\x00', 'file type'),
    ],
)
def test_staged_files_that_could_carry_data_or_secrets_are_refused(repo, name, content, reason):
    add(repo, name, content)
    assert reason in found(repo)[name]


def test_the_package_data_module_is_code_not_data(repo):
    add(repo, 'src/jockeybench/data/source.py', 'x = 1\n')
    assert found(repo) == {}


def test_the_index_is_checked_not_the_working_tree(repo):
    add(repo, 'notes.md', 'key = ' + TOKEN + '\n')
    (repo / 'notes.md').write_text('clean\n')  # fixed on disk only
    assert 'credential' in found(repo)['notes.md']


def test_history_finds_a_secret_removed_in_a_later_commit(repo):
    add(repo, 'notes.md', 'key = ' + TOKEN + '\n')
    git(repo, 'commit', '-qm', 'one')
    add(repo, 'notes.md', 'clean\n')
    git(repo, 'commit', '-qm', 'two')
    assert found(repo) == {}
    assert any(label.startswith('notes.md@') for label in found(repo, history=True))


def test_a_project_in_a_subdirectory_checks_only_its_own_files(repo):
    add(repo, 'other/notes.md', 'key = ' + TOKEN + '\n')  # another project in the same repository
    add(repo, 'project/notes.md', 'clean\n')
    assert found(repo / 'project') == {}
    add(repo, 'project/data/rows.json', '{}\n')
    assert found(repo / 'project') == {'project/data/rows.json': 'path inside a data or run directory'}


def test_a_shallow_clone_is_refused(repo, tmp_path):
    add(repo, 'README.md', 'hello\n')
    git(repo, 'commit', '-qm', 'one')
    git(repo, 'commit', '-qm', 'two', '--allow-empty')
    shallow = tmp_path / 'shallow'
    subprocess.run(['git', 'clone', '-q', '--depth', '1', repo.as_uri(), str(shallow)], check=True, capture_output=True)
    assert 'shallow' in found(shallow, history=True)['(repository)']

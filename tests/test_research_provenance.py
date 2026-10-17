"""Current hashes and historical provenance remain separate; no scientific imports."""

import importlib.util
import io
import json
import shutil
import subprocess
import tarfile
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('research_provenance', ROOT / 'scripts/verify_research_provenance.py')
provenance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provenance)


@pytest.fixture
def source_copy(tmp_path):
    target = tmp_path / 'source'
    shutil.copytree(ROOT / 'src', target / 'src', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copytree(ROOT / provenance.EVIDENCE, target / provenance.EVIDENCE)
    return target


def test_migration_has_complete_current_hashes_and_preserved_original_evidence():
    result = provenance.verify(ROOT)
    assert result['passed'], result['errors']
    assert result['migrated_python_files'] == 128
    assert result['excluded_historical_files'] == 27
    assert result['scientific_module_imports'] == 0
    assert result['provider_calls'] == 0
    assert result['changed_original_files'] > 0


def test_intentional_migration_does_not_relabel_changed_bytes_as_original():
    manifest = provenance.read(ROOT / provenance.EVIDENCE / 'migration-manifest.json')
    originals = provenance.read(ROOT / provenance.EVIDENCE / 'source-manifest.json')['files']
    by_path = {'ActiveResearch/' + row['path']: row for row in originals}
    for row in manifest['migrated_files']:
        assert row['original_sha256'] == by_path[row['old_path']]['sha256']
        assert row['original_source'] == by_path[row['old_path']]['source']
        assert row['changed'] == (row['original_sha256'] != row['current_sha256'])


def test_changed_current_source_fails_without_updating_provenance(source_copy):
    path = source_copy / provenance.RESEARCH / 'common_scoring.py'
    path.write_bytes(path.read_bytes() + b'\n# Unreviewed change\n')
    assert not provenance.verify(source_copy)['passed']


def test_source_archive_preserves_recorded_bytes_with_windows_git_defaults(source_copy, tmp_path):
    shutil.copyfile(ROOT / '.gitattributes', source_copy / '.gitattributes')

    def git(*args):
        return subprocess.run(['git', '-C', str(source_copy), *args], check=True, capture_output=True).stdout

    git('init', '-q')
    git('config', 'core.autocrlf', 'true')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    git('config', 'commit.gpgsign', 'false')
    git('add', '.')
    git('-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'Synthetic source archive fixture')
    destination = tmp_path / 'extracted'
    archive = git('archive', '--format=tar', 'HEAD')
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
        # Compare archive bytes to committed blobs, independently of checkout conversion.
        for path in (
            provenance.RESEARCH / 'public.py',
            provenance.RESEARCH / 'daily_reflection_budget.json',
            provenance.EVIDENCE / 'initial-source-manifest.json',
        ):
            name = path.as_posix()
            assert stream.extractfile(name).read() == git('show', 'HEAD:' + name), name
        stream.extractall(destination, filter='data')
    result = provenance.verify(destination)
    assert result['passed'], result['errors']


def test_new_unrecorded_source_is_detected(source_copy):
    (source_copy / provenance.RESEARCH / 'unrecorded.py').write_text('VALUE = 1\n')
    assert not provenance.verify(source_copy)['passed']


def test_record_refuses_to_rewrite_changed_historical_evidence(source_copy):
    path = source_copy / provenance.EVIDENCE / 'initial-source-manifest.json'
    baseline = provenance.read(path)
    baseline['files'][0]['sha256'] = '0' * 64
    path.write_text(json.dumps(baseline))
    with pytest.raises(ValueError, match='Original evidence changed'):
        provenance.record(source_copy)


def test_recorded_old_namespace_import_still_fails(source_copy):
    (source_copy / provenance.RESEARCH / 'forbidden_import.py').write_text('import jrdb_lh\n')
    provenance.record(source_copy)
    result = provenance.verify(source_copy)
    assert not result['passed']
    assert any('Old framework namespace imports remain' in error for error in result['errors'])


def test_unresolved_internal_import_is_not_hidden_by_recording(source_copy):
    (source_copy / provenance.RESEARCH / 'missing_import.py').write_text('from jockeybench.research.absent_module import missing\n')
    provenance.record(source_copy)
    result = provenance.verify(source_copy)
    assert not result['passed']
    assert any('Unresolved internal imports' in error for error in result['errors'])


def test_single_distribution_collects_only_supported_root_tests():
    config = tomllib.loads((ROOT / 'pyproject.toml').read_text())
    assert config['project']['name'] == 'jockeybench'
    assert config['tool']['pytest']['ini_options']['testpaths'] == ['tests']
    assert config['tool']['setuptools']['packages']['find']['include'] == ['jockeybench*']
    assert 'daily_reflection_budget.json' in config['tool']['setuptools']['package-data']['jockeybench.research']
    assert not (ROOT / 'provenance/active-research/pyproject.toml').exists()
    assert not (ROOT / 'src/active_research').exists()
    assert not (ROOT / 'src/jrdb_lh').exists()

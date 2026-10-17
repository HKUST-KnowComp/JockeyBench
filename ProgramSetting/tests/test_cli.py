import json

import pytest

import jockeybench.cli as cli


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def test_data_and_model_commands_refuse_hosts_without_the_marker(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli.platform, 'system', lambda: 'Darwin')
    for argv in (
        ['doctor'],
        ['run', '--run-id', 'x'],
        ['replay-audit', '--run-id', 'x'],
        ['data', 'build', '--raw', 'r', '--docs', 'd', '--database', 'db', '--start', '2024-01-01', '--end', '2024-01-02'],
    ):
        code, out = run(capsys, *argv)
        assert code == 2 and '.experiment-host' in out['reason']


def test_full_execution_requires_macos(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / cli.HOST_MARKER).touch()
    monkeypatch.setattr(cli.platform, 'system', lambda: 'Linux')
    code, out = run(capsys, 'doctor')
    assert code == 2 and 'macOS' in out['reason']


def test_run_names_are_validated_before_anything_runs(capsys):
    code, out = run(capsys, 'run', '--run-id', '../escape')
    assert code == 2 and out['reason'] == 'Invalid run name'


def test_layouts_need_no_data(capsys):
    code, out = run(capsys, 'data', 'layouts')
    assert code == 0 and out['SED'] == {'archive_directory': 'Sed', 'specification': 'Sed/sed_doc.txt'}


def test_a_provider_without_its_credential_sends_nothing(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / cli.HOST_MARKER).touch()
    monkeypatch.setattr(cli.platform, 'system', lambda: 'Darwin')
    monkeypatch.delenv('UNIT_KEY', raising=False)
    provider = tmp_path / 'provider.json'
    provider.write_text(json.dumps({'model': 'm', 'base_url': 'https://example.invalid', 'api_key_env': 'UNIT_KEY'}))
    with pytest.raises(cli.Rejected, match='credential is unavailable'):
        cli.make_agent(cli.build_parser().parse_args(['run', '--run-id', 'x', '--provider-config', str(provider)]))

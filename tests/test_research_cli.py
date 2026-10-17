import json

import pytest

from jockeybench import cli
from jockeybench.research.profile import provider_settings


def test_setting_help_requires_no_data_or_credentials(capsys):
    assert cli.main(['--help']) == 0
    assert 'research' in capsys.readouterr().out
    for setting in ('program', 'research'):
        with pytest.raises(SystemExit) as caught:
            cli.main([setting, '--help'])
        assert caught.value.code == 0
        assert 'run' in capsys.readouterr().out


@pytest.mark.parametrize('system', ['Darwin', 'Linux'])
def test_research_commands_guard_host_before_data_or_provider(capsys, tmp_path, monkeypatch, system):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli.platform, 'system', lambda: system)
    for command in (
        ['doctor'],
        ['replay-audit', '--run-id', 'unit'],
        ['run', '--run-id', 'unit', '--provider-config', 'missing.json'],
    ):
        assert cli.main(['research', *command]) == 2
        assert '.experiment-host' in json.loads(capsys.readouterr().out)['reason']


def test_research_run_name_is_validated_before_storage(capsys):
    assert cli.main(['research', 'report', '--run-id', '../escape']) == 2
    assert json.loads(capsys.readouterr().out)['reason'] == 'Invalid run name'


def test_public_provider_configuration_never_contains_secrets():
    from jockeybench.errors import Rejected

    with pytest.raises(Rejected, match='unknown fields'):
        provider_settings({'api_key': 'not-a-real-key'})
    with pytest.raises(Rejected, match='requires model'):
        provider_settings({})

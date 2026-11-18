import json

import pytest

from jockeybench import cli
from jockeybench.research.profile import provider_settings


def test_setting_help_requires_no_data_or_credentials(capsys):
    assert cli.main(['--help']) == 0
    help_text = capsys.readouterr().out
    assert 'code-as-policy (program)' in help_text and 'llm-as-policy (research)' in help_text


@pytest.mark.parametrize(
    ('prefix', 'usage', 'arm_option'),
    [
        ('code-as-policy', 'jockeybench code-as-policy', '--reference'),
        ('Code-as-policy', 'jockeybench code-as-policy', '--reference'),
        ('program', 'jockeybench program', '--reference'),
        ('', 'jockeybench', '--reference'),
        ('llm-as-policy', 'jockeybench llm-as-policy', '--arm {adaptive,frozen}'),
        ('LLM-as-policy', 'jockeybench llm-as-policy', '--arm {adaptive,frozen}'),
        ('research', 'jockeybench research', '--arm {adaptive,frozen}'),
    ],
)
def test_workflow_run_help(capsys, prefix, usage, arm_option):
    args = [prefix] if prefix else []
    with pytest.raises(SystemExit) as caught:
        cli.main([*args, 'run', '--help'])
    help_text = capsys.readouterr().out
    assert caught.value.code == 0
    assert help_text.startswith(f'usage: {usage} run ') and arm_option in help_text


@pytest.mark.parametrize('system', ['Darwin', 'Linux'])
@pytest.mark.parametrize('prefix', ['llm-as-policy', 'research'])
def test_research_commands_guard_host_before_data_or_provider(capsys, tmp_path, monkeypatch, system, prefix):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli.platform, 'system', lambda: system)
    for command in (
        ['doctor'],
        ['replay-audit', '--run-id', 'unit'],
        ['run', '--run-id', 'unit', '--provider-config', 'missing.json'],
    ):
        assert cli.main([prefix, *command]) == 2
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

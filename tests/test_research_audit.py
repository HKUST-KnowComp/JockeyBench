"""Completed-trajectory audit checks beyond individual valid checkpoint counts."""

import gzip
import json
import shutil

import pytest

import test_research_lifecycle as lifecycle_fixtures
from conftest import make_source
from jockeybench.errors import Rejected
from jockeybench.research import public
from jockeybench.storage import checkpoint_digest, read_checkpoint

SETTINGS = lifecycle_fixtures.SETTINGS
fake_provider = lifecycle_fixtures.fake_provider
research_config = lifecycle_fixtures.research_config


@pytest.fixture
def completed(research_config, fake_provider):
    factory = lambda *args: make_source()
    report = public.execute(research_config, 'audit', SETTINGS, source_factory=factory)
    return research_config, public.run_path(research_config, 'audit'), factory, report, fake_provider


def mutate_json(path, change):
    value = json.loads(path.read_text())
    change(value)
    path.write_text(json.dumps(value))


def mutate_checkpoint(path, change):
    state = read_checkpoint(path)
    change(state)
    state['checkpoint_digest'] = checkpoint_digest(state)
    with gzip.open(path, 'wt') as stream:
        json.dump(state, stream)


def test_complete_audit_is_offline_and_covers_exact_schedule(completed):
    config, out, factory, report, calls = completed
    count = len(calls)
    audit = public.audit(config, 'audit', source_factory=factory)
    assert audit['verified_races'] == report['races']
    assert [row['run_id'] for row in audit['results']] == ['PAR-' + str(i + 1).zfill(6) for i in range(report['races'])]
    assert len(calls) == count


def test_duplicate_valid_checkpoint_cannot_replace_another_race(completed):
    config, out, factory, report, _ = completed
    checkpoints = sorted((out / 'runs').glob('PAR-*/checkpoint.json.gz'))
    assert len(checkpoints) == report['races'] >= 2
    shutil.copyfile(checkpoints[0], checkpoints[1])
    # Every file still has a valid internal digest and the count is unchanged.
    assert read_checkpoint(checkpoints[0]) == read_checkpoint(checkpoints[1])
    with pytest.raises(Rejected, match='ordered race identity'):
        public.audit(config, 'audit', source_factory=factory)


def test_duplicate_cannot_hide_behind_renamed_run_id(completed):
    config, out, factory, _, _ = completed
    checkpoints = sorted((out / 'runs').glob('PAR-*/checkpoint.json.gz'))
    shutil.copyfile(checkpoints[0], checkpoints[1])
    mutate_checkpoint(checkpoints[1], lambda state: state.update(run_id=checkpoints[1].parent.name))
    with pytest.raises(Rejected, match='ordered race identity'):
        public.audit(config, 'audit', source_factory=factory)


@pytest.mark.parametrize('field', ['scores', 'release_ledger', 'feedback'])
def test_individually_valid_but_unreleased_checkpoint_is_incomplete(completed, field):
    config, out, factory, _, _ = completed
    path = out / 'runs/PAR-000001/checkpoint.json.gz'
    mutate_checkpoint(path, lambda state: state[field].clear())
    with pytest.raises(Rejected, match='completely sealed, released and scored'):
        public.audit(config, 'audit', source_factory=factory)


@pytest.mark.parametrize('field', ['source', 'environment'])
def test_each_checkpoint_must_match_trajectory_source_and_environment(completed, field):
    config, out, factory, _, _ = completed
    path = out / 'runs/PAR-000001/checkpoint.json.gz'
    mutate_checkpoint(path, lambda state: state['binding'][field].update(injected='different binding'))
    with pytest.raises(Rejected, match=field + ' differs'):
        public.audit(config, 'audit', source_factory=factory)


@pytest.mark.parametrize('artifact', ['initial', 'accounting', 'status'])
def test_initial_inputs_and_completion_receipt_are_verified(completed, artifact):
    config, out, factory, _, _ = completed
    if artifact == 'initial':
        mutate_json(out / 'initial-policy.json', lambda value: value.update(source=value['source'] + '\n# changed\n'))
    elif artifact == 'accounting':
        mutate_json(out / 'warmup-accounting.json', lambda value: value.update(weighted_ledger_cost=value['weighted_ledger_cost'] + 1))
    else:
        mutate_json(out / 'status.json', lambda value: value.update(completed_days=[]))
    with pytest.raises(Rejected):
        public.audit(config, 'audit', source_factory=factory)


def test_frozen_audit_checks_recorded_origin_link(completed):
    config, out, factory, _, calls = completed
    public.execute(config, 'frozen-audit', SETTINGS, arm='frozen', initial_from='audit', source_factory=factory)
    frozen = public.run_path(config, 'frozen-audit')
    count = len(calls)
    assert public.audit(config, 'frozen-audit', source_factory=factory)['verified_races'] > 0
    assert len(calls) == count
    mutate_json(frozen / 'matched-initial.json', lambda value: value.update(source_run_id='unrelated-adaptive'))
    with pytest.raises(Rejected, match='matched provenance'):
        public.audit(config, 'frozen-audit', source_factory=factory)

"""Full public research lifecycle with fabricated data and an in-memory provider."""

import copy
import json
from dataclasses import replace

import pytest

from conftest import make_source, native
from jockeybench.agents import provider
from jockeybench.canonical import digest
from jockeybench.errors import Rejected
from jockeybench.research import public
from jockeybench.runner import Run
from jockeybench.storage import read_checkpoint

ORIGINAL_PREFLIGHT = Run.preflight

CODE = """def collect_evidence(sdk, context):
    sdk.call("track_context")
    for horse in context["runners"]:
        sdk.call("recent_history", horse_number=horse["number"], depth=1)
        sdk.call("recent_history", horse_number=horse["number"], depth=3)
        sdk.call("training", horse_number=horse["number"])
    return sdk.package()
"""
SETTINGS = {
    'model': 'synthetic-model',
    'base_url': 'https://invalid.example/v1',
    'api_key_env': 'SYNTHETIC_API_KEY',
    'timeout_seconds': 1,
    'output_token_field': 'max_tokens',
    'input_usd_per_million': 0.1,
    'output_usd_per_million': 0.2,
    'max_estimated_cost_usd': 1,
    'reservation_output_tokens': 100,
    'retry_attempts': 1,
    'retry_base_seconds': 1,
    'retry_max_seconds': 1,
}


@pytest.fixture
def research_config(config):
    return replace(
        config,
        schedule_mode='smoke',
        smoke_days=2,
        races_per_day=2,
        limits=replace(config.limits, page_size=8),
        tariff=replace(config.tariff, hit_bonus_yen=0),
    )


@pytest.fixture
def fake_provider(monkeypatch):
    calls = []

    def request(service, settings, body, request_id, phase):
        calls.append({'phase': phase, 'request': copy.deepcopy(body), 'run_id': service.state['run_id']})
        index = int(phase.rsplit('-', 1)[-1]) - 1
        if phase.startswith('warmup-'):
            name, args = [
                ('inspect_contract', {}),
                ('validate_research_policy', {'source': CODE}),
                ('save_research_policy', {'source': CODE}),
                ('finish', {}),
            ][index]
        elif phase.startswith('policy_update-'):
            name, args = [('inspect_contract', {}), ('finish', {})][index]
        else:
            assert phase == 'prediction-1'
            observation = json.loads(body['messages'][1]['content'])
            numbers = [r['number'] for r in observation['basic_race_card']['runners']]
            name, args = 'submit', {'ranking': numbers, 'probabilities': {n: (0.6 if i == 0 else 0.1) for i, n in enumerate(numbers)}}
        message = {
            'role': 'assistant',
            'content': '',
            'tool_calls': [{'id': request_id, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}],
        }
        attempt = {
            'request_id': request_id,
            'status': 'complete',
            'elapsed_seconds': 0.001,
            'phase': service.state['phase'],
            'index': service.state['index'],
            'reserved_cost_usd': 0.001,
        }
        service.state.setdefault('provider_attempts', []).append(attempt)
        return {
            'model': settings['model'],
            'choices': [{'message': message, 'finish_reason': 'tool_calls'}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5},
        }, attempt

    monkeypatch.setattr(provider, 'request', request)
    monkeypatch.setattr(Run, 'preflight', lambda self: self.state.update(isolation={'fixture_only': True}))
    return calls


def test_adaptive_and_matched_frozen_share_warmup_and_release_after_sealing(research_config, fake_provider):
    factory = lambda *args: make_source()
    adaptive = public.execute(research_config, 'adaptive', SETTINGS, source_factory=factory)
    assert adaptive['completed'] and adaptive['races'] == 3 and adaptive['days'] == 2
    assert adaptive['updater_sessions'] == 2
    assert adaptive['api_usage']['incurred_this_run']['logical_requests'] == 11
    assert adaptive['api_usage']['incurred_this_run']['cost_coverage_complete']
    assert adaptive['warmup']['accounting']['normalization_races'] == 3
    assert adaptive['delivery_totals'] == {'direct_predictor': 3}
    out = public.run_path(research_config, 'adaptive')
    events = json.loads((out / 'events.json').read_text())
    for day in ['2024-01-06', '2024-01-07']:
        kinds = [e['kind'] for e in events if e.get('day') == day]
        assert kinds.index('sealed') < kinds.index('release_gate') < kinds.index('result_released') < kinds.index('frame_commit')
    assert public.audit(research_config, 'adaptive', source_factory=factory)['verified_races'] == 3
    calls_before = len(fake_provider)
    frozen = public.execute(research_config, 'frozen', SETTINGS, arm='frozen', initial_from='adaptive', source_factory=factory)
    assert frozen['completed'] and frozen['updater_sessions'] == 0
    assert frozen['initial_policy_hash'] == adaptive['initial_policy_hash']
    assert frozen['warmup']['accounting'] == adaptive['warmup']['accounting']
    assert frozen['api_usage']['incurred_this_run']['logical_requests'] == 3
    assert frozen['api_usage']['attributed_total']['logical_requests'] == 7
    assert frozen['api_usage']['updaters']['logical_requests'] == 0
    assert [x['phase'] for x in fake_provider[calls_before:]] == ['prediction-1', 'prediction-1', 'prediction-1']
    assert public.audit(research_config, 'frozen', source_factory=factory)['verified_races'] == 3
    with pytest.raises(Rejected, match='fresh research run name'):
        public.execute(research_config, 'adaptive', SETTINGS, source_factory=factory)


def test_changed_model_identity_stops_before_any_release(research_config, fake_provider, monkeypatch):
    original = provider.request

    def wrong_model(*args):
        response, attempt = original(*args)
        if args[-1] == 'prediction-1':
            response['model'] = 'different-model'
        return response, attempt

    monkeypatch.setattr(provider, 'request', wrong_model)
    with pytest.raises(Rejected):
        public.execute(research_config, 'wrong-model', SETTINGS, source_factory=lambda *args: make_source())
    out = public.run_path(research_config, 'wrong-model')
    status = json.loads((out / 'status.json').read_text())
    assert status['status'] == 'stopped' and not status['completed_days']
    state = read_checkpoint(out / 'runs/PAR-000001/checkpoint.json.gz')
    assert state['model_identity_hard_stop']['observed'] == 'different-model'
    assert state['scores'] == {} and state['release_ledger'] == {}
    assert not (out / 'report.json').exists()
    count = len(fake_provider)
    with pytest.raises(Rejected, match='fresh research run name'):
        public.execute(research_config, 'wrong-model', SETTINGS, source_factory=lambda *args: make_source())
    assert len(fake_provider) == count


def test_audit_rejects_changed_manifest(research_config, fake_provider):
    public.execute(research_config, 'audit', SETTINGS, source_factory=lambda *args: make_source())
    out = public.run_path(research_config, 'audit')
    manifest = json.loads((out / 'manifest.json').read_text())
    manifest['schedule'][0]['races'].append('injected')
    assert manifest['digest'] != digest({k: v for k, v in manifest.items() if k != 'digest'})
    (out / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(Rejected, match='manifest digest'):
        public.audit(research_config, 'audit', source_factory=lambda *args: make_source())


def test_matched_frozen_preserves_p0_after_multiple_warmup_saves(research_config, fake_provider):
    from test_research_warmup import scripted

    steps = [
        ('inspect_contract', {}),
        ('validate_research_policy', {'source': CODE}),
        ('save_research_policy', {'source': CODE}),
        ('save_research_policy', {'source': CODE}),
        ('finish', {}),
    ]
    factory = lambda *args: make_source()
    public.execute(research_config, 'multiple-saves', SETTINGS, source_factory=factory, warmup_caller=scripted(lambda i, s: steps[i]))
    out = public.run_path(research_config, 'multiple-saves')
    initial = json.loads((out / 'initial-policy.json').read_text())
    assert initial['policy_version'] == 'v001'
    public.execute(research_config, 'matched', SETTINGS, arm='frozen', initial_from='multiple-saves', source_factory=factory)
    matched = public.run_path(research_config, 'matched')
    assert json.loads((matched / 'initial-policy.json').read_text()) == initial
    assert json.loads((matched / 'policies/research_policy_v001.json').read_text()) == initial
    assert (matched / 'policies/research_policy_v001.py').read_text() == CODE


def test_environment_change_between_manifest_and_race_stops(research_config, fake_provider, monkeypatch):
    original = public.probe

    def changed(*args):
        return {**original(*args), 'changed_fixture_environment': True}

    monkeypatch.setattr(public, 'probe', changed)
    with pytest.raises(Rejected, match='environment changed'):
        public.execute(research_config, 'changed-env', SETTINGS, source_factory=lambda *args: make_source())
    out = public.run_path(research_config, 'changed-env')
    assert json.loads((out / 'status.json').read_text())['status'] == 'stopped'
    assert not (out / 'report.json').exists()


def test_report_distinguishes_unknown_api_cost_from_zero(research_config, fake_provider, monkeypatch):
    original = provider.request
    changed = False

    def missing_usage(*args):
        nonlocal changed
        response, attempt = original(*args)
        if args[-1] == 'prediction-1' and not changed:
            response.pop('usage')
            changed = True
        return response, attempt

    monkeypatch.setattr(provider, 'request', missing_usage)
    report = public.execute(research_config, 'unknown-cost', SETTINGS, source_factory=lambda *args: make_source())
    usage = report['api_usage']['incurred_this_run']
    assert usage['unknown_cost_outputs'] == 1
    assert not usage['cost_coverage_complete']
    assert usage['estimated_cost_usd'] > 0
    assert report['api_cost_in_score'] is False


@native
@pytest.mark.native
def test_public_lifecycle_with_real_native_isolation(research_config, fake_provider, monkeypatch):
    monkeypatch.setattr(Run, 'preflight', ORIGINAL_PREFLIGHT)
    config = replace(research_config, smoke_days=1, races_per_day=1)
    report = public.execute(config, 'native', SETTINGS, source_factory=lambda *args: make_source())
    assert report['completed'] and report['delivery_totals'] == {'direct_predictor': 1}
    out = public.run_path(config, 'native')
    state = read_checkpoint(out / 'runs/PAR-000001/checkpoint.json.gz')
    assert state['isolation']['network_denied'] and state['isolation']['subprocess_denied']
    assert state['isolation']['filesystem_denied'] >= 3
    assert public.audit(config, 'native', source_factory=lambda *args: make_source())['verified_races'] == 1

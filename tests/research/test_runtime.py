"""Public-core research regression: synthetic source, policies and mocked HTTP only.

These tests retain the original acquisition, delivery, release and updater
invariants without importing historical test modules or their external fixtures.
"""

import copy
import io
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from jockeybench.canonical import code_hash, digest, packed
from jockeybench.clock import cutoff, release
from jockeybench.errors import Rejected
from jockeybench.ledger import reconcile, summary
from jockeybench.protocol.metering import PROGRAM_BUDGET_VERSION
from jockeybench.research.finegrained_v3 import control, runtime
from jockeybench.research.finegrained_v3.audit_live import evidence_lifecycle
from jockeybench.research.finegrained_v3.frame import extract, make_frame
from jockeybench.research.finegrained_v3.marginal_cost import MarginalAcquisition, audit_owned_cache
from jockeybench.research.finegrained_v3.marginal_cost import seal as seal_cache
from jockeybench.research.finegrained_v3.native_replay import replay_economic
from jockeybench.research.finegrained_v3.policy_builder import Registry, construct, revise
from jockeybench.research.finegrained_v3.policy_contract import PolicyInvalid, synthetic_cards, validate_source
from jockeybench.research.finegrained_v3.policy_executor import EvidenceSDK, replay_acquisition
from jockeybench.research.finegrained_v3.updater_context import context, lifecycle_checkpoint
from jockeybench.research.policy_amortized.lifecycle import coordinate
from jockeybench.research.policy_amortized.service import PolicyService
from jockeybench.research.runner import basic_card, settle

SETTINGS = {
    'research_profile': 'jockeybench-research-v1',
    'model': 'synthetic-research-model',
    'base_url': 'https://synthetic.invalid/v1',
    'api_key_env': 'JOCKEYBENCH_SYNTHETIC_TEST_KEY',
    'timeout_seconds': 10,
    'output_token_field': 'max_tokens',
    'temperature': 0,
    'input_usd_per_million': 0.06,
    'output_usd_per_million': 0.18,
    'retry_attempts': 1,
    'reservation_output_tokens': 4096,
    'max_estimated_cost_usd': 0.75,
}

CODE = '''def collect_evidence(sdk, context):
    sdk.call("track_context")
    h = context["runners"][0]["number"]
    sdk.call("recent_history", horse_number=h, depth=1)
    sdk.call("recent_history", horse_number=h, depth=3)
    sdk.call("recent_history", horse_number=h, depth=5)
    sdk.call("recent_history", horse_number=h, depth=5)
    sdk.call("condition_stats", horse_number=h)
    for h in context["runners"]:
        sdk.call("training", horse_number=h["number"])
    return sdk.package()
'''
SHORT_CODE = '''def collect_evidence(sdk, context):
    sdk.call("track_context")
    return sdk.package()
'''


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('No real network or credential access in research tests')

    monkeypatch.setattr('socket.socket.connect', forbidden)
    monkeypatch.setattr('urllib.request.urlopen', forbidden)
    monkeypatch.setattr('jockeybench.agents.provider.require_credential', forbidden)
    monkeypatch.delenv(SETTINGS['api_key_env'], raising=False)


@pytest.fixture
def memory_run(service):
    source = service.source
    # Five earlier starts, with exactly the same fabricated runners and fields.
    for i, day in enumerate(('2023-11-27', '2023-11-28', '2023-11-29')):
        rid = 'synthetic-support-' + str(i)
        source.con.execute(
            'INSERT INTO races SELECT ?,?,?,?,?,context FROM races WHERE id=?',
            (rid, day, cutoff(day), release(day) - 3600, release(day) + 5 * 86400, 'raw0'),
        )
        source.con.execute(
            'INSERT INTO entries SELECT ?,number,horse_id,jockey_id,trainer_id,pre,history,settlement,? FROM entries WHERE race_id=?',
            (rid, release(day) + 5 * 86400, 'raw0'),
        )
        source.con.execute('INSERT INTO expanded_race_ids VALUES(?,?)', (rid, source.token('race', rid)))
    source.con.commit()
    cfg = replace(
        service.config,
        tariff=replace(service.config.tariff, hit_bonus_yen=0),
        limits=replace(service.config.limits, page_size=8, strategy_calls=64),
    )
    state = service.state
    state['schedule'][0]['races'] = state['schedule'][0]['races'][:1]
    state.update(
        scores={},
        release_ledger={},
        binding={
            'config': cfg.public(),
            'code': code_hash(),
            'budget_version': PROGRAM_BUDGET_VERSION,
            'source': source.manifest(),
        },
    )
    broker = PolicyService(source, cfg, state)
    return SimpleNamespace(config=cfg, source=source, state=state, service=broker, persist=lambda: None)


@pytest.fixture
def policy(tmp_path):
    return Registry(tmp_path / 'policies').save(CODE)


def predict_mocked(run, policy, monkeypatch, mode='direct'):
    requests = []
    acquisition_snapshots = []

    def http(request, timeout):
        body = json.loads(request.data)
        requests.append(body)
        acquisition_snapshots.append(digest([run.state['active_trace'], run.state['ledger']]))
        repairing = 'Research is now closed.' in body['messages'][0]['content']
        if mode in {'repair', 'fallback'} and (not repairing or mode == 'fallback'):
            raise OSError('synthetic unavailable response')
        observation = json.loads(body['messages'][1]['content'])
        numbers = [r['number'] for r in observation['basic_race_card']['runners']]
        args = {'ranking': numbers, 'probabilities': {n: 0.6 if i == 0 else 0.4 / (len(numbers) - 1) for i, n in enumerate(numbers)}}
        result = {
            'model': SETTINGS['model'],
            'choices': [
                {
                    'finish_reason': 'tool_calls',
                    'message': {
                        'role': 'assistant',
                        'content': '',
                        'tool_calls': [
                            {'id': 'synthetic-submit', 'type': 'function', 'function': {'name': 'submit', 'arguments': json.dumps(args)}}
                        ],
                    },
                }
            ],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 10, 'estimated_cost': 0.001},
        }
        return io.BytesIO(packed(result))

    monkeypatch.setattr('urllib.request.urlopen', http)
    got = runtime.predict(run, runtime.Predictor(SETTINGS, policy))
    return got, requests, acquisition_snapshots


def released_frame(run, policy):
    state = run.state
    day, race = state['schedule'][0]['day'], state['schedule'][0]['races'][0]
    state['prediction_phase_cost'] = summary(state, run.config)
    state.pop('strategy_job', None)
    state.pop('strategy_job_usage', None)
    settle(run, race, day)
    state['scores'][race]['format_fallback'] = state['submissions'][race]['format_fallback']
    # The released result read is part of the original research accounting.
    state['phase'] = 'reflect'
    run.service.call('results', {'race_id': race})
    state['phase'] = 'completed'
    replay_economic(run.config, state, source=run.source)
    runtime.verify_semantics(state)
    row = extract(state, policy, release(day))
    return make_frame([row], [], day, policy, release(day), [race])


@pytest.mark.parametrize(
    'mode,delivery,request_count',
    [
        ('direct', 'direct_predictor', 1),
        ('repair', 'repaired_predictor', 2),
        ('fallback', 'execution_fallback', 2),
    ],
)
def test_public_core_predict_release_replay_and_frame(memory_run, policy, monkeypatch, mode, delivery, request_count):
    (race, day), requests, snapshots = predict_mocked(memory_run, policy, monkeypatch, mode)
    state = memory_run.state
    assert len(requests) == request_count
    assert len(set(snapshots)) == 1  # Repair never reacquires or charges evidence again.
    assert state['aligned_delivery'][race]['delivery'] == delivery
    assert state['policy_execution']['success'] and state['policy_execution']['llm_calls'] == 0
    assert state['acquisition_complete'] and not state['scores'] and not state['release_ledger']
    assert runtime.verify_semantics(state)['semantic_json_equality'] == 'PASS'
    assert replay_acquisition(state)['control_view'] == 'PASS'
    assert evidence_lifecycle(state)['stage'] == 'sealed'
    accounting = audit_owned_cache(state, complete=True)
    assert accounting['logical_actions'] == 11
    assert accounting['native_acquisition_operations'] == 5
    assert accounting['cache_only_actions'] == 6
    history = [e for e in state['active_trace'] if e['action'].get('domain') == 'recent_history']
    cells = [e['newly_acquired_cells'] for e in history]
    assert cells[0] > 0 and cells == [cells[0], 2 * cells[0], 2 * cells[0], 0]
    before = digest(state)
    replay_economic(memory_run.config, state, source=memory_run.source)
    assert digest(state) == before
    with pytest.raises(Rejected):
        extract(state, policy, cutoff(day))
    frame = released_frame(memory_run, policy)
    assert frame['acquisition']['logical_policy_actions'] == 11
    assert frame['acquisition']['native_acquisition_operations'] == 5
    assert frame['acquisition']['cache_only_actions'] == 6
    assert frame['frame_digest'] == digest({k: v for k, v in frame.items() if k != 'frame_digest'})
    assert 'winners' not in json.dumps(frame) and 'scoring_outcome' not in json.dumps(frame)
    assert reconcile(state)['cost_micro'] > 0


@pytest.mark.parametrize('damage', ['provenance_clock', 'invoice', 'cursor_proof'])
def test_acquisition_audit_rejects_tampering(memory_run, policy, monkeypatch, damage):
    predict_mocked(memory_run, policy, monkeypatch)
    state = copy.deepcopy(memory_run.state)
    if damage == 'provenance_clock':
        state['semantic_provenance'][0]['clock'] += 1
    elif damage == 'invoice':
        state['active_trace'][2]['cost']['total_micro'] += 1
    else:
        proof = state['marginal_acquisition']['cursor_rebindings'][0]
        proof['to_arguments']['number'] = '02'
        seal_cache(proof)
        with pytest.raises(Rejected):
            replay_economic(memory_run.config, state, source=memory_run.source)
        return
    with pytest.raises(Rejected):
        audit_owned_cache(state, complete=True)


def test_committed_acquisition_replay_never_buys_again(memory_run, monkeypatch):
    run = memory_run
    state = run.state
    race = state['schedule'][0]['races'][0]
    raw = run.service.call('card', {'race_id': race})
    state.update(active_trace=[], semantic_provenance=[], query_count=0, active_initial={'card': basic_card(raw), 'receipt': raw['_cost']})
    acquisition = MarginalAcquisition(run)
    acquisition.call('recent_history', '01', 1, logical_id='policy-1')
    ledger, calls = copy.deepcopy(state['ledger']), len(state['audit'])

    def rebuy(*args, **kwargs):
        pytest.fail('A committed acquisition must replay from its owned cache')

    monkeypatch.setattr(run.service, 'call', rebuy)
    EvidenceSDK(run).call('recent_history', horse_number='01', depth=1)
    assert state['ledger'] == ledger and len(state['audit']) == calls
    assert audit_owned_cache(state, complete=True)['logical_actions'] == 1


def test_control_view_carries_quality_without_predictive_values():
    card = synthetic_cards()[1]
    action = {'domain': 'recent_history', 'horse_number': '01', 'depth': 1}
    observation = {
        'starts': [
            {
                'day_index': -1,
                'race_facts': {'surface': 1, 'distance': 1600},
                'runner_facts': {'earned_prize': 99999},
                'values': {'finish': 1, 'corner4_rank': 1, 'last3f_tenths_sec': 350},
            }
        ]
    }
    original = control.quality(action, observation, {'next_cursor': 'opaque'}, card)
    observation['starts'][0]['values'].update(finish=5, corner4_rank=9, last3f_tenths_sec=900)
    observation['starts'][0]['runner_facts']['earned_prize'] = 1
    assert original == control.quality(action, observation, {'next_cursor': 'opaque'}, card)
    assert set(original) == set(control.DOMAIN_FIELDS)
    assert set(control.context(card)['runners'][0]) == {'number'}


@pytest.mark.parametrize(
    'body',
    [
        'import os',
        'x = sdk.run',
        'x = context["runners"][0]["facts"]',
        'sdk.call("results")',
        'sdk.call("recent_history", horse_number="01", depth=2)',
        'while True:\n        pass',
    ],
)
def test_acquisition_interpreter_rejects_unavailable_capabilities(body):
    source = 'def collect_evidence(sdk, context):\n    sdk.call("track_context")\n    ' + body + '\n    return sdk.package()'
    with pytest.raises((PolicyInvalid, KeyError)):
        validate_source(source)


def scripted_updater(actions):
    actions = iter(actions)

    def call(store, settings, request, phase):
        name, args = next(actions)
        message = {
            'role': 'assistant',
            'tool_calls': [{'id': 'synthetic-' + phase, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}],
        }
        rid = digest([store.state['run_id'], phase, request])
        store.state['llm_trajectory'].append(
            {
                'phase': phase,
                'request_id': rid,
                'request': copy.deepcopy(request),
                'request_body_bytes': len(packed(request)),
                'logical_request_elapsed_seconds': 0,
            }
        )
        store.state['provider_attempts'].append({'phase': phase, 'request_id': rid, 'status': 'complete'})
        store.state['model_outputs'].append(
            {
                'phase': phase,
                'request_id': rid,
                'message': message,
                'prompt_tokens': 1,
                'completion_tokens': 1,
                'cost_usd': 0,
                'response_model': settings['model'],
            }
        )
        store.persist()
        return message

    return call


@pytest.mark.parametrize('decision', ['change', 'keep', 'invalid'])
def test_updater_accepts_only_released_frame_and_validated_policy(memory_run, policy, monkeypatch, tmp_path, decision):
    predict_mocked(memory_run, policy, monkeypatch)
    frame = released_frame(memory_run, policy)
    with pytest.raises(Rejected):
        context(policy, frame, cutoff(frame['day']), tmp_path)
    actions = [('inspect_contract', {})]
    if decision == 'change':
        actions += [('validate_research_policy', {'source': SHORT_CODE}), ('save_research_policy', {'source': SHORT_CODE})]
    elif decision == 'invalid':
        actions += [('validate_research_policy', {'source': 'import os'})]
    actions.append(('finish', {}))
    chosen, session = construct(
        tmp_path, SETTINGS, Registry(tmp_path / 'updates'), policy, frame, release(frame['day']), scripted_updater(actions)
    )
    assert lifecycle_checkpoint(session)['passed']
    assert session['saved'] is (decision == 'change')
    assert chosen['source'] == (SHORT_CODE if decision == 'change' else policy['source'])
    payload = json.loads(session['llm_trajectory'][0]['request']['messages'][1]['content'])
    assert all(key not in json.dumps(payload) for key in ('race_id', 'scoring_outcome', 'provenance', 'trigger_states'))
    assert session['updater_context_source_audit']['current_frame_digest'] == frame['frame_digest']


def test_frozen_coordinator_keeps_exact_initial_policy_without_updater(policy):
    schedule = [{'day': day, 'races': ['synthetic-' + day]} for day in ('2024-01-06', '2024-01-07')]
    seen = []

    def one(day, race, current):
        assert current == policy
        seen.append(current['policy_hash'])
        current['source'] = 'local mutation must not change shared P0'
        return race

    def release_day(day, sealed, current):
        assert current == policy
        return [{'day': day}]

    def feedback(rows, history, day, current, clock, races):
        assert current == policy and clock == release(day)
        return {'day': day, 'release_clock': clock, 'frame_digest': digest(day)}

    def forbidden(*args):
        pytest.fail('Frozen must never run an updater')

    initial = copy.deepcopy(policy)
    final, history = coordinate(schedule, policy, 'frozen_research_program', one, release_day, feedback, forbidden, lambda *a, **k: None)
    assert final == policy == initial and seen == [policy['policy_hash']] * 2 and len(history) == 2
    assert revise('frozen_research_program', policy, {}, None, None, SETTINGS, None, forbidden) == (policy, None)

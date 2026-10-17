"""The model loop and its transport: settings, retries, cost monitoring and context limits."""

import io
import json
from dataclasses import replace

import pytest

from jockeybench.agents.compatible import CompatibleAgent
from jockeybench.agents.provider import cost_exposure, request_reservation, require_credential
from jockeybench.canonical import packed
from jockeybench.errors import BudgetExceeded, Pending, ProviderCostLimit, Rejected


def agent(**settings):
    return CompatibleAgent({'model': 'unit', 'base_url': 'https://example.invalid', **settings})


def reply(message=None, cost=0.01, **extra):
    return io.BytesIO(
        json.dumps(
            {'choices': [{'message': message or {'role': 'assistant', 'content': 'done'}}], 'usage': {'estimated_cost': cost, **extra}}
        ).encode()
    )


def tool_call(ident, name, arguments):
    return {
        'role': 'assistant',
        'content': None,
        'tool_calls': [{'id': ident, 'type': 'function', 'function': {'name': name, 'arguments': arguments}}],
    }


@pytest.fixture
def reflecting(service):
    service.state['phase'] = 'reflect'
    return service


def reflect(a, service, day=0):
    service.state['index'] = day
    service.state.pop('model_context', None)
    a.reflect(service, {'day': day})


@pytest.mark.parametrize(
    'settings,message',
    [
        ({'base_url': 'https://' + 'user:pw@example.invalid'}, 'without credentials'),
        ({'unknown': 1}, 'Unknown provider setting'),
        ({'timeout_seconds': 0}, 'timeout'),
        ({'retry_attempts': 11}, 'retry_attempts'),
        ({'retry_max_seconds': 301}, 'Retry delays'),
        ({'input_usd_per_million': -1}, 'token rates'),
        ({'max_estimated_cost_usd': 0}, 'threshold'),
        ({'reservation_output_tokens': True}, 'reservation_output_tokens'),
        ({'output_token_field': 'tokens'}, 'output token'),
    ],
)
def test_invalid_provider_settings_are_refused(settings, message):
    with pytest.raises(Rejected, match=message):
        agent(**settings)


def test_identity_records_settings_but_not_the_credential_variable():
    identity = agent(api_key_env='MY_KEY', temperature=0).identity
    assert 'api_key_env' not in identity and identity['temperature'] == 0 and identity['kind'] == 'compatible'


def test_credential_comes_only_from_the_named_environment_variable(monkeypatch):
    monkeypatch.delenv('MY_KEY', raising=False)
    with pytest.raises(Rejected, match='unavailable'):
        require_credential({'api_key_env': 'MY_KEY'})
    monkeypatch.setenv('MY_KEY', 'secret')
    require_credential({'api_key_env': 'MY_KEY'})


def test_model_calls_share_one_budget_per_phase(reflecting, monkeypatch):
    reflecting.config = replace(reflecting.config, limits=replace(reflecting.config.limits, batch_model_calls=7, repair_model_calls=5))
    sent = []
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **k: sent.append(1) or reply())
    a = agent()
    reflect(a, reflecting, 0)
    reflect(a, reflecting, 1)  # 7 calls minus the 5 reserved for repair leaves 2
    with pytest.raises(BudgetExceeded, match='Provider call budget'):
        reflect(a, reflecting, 2)
    assert len(sent) == reflecting.state['usage']['model_calls'] == 2


def test_network_failures_are_retried_and_never_consume_a_model_call(reflecting, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError('timeout')

    monkeypatch.setattr('urllib.request.urlopen', fail)
    monkeypatch.setattr('time.sleep', lambda seconds: None)
    with pytest.raises(Pending):
        agent(retry_attempts=3, retry_base_seconds=0).reflect(reflecting, {})
    attempts = reflecting.state['provider_attempts']
    assert len(attempts) == 3 and len({a['request_id'] for a in attempts}) == 1
    assert reflecting.state['usage']['model_calls'] == 0 and reflecting.state['provider_failures'] == 3
    assert reflecting.state['pending']['status'] == 'network_pending'


@pytest.mark.parametrize('body', ['{"choices":null}', '{"choices":[],"usage":{"estimated_cost":NaN}}'])
def test_an_invalid_response_is_recorded_and_its_cost_treated_as_unknown(reflecting, monkeypatch, body):
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **k: io.BytesIO(body.encode()))
    a = agent(max_estimated_cost_usd=1)
    with pytest.raises(Pending):
        a.reflect(reflecting, {})
    attempt = reflecting.state['provider_attempts'][0]
    assert attempt['response_discarded'] and attempt['response_bytes'] == len(body) and attempt['cost_usd'] is None
    with pytest.raises(ProviderCostLimit, match='unknown unreserved cost'):
        a.reflect(reflecting, {})


def test_cost_monitor_stops_before_the_threshold_is_crossed(reflecting, monkeypatch):
    sent = []
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **k: sent.append(1) or reply(cost=0.01))
    a = agent(max_estimated_cost_usd=0.01)
    reflect(a, reflecting, 0)
    with pytest.raises(ProviderCostLimit, match='cost monitoring'):
        reflect(a, reflecting, 1)
    assert len(sent) == 1 and reflecting.state['funding_stop']['threshold_usd'] == 0.01


@pytest.mark.parametrize(
    'usage,cost,basis',
    [
        ({'prompt_tokens': 100, 'completion_tokens': 300}, 0.00006, 'configured_uncached_rate_estimate'),
        ({'prompt_tokens': 100, 'completion_tokens': 300, 'estimated_cost': 0.001}, 0.001, 'provider_reported_estimate'),
        ({'prompt_tokens': 100}, None, 'unavailable'),
    ],
)
def test_cost_estimates_prefer_the_provider_and_never_price_missing_usage_at_zero(reflecting, monkeypatch, usage, cost, basis):
    raw = {'choices': [{'message': {'role': 'assistant', 'content': 'done'}}], 'usage': usage}
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **k: io.BytesIO(json.dumps(raw).encode()))
    agent(input_usd_per_million=0.06, output_usd_per_million=0.18, reservation_output_tokens=1000).reflect(reflecting, {})
    output = reflecting.state['model_outputs'][0]
    assert output['cost_basis'] == basis and (output['cost_usd'] == pytest.approx(cost) if cost else output['cost_usd'] is None)
    assert cost_exposure(reflecting.state)[0] > 0


def test_null_output_tokens_are_forwarded_and_reserved_with_the_documented_bound(reflecting, monkeypatch):
    sent = []
    monkeypatch.setattr('urllib.request.urlopen', lambda request, **k: sent.append(json.loads(request.data)) or reply())
    agent(
        output_token_field='max_completion_tokens',
        input_usd_per_million=0.06,
        output_usd_per_million=0.18,
        reservation_output_tokens=65536,
        max_estimated_cost_usd=2,
    ).reflect(reflecting, {})
    assert sent[0]['max_completion_tokens'] is None and 'reservation_output_tokens' not in sent[0]
    assert reflecting.state['provider_attempts'][0]['reserved_cost_usd'] > 65536 * 0.18 / 1e6
    assert (
        request_reservation(
            {'output_token_field': 'max_tokens', 'input_usd_per_million': 1, 'output_usd_per_million': 1}, {'max_tokens': None}
        )
        is None
    )


def test_malformed_tool_arguments_still_cost_a_tool_call(reflecting, monkeypatch):
    replies = [reply(tool_call('bad', 'history', '{"limit":')), reply()]
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **k: replies.pop(0))
    agent().reflect(reflecting, {})
    assert reflecting.state['usage']['calls'] == 1 and reflecting.state['audit'][0]['status'] == 'rejected'
    error = json.loads(reflecting.state['model_context']['messages'][-2]['content'])['error']
    assert error['code'] == 'Rejected' and 'bounded finite JSON' in error['message']


def test_context_trimming_drops_whole_old_rounds_and_reserves_room_for_replies(reflecting, monkeypatch):
    reflecting.config = replace(reflecting.config, limits=replace(reflecting.config.limits, context_bytes=200000, batch_model_calls=30))
    bodies = []

    def provider(request, **kwargs):
        bodies.append(json.loads(request.data))
        count = len(bodies)
        return reply(tool_call(str(count), 'card', json.dumps({'race_id': 'unit'})) if count < 9 else None)

    original = reflecting.dispatch
    monkeypatch.setattr(
        reflecting, 'dispatch', lambda name, args, meter=None: {'payload': 'x' * 30000} if name == 'card' else original(name, args, meter)
    )
    monkeypatch.setattr('urllib.request.urlopen', provider)
    agent().reflect(reflecting, {})
    assert all(row['status'] == 'ok' for row in reflecting.state['audit'])
    assert all(len(packed(b)) <= reflecting.config.limits.context_bytes for b in bodies)
    assert reflecting.state['model_context']['discarded_rounds'] > 0
    for body in bodies:
        calls = {t['id'] for m in body['messages'] for t in (m.get('tool_calls') or [])}
        assert calls == {m['tool_call_id'] for m in body['messages'] if m['role'] == 'tool'}
    assert 'Earlier completed tool rounds may be removed' in bodies[-1]['messages'][0]['content']

"""Portable acquisition warmup on fabricated in-memory archives, without providers."""

import copy
import gzip
import json
from dataclasses import replace

import pytest

from conftest import make_source, native
from jockeybench.clock import cutoff
from jockeybench.data.connections import ConnectionsIndex
from jockeybench.errors import Rejected
from jockeybench.research import portable_warmup
from jockeybench.research.finegrained_v3.policy_contract import PolicyInvalid
from jockeybench.runner import Run

CODE = 'def collect_evidence(sdk, context):\n    sdk.call("track_context")\n    return sdk.package()\n'
SETTINGS = {'model': 'synthetic-provider', 'base_url': 'https://invalid.example/v1'}


def scripted(actions):
    def call(service, settings, body, phase):
        index = len(service.state.get('llm_trajectory', []))
        name, args = actions(index, service)
        message = {
            'role': 'assistant',
            'content': '',
            'tool_calls': [{'id': str(index), 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}],
        }
        service.state.setdefault('llm_trajectory', []).append(
            {'phase': phase, 'request': copy.deepcopy(body), 'logical_request_elapsed_seconds': 1}
        )
        service.state.setdefault('model_outputs', []).append(
            {'request_id': str(index), 'cost_usd': 0.001, 'prompt_tokens': 10, 'completion_tokens': 5, 'message': message}
        )
        service.state.setdefault('provider_attempts', []).append({'status': 'complete', 'request_id': str(index)})
        return message

    return call


def test_support_hides_formal_future_and_unreviewed_rows_from_broad_scans(source):
    # A planted pre-window race is not legal support until its review is public.
    source.con.execute("UPDATE races SET review_at=? WHERE id='raw1'", (cutoff('2024-01-06') + 1,))
    source.con.commit()
    formal_id = source.token('race', 'raw2')
    support = portable_warmup.HistoricalSource.restrict(source, portable_warmup.Boundary('2024-01-01', '2024-01-06'))
    assert [r[0] for r in support.con.execute('SELECT id FROM races')] == ['raw0']
    assert support.con.execute('SELECT COUNT(*) FROM entries').fetchone()[0] == 5
    assert support.con.execute('SELECT COUNT(*) FROM expanded_race_ids').fetchone()[0] == 1
    assert support.con.execute('PRAGMA query_only').fetchone()[0] == 1
    assert support.con.execute('SELECT COUNT(*) FROM main.races').fetchone()[0] == 8
    index = ConnectionsIndex(support)
    assert all(event[1] < '2024-01-01' for events in index.rows.values() for event in events)
    with pytest.raises(Rejected, match='Unknown race'):
        support.card(formal_id, support.boundary.clock, '2024-01-01')


def test_support_enforces_clock_origin_and_negative_filters(source):
    boundary = portable_warmup.Boundary('2024-01-01', '2024-01-06')
    support = portable_warmup.HistoricalSource.restrict(source, boundary)
    page, _ = support.history_page(boundary.clock, boundary.origin, None, 8)
    assert len(page['examples']) == 2
    assert all(e['card']['day_index'] < 0 and e['labels'] for e in page['examples'])
    for clock, origin, filters in [
        (boundary.clock + 1, boundary.origin, None),
        (boundary.clock, '2024-01-02', None),
        (boundary.clock, boundary.origin, {'day_max': 0}),
    ]:
        with pytest.raises(Rejected):
            support.history_page(clock, origin, None, 8, filters=filters)
    support.con.set_trace_callback(lambda sql: pytest.fail('Schedule must not query archive: ' + sql))
    assert support.schedule() == [{'day': '2024-01-06', 'races': []}]
    support.con.set_trace_callback(None)


def test_public_builder_saves_policy_with_complete_cost_and_distinct_lineage(config, tmp_path, monkeypatch):
    monkeypatch.setattr(Run, 'preflight', lambda self: self.state.update(isolation={'synthetic_test': True}))
    out = tmp_path / 'public-warmup'
    steps = [
        ('inspect_contract', {}),
        ('history', {}),
        ('validate_research_policy', {'source': CODE}),
        ('save_research_policy', {'source': CODE}),
        ('finish', {}),
    ]
    policy = portable_warmup.build(
        out, config, SETTINGS, source_factory=lambda *args: make_source(), caller=scripted(lambda i, s: steps[i])
    )
    assert policy['source'] == CODE and policy['parent_policy_hash'] is None
    lineage = json.loads((out / 'warmup-lineage.json').read_text())
    assert lineage['warmup_version'] == portable_warmup.VERSION
    assert lineage['support_boundary']['origin'] == config.start
    assert 'archive_code_hash' not in lineage
    accounting = json.loads((out / 'warmup-accounting.json').read_text())
    assert accounting['logical_requests'] == 5 and accounting['api_cost_usd_estimate'] == pytest.approx(0.005)
    assert accounting['weighted_data_tool_cost'] > 0
    assert accounting['ledger_reconciled'] and accounting['compute_settled']
    assert policy['model_usage']['logical_requests'] == 4
    assert accounting['builder_session_usage']['logical_requests'] == 5
    checkpoint = json.loads(gzip.decompress((out / 'warmup-runs/historical-support/checkpoint.json.gz').read_bytes()))
    assert checkpoint['clock'] == cutoff('2024-01-06')
    assert checkpoint['schedule'] == [{'day': '2024-01-06', 'races': []}]
    assert checkpoint['warmup_actions'][1]['reply'] == {'recorded_in_tool_audit': True}
    with pytest.raises(PolicyInvalid, match='No warmup resume'):
        portable_warmup.build(out, config, SETTINGS, source_factory=lambda *args: make_source(), caller=scripted(lambda i, s: steps[i]))


def test_public_builder_never_falls_back_to_a_handwritten_policy(config, tmp_path, monkeypatch):
    monkeypatch.setattr(Run, 'preflight', lambda self: None)
    config = replace(config, limits=replace(config.limits, warmup_model_calls=2, repair_model_calls=1))
    out = tmp_path / 'no-policy'
    with pytest.raises(PolicyInvalid, match='Warmup failed'):
        portable_warmup.build(
            out, config, SETTINGS, source_factory=lambda *args: make_source(), caller=scripted(lambda i, s: ('inspect_contract', {}))
        )
    assert not list((out / 'policies').glob('*.py'))
    assert json.loads((out / 'warmup-accounting.json').read_text())['logical_requests'] == 2


@pytest.mark.native
@native
def test_public_warmup_compute_uses_isolated_shared_runtime(config, tmp_path):
    steps = [
        ('inspect_contract', {}),
        ('compute', {'code': 'result = len(sdk.call("history", limit=1)["examples"])'}),
        ('validate_research_policy', {'source': CODE}),
        ('save_research_policy', {'source': CODE}),
        ('finish', {}),
    ]
    out = tmp_path / 'native-warmup'
    portable_warmup.build(out, config, SETTINGS, source_factory=lambda *args: make_source(), caller=scripted(lambda i, s: steps[i]))
    checkpoint = json.loads(gzip.decompress((out / 'warmup-runs/historical-support/checkpoint.json.gz').read_bytes()))
    assert checkpoint['isolation']['network_denied'] and checkpoint['isolation']['subprocess_denied']
    assert any(row['tool'] == 'history' and row['status'] == 'ok' for row in checkpoint['audit'])
    assert any(row['tool'] == 'compute' and row['status'] == 'ok' for row in checkpoint['audit'])
    assert json.loads((out / 'warmup-accounting.json').read_text())['weighted_compute_cost'] > 0

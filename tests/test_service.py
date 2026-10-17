"""The tool broker: metering, prices, allowances, cursors and crash idempotence."""

import copy
from dataclasses import replace

import pytest

from jockeybench.canonical import digest
from jockeybench.config import Tariff
from jockeybench.errors import BudgetLimit, Rejected
from jockeybench.ledger import ledger, reconcile


def race(service, raw='raw2'):
    return service.source.token('race', raw)


def test_cursors_bind_run_clock_and_query(service):
    one = service.call('history', {'limit': 1, 'scope': 'support'})
    two = service.call('history', {'limit': 1, 'scope': 'support', 'cursor': one['next_cursor']})
    assert one['examples'][0] != two['examples'][0] and two['next_cursor'] is None
    service.state['clock'] += 1
    with pytest.raises(Rejected, match='time and query'):
        service.call('history', {'limit': 1, 'scope': 'support', 'cursor': one['next_cursor']})
    service.state['clock'] -= 1
    with pytest.raises(Rejected):
        service.call('history', {'limit': 2, 'scope': 'support', 'cursor': one['next_cursor']})
    service.state['run_id'] = 'other'
    with pytest.raises(Rejected):
        service.call('history', {'limit': 1, 'scope': 'support', 'cursor': one['next_cursor']})


@pytest.mark.parametrize(
    'name,args,message',
    [
        ('nope', {}, 'Unknown tool'),
        ('history', {'sql': 'select 1'}, 'Unknown tool or unauthorized'),
        ('card', {}, 'Missing required'),
        ('history', {'limit': 1000}, 'page size'),
        ('history', [], 'bounded finite JSON'),
        ('card', {'race_id': 'x', 'max_spend': -1}, 'max_spend must be'),
        ('statistics', {'group_by': 'finish'}, 'group_by'),
        ('submit', {'race_id': 'x', 'prediction': {}}, 'cannot submit'),
    ],
)
def test_invalid_requests_are_rejected_and_still_consume_a_call(service, name, args, message):
    with pytest.raises(Rejected, match=message):
        service.call(name, args)
    assert service.state['usage']['calls'] == 1
    assert service.state['audit'][-1]['status'] == 'rejected'


def test_rejected_data_request_pays_only_its_call_fee(service):
    with pytest.raises(Rejected):
        service.call('card', {'race_id': 'race_' + '0' * 24})
    assert ledger(service.state)['cost_micro'] == 250000  # 0.5 score-yen x multiplier 0.5
    with pytest.raises(Rejected):
        service.call('describe', {'unexpected': 1})
    assert ledger(service.state)['cost_micro'] == 250000  # control tools are free


def test_evidence_is_metered_and_identical_rereads_are_free(service):
    card = service.call('card', {'race_id': race(service)})
    cost = card['_cost']
    assert cost['evidence_cells'] > 100 and cost['charged_cells'] == cost['evidence_cells'] and not cost['repeat']
    assert service.state['usage']['evidence_cells'] == cost['evidence_cells']
    again = service.call('card', {'race_id': race(service)})['_cost']
    assert again['repeat'] and again['charged_cells'] == again['data_micro'] == 0 and again['call_micro'] == cost['call_micro']
    assert service.state['usage']['evidence_cells'] == cost['evidence_cells']
    assert sum(r['_cost']['total_micro'] for r in service.state['audit']) == reconcile(service.state)['cost_micro']


def test_statistics_and_coverage_are_charged_for_the_evidence_they_inspect(service):
    stats = service.call('statistics', {'group_by': 'sex', 'limit': 2})
    assert stats['_cost']['evidence_cells'] == 20  # 2 support races x 5 runners x (sex + finish)
    coverage = service.call('coverage', {'scope': 'support', 'limit': 2})
    assert coverage['_cost']['evidence_cells'] == sum(v['total'] for v in coverage['fields'].values())


def test_splitting_history_pages_does_not_discount_evidence(service):
    both = service.call('history', {'scope': 'support', 'limit': 2})
    one = service.call('history', {'scope': 'support', 'limit': 1})
    two = service.call('history', {'scope': 'support', 'limit': 1, 'cursor': one['next_cursor']})
    assert both['_cost']['evidence_cells'] == one['_cost']['evidence_cells'] + two['_cost']['evidence_cells']


def test_spend_limits_and_undeliverable_responses_charge_no_data(service):
    with pytest.raises(Rejected, match='max_spend'):
        service.call('card', {'race_id': race(service), 'max_spend': 0.01})
    assert ledger(service.state)['cost_micro'] == 250000 and service.state['usage']['evidence_cells'] == 0
    service.observation_limit = 2200
    with pytest.raises(Rejected, match='Undeliverable'):
        service.call('card', {'race_id': race(service)})
    assert ledger(service.state)['cost_micro'] == 250000 and service.state['audit'][-1]['_cost']['total_micro'] == 0


def test_tariff_multiplier_scales_every_fee(service):
    first = service.call('card', {'race_id': race(service)})['_cost']
    service.config = replace(service.config, tariff=Tariff(multiplier=2))
    second = service.call('card', {'race_id': race(service, 'raw3')})['_cost']
    assert second['call_micro'] == 4 * first['call_micro']
    assert second['data_micro'] == 4 * first['data_micro'] * second['charged_cells'] // first['charged_cells']


def test_data_allowances_reject_recoverably(service):
    service.config = replace(service.config, limits=replace(service.config.limits, batch_evidence_cells=10))
    with pytest.raises(BudgetLimit, match='evidence_cells limit reached'):
        service.call('card', {'race_id': race(service)})
    assert service.state['limit_events'][-1]['recoverable'] and service.state['usage']['evidence_cells'] == 0
    assert service.call('status')['usage']['evidence_cells'] == 0  # control tools keep working


def test_completed_operation_is_replayed_and_verified_not_recharged(service):
    first = service.call('card', {'race_id': race(service)}, operation_id='read-once')
    usage = copy.deepcopy(service.state['usage'])
    assert service.call('card', {'race_id': race(service)}, operation_id='read-once') == first
    assert service.state['usage'] == usage
    with pytest.raises(Rejected, match='identity conflict'):
        service.call('card', {'race_id': race(service, 'raw3')}, operation_id='read-once')


def test_interrupted_read_resumes_without_a_second_call_charge(service):
    args = {'race_id': race(service)}
    service.state['usage']['calls'] = 1  # charged by the durable pre-dispatch checkpoint
    service.state['economic_operations'] = {'read': {'key': digest(['card', args, service.state['clock'], None]), 'status': 'pending'}}
    value = service.call('card', args, operation_id='read')
    assert service.state['usage']['calls'] == 1
    assert service.state['usage']['evidence_cells'] == value['_cost']['evidence_cells'] > 0


def test_interrupted_mutation_is_rolled_back_not_repeated(service):
    restored = []
    service.executor = type('Executor', (), {'restore': lambda self, ident: restored.append(ident)})()
    args = {'files': {'NOTES.md': 'x'}}
    service.state['economic_operations'] = {
        'save': {
            'key': digest(['save_strategy', args, service.state['clock'], None]),
            'status': 'pending',
            'restore_digest': 'before',
            'cpu_before': 0,
        }
    }
    with pytest.raises(Rejected, match='rolled back without re-execution'):
        service.call('save_strategy', args, operation_id='save')
    assert restored == ['before'] and service.state['recovered_mutations'] == [{'operation': 'save', 'tool': 'save_strategy'}]


def test_status_and_ledger_report_allowances_without_machine_limits(service):
    status = service.call('status')
    assert status['ledger']['currency'] == 'score_yen_not_money' and status['ledger']['monetary_cap'] is None
    assert 'rss_bytes' not in status['limits'] and status['limits']['max_page_size'] == 2
    assert status['_budget']['tool_calls'] == service.config.limits.batch_calls - 1


def test_static_arm_cannot_change_its_program_after_warmup(service):
    service.config = replace(service.config, arm='S')
    with pytest.raises(Rejected, match='frozen'):
        service.call('save_strategy', {'mode': 'uniform'})

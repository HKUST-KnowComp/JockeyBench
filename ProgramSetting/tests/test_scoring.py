import math

import pytest

from jockeybench.config import Tariff
from jockeybench.errors import Rejected
from jockeybench.ledger import book, ledger, reconcile
from jockeybench.protocol.specification import EXAMPLE_LOG_REVENUE
from jockeybench.scoring import outcome, score, sealed_revenue, summarize, uniform, validate

TARIFF = Tariff()


def rows(n=5, **overrides):
    result = {
        f'{i:02}': {'finish': str(i), 'abnormal': '0', 'result_present': True, 'win_payout_yen': 200 if i == 1 else 0}
        for i in range(1, n + 1)
    }
    for number, values in overrides.items():
        result[number].update(values)
    return result


@pytest.mark.parametrize(
    'bad',
    [
        None,
        [],
        {'ranking': ['01', '01']},
        {'ranking': [1, 2]},
        {'ranking': ['01', '02'], 'extra': 1},
        {'ranking': ['01', '02'], 'probabilities': {'01': float('nan'), '02': 0}},
        {'ranking': ['01', '02'], 'probabilities': {'01': 2, '02': -1}},
        {'ranking': ['01', '02'], 'probabilities': {'01': 0.5}},
        {'ranking': ['01', '02'], 'probabilities': {'01': 0.6, '02': 0.6}},
    ],
)
def test_invalid_predictions_are_rejected(bad):
    with pytest.raises(Rejected):
        validate(bad, ['01', '02'])


def test_rounding_within_tolerance_is_normalised():
    exact = validate({'ranking': ['01', '02'], 'probabilities': {'01': 0.5, '02': 0.5}}, ['01', '02'])
    assert exact['probabilities'] == {'01': 0.5, '02': 0.5}
    rounded = validate({'ranking': ['01', '02'], 'probabilities': {'01': 0.51, '02': 0.5}}, ['01', '02'])
    assert sum(rounded['probabilities'].values()) == pytest.approx(1)
    with pytest.raises(Rejected, match='mandatory|requires probabilities'):
        validate({'ranking': ['01', '02']}, ['01', '02'])


def test_uniform_forecast_scores_exactly_zero_log_revenue():
    value = score(uniform('race', list(rows()), 0), rows(), TARIFF)
    assert value['status'] == 'ok' and value['revenue_log_yen'] == pytest.approx(0)


def test_worked_example_of_the_task_specification():
    entrants = [f'{i:02}' for i in range(1, 15)]
    probabilities = {n: 0.10 if n == '01' else 0.90 / 13 for n in entrants}
    value = score({'ranking': entrants, 'probabilities': probabilities}, rows(14), TARIFF, delivery='predictor')
    assert value['revenue_log_yen'] == pytest.approx(EXAMPLE_LOG_REVENUE) == pytest.approx(100 * math.log(14 * (0.98 * 0.1 + 0.02 / 14)))
    assert value['revenue_hit_yen'] == pytest.approx(100 * (1 - 1 / 14))  # +92.9 for a hit at n=14


@pytest.mark.parametrize('delivery,bonus', [('predictor', 80), ('silent_uniform', 0), ('execution_fallback', 0), ('explicit_uniform', 0)])
def test_hit_bonus_requires_delivery_by_a_validated_program(delivery, bonus):
    value = score(uniform('race', list(rows()), 0) | {'ranking': list(rows())}, rows(), TARIFF, delivery=delivery)
    assert value['revenue_hit_yen'] == pytest.approx(bonus)
    assert value['score_revenue_yen'] == pytest.approx(value['revenue_log_yen'] + bonus)


def test_scratches_dead_heats_and_nominal_return():
    settled = {
        '01': {'abnormal': '1'},
        '02': {'result_present': True, 'finish': '1', 'win_payout_yen': 160},
        '03': {'result_present': True, 'finish': '1', 'win_payout_yen': 120},
        '04': {'result_present': True, 'finish': '3'},
    }
    value = score({'ranking': list(settled), 'probabilities': dict.fromkeys(settled, 0.25)}, settled, TARIFF)
    assert outcome(settled)['winners'] == ['02', '03'] and value['scoring_outcome']['active'] == ['02', '03', '04']
    assert value['top1'] == 1 and value['top3_hits'] == 3 and value['all3'] == 1 and value['nominal_net_yen'] == 100
    assert value['revenue_log_yen'] == pytest.approx(0)  # uniform over the three starters, averaged over both winners
    assert summarize([value])['private_nominal_no_takeout_roi'] == 1


@pytest.mark.parametrize(
    'settled,status,refund',
    [
        ({'01': {'abnormal': '02'}}, 'no_valid_runners', 100),
        ({'01': {'cancelled': True}}, 'cancelled', 100),
        ({'01': {'finish': '1', 'result_present': False}}, 'missing_results', 0),
    ],
)
def test_unscored_races_book_no_revenue(settled, status, refund):
    value = score({'ranking': ['01'], 'probabilities': {'01': 1}}, settled, TARIFF)
    assert value['status'] == status and value['refund_yen'] == refund and value['score_revenue_yen'] is None
    assert summarize([value])['top1_accuracy'] is None


def test_uniform_fallback_is_deterministic_and_order_free():
    assert uniform('same', ['01', '02', '03'], 2) == uniform('same', ['03', '02', '01'], 2)
    assert {tuple(uniform(f'race{i}', ['01', '02', '03'], 2)['ranking']) for i in range(20)} != {('01', '02', '03')}


def test_sealed_revenue_recomputes_the_booked_score():
    prediction = {'ranking': ['03', '01', '02', '04', '05'], 'probabilities': {'01': 0.4, '02': 0.1, '03': 0.3, '04': 0.1, '05': 0.1}}
    value = score(prediction, rows(), TARIFF, delivery='predictor')
    assert sealed_revenue(prediction, value['scoring_outcome'], TARIFF, 'predictor') == {
        'revenue_log_yen': value['revenue_log_yen'],
        'revenue_hit_yen': value['revenue_hit_yen'],
    }


def test_ledger_entries_are_idempotent_and_reconciled():
    state = {'phase': 'predict', 'index': 0}
    book(state, 'a', 'calls', 5)
    assert book(state, 'a', 'calls', 5)['amount_micro'] == 5 and ledger(state)['cost_micro'] == 5
    with pytest.raises(Rejected, match='conflict'):
        book(state, 'a', 'calls', 6)
    book(state, 'r', 'revenue_log', 7)
    assert reconcile(state) == {'entries': 2, 'cost_micro': 5, 'revenue_micro': 7, 'reconciled': True}
    ledger(state)['cost_micro'] += 1
    with pytest.raises(Rejected, match='total mismatch'):
        reconcile(state)

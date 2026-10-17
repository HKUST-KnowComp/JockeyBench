"""Trusted evaluator: prediction validation and race scoring. Never part of agent observations.

A race's score-yen revenue is ``S * ln(n * pbar) + I(predictor) * B * (hit - 1/n)``
where ``pbar`` is the mean smoothed probability of the official winners, ``n``
the number of starters, ``S`` the score scale and ``B`` the hit bonus.
"""

import math
import random

from .canonical import digest
from .data.projection import number
from .errors import Rejected


def validate(prediction, entrants):
    """Check a prediction against the entrants and normalise rounding; probabilities are mandatory."""
    if not isinstance(prediction, dict) or set(prediction) - {'ranking', 'probabilities'}:
        raise Rejected('Prediction requires exactly ranking and probabilities')
    ranking = prediction.get('ranking')
    if not isinstance(ranking, list) or any(type(n) is not str for n in ranking):
        raise Rejected('Ranking must contain runner number strings')
    if len(ranking) != len(entrants) or set(ranking) != set(entrants):
        raise Rejected('Ranking must contain each entrant exactly once; expected: ' + ', '.join(sorted(entrants)))
    probs = prediction.get('probabilities')
    if probs is None:
        raise Rejected('Economic protocol requires probabilities for every entrant')
    if not isinstance(probs, dict) or set(probs) != set(entrants):
        raise Rejected('Probabilities must cover all entrants')
    if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 or v > 1 for v in probs.values()):
        raise Rejected('Invalid probability')
    total = sum(probs.values())
    if total <= 0 or not math.isclose(total, 1, abs_tol=0.02, rel_tol=0):
        raise Rejected('Probabilities must sum to one within 0.02 rounding tolerance')
    return {**prediction, 'probabilities': dict(probs) if abs(total - 1) < 1e-12 else {n: v / total for n, v in probs.items()}}


def uniform(race_id, entrants, seed):
    """Deterministic fallback: a seeded shuffle with uniform probabilities."""
    order = sorted(entrants)
    random.Random(digest([seed, race_id, 'shared-format-fallback-v1'])).shuffle(order)
    return {'ranking': order, 'probabilities': {n: 1 / len(entrants) for n in entrants}}


def outcome(rows):
    """Official result: scratches, winners (dead heats allowed) and the first three."""
    scratches = sorted(n for n, s in rows.items() if str(s.get('abnormal', '')).strip().lstrip('0') in {'1', '2'})
    active = {n: s for n, s in rows.items() if n not in scratches}
    if not active:
        return {'status': 'no_valid_runners', 'winners': [], 'top3': [], 'scratches': scratches}
    if all(s.get('cancelled') is True for s in active.values()):
        return {'status': 'cancelled', 'winners': [], 'top3': [], 'scratches': scratches}
    winners = sorted(n for n, s in active.items() if number(s.get('finish')) == 1)
    top = sorted(n for n, s in active.items() if number(s.get('finish')) in (1, 2, 3))
    valid = all(s.get('result_present') is True for s in active.values()) and bool(winners)
    return {
        'status': 'ok' if valid else 'missing_results',
        'winners': winners if valid else [],
        'top3': top if valid else [],
        'scratches': scratches,
    }


def hit_revenue(n, top1, bonus, delivery):
    return bonus * (int(top1) - 1 / n) if delivery == 'predictor' and bonus else 0.0


def revenue_components(value):
    return {'revenue_log_yen': value['revenue_log_yen'], 'revenue_hit_yen': value['revenue_hit_yen']}


def _smoothed(prediction, active, smoothing):
    n = len(active)
    mass = sum(prediction['probabilities'][i] for i in active)
    p = {i: prediction['probabilities'][i] / mass if mass else 1 / n for i in active}
    return {i: (1 - smoothing) * p[i] + smoothing / n for i in active}


def _log_gain(probabilities, winners):
    return math.log(len(probabilities) * sum(probabilities[i] for i in winners) / len(winners))


def sealed_revenue(prediction, fact, tariff, delivery):
    """Recompute both revenue parts from a sealed prediction and a booked outcome."""
    active = [i for i in prediction['ranking'] if i not in fact['scratches']]
    winners = fact['winners']
    if active != fact['active'] or not active or not winners or len(set(winners)) != len(winners) or not set(winners) <= set(active):
        raise Rejected('Invalid booked scoring outcome')
    log = tariff.score_scale * _log_gain(_smoothed(prediction, active, tariff.smoothing), winners)
    return {'revenue_log_yen': log, 'revenue_hit_yen': hit_revenue(len(active), active[0] in winners, tariff.hit_bonus_yen, delivery)}


def score(prediction, rows, tariff, *, delivery=None):
    """Score one sealed prediction against settled ``rows`` (number -> settlement)."""
    prediction = validate(prediction, list(rows))
    fact = outcome(rows)
    value = {'status': fact['status'], 'refund_yen': 0, 'stake_yen': 0, 'gross_yen': 0}
    if fact['status'] != 'ok':
        # A cancelled/no-runner event returns the fixed stake without takeout uplift.
        if fact['status'] in {'cancelled', 'no_valid_runners'}:
            value['refund_yen'] = 100
        return {**value, 'score_revenue_yen': None, 'revenue_log_yen': None, 'revenue_hit_yen': 0.0}

    # Diagnostics: hit rates, unsmoothed log loss and a nominal no-takeout return.
    order = [n for n in prediction['ranking'] if n not in fact['scratches']]
    chosen, winners = order[:3], fact['winners']
    hit = int(order[0] in winners)
    value.update(
        top1=hit,
        top3_hits=len(set(chosen) & set(fact['top3'])),
        top3_n=len(chosen),
        all3=int(len(chosen) == 3 and set(chosen) <= set(fact['top3'])),
        all3_eligible=int(len(chosen) == 3),
    )
    payouts = [number(rows[n].get('win_payout_yen')) for n in winners]
    if all(v is not None and v > 0 for v in payouts):
        gross = number(rows[order[0]].get('win_payout_yen')) if hit else 0
        value.update(stake_yen=100, gross_yen=gross, nominal_net_yen=gross / 0.8 - 100, roi_status='ok')
    else:
        value['roi_status'] = 'missing_payout'
    value['probabilities_provided'] = True
    probs = prediction['probabilities']
    total = sum(probs[n] for n in order)
    if total <= 0:
        # A degenerate distribution cannot selectively remove a bad race.
        probs, total = {n: 1 / len(order) for n in order}, 1
        value['probabilities_zero_mass'] = True
    value['log_loss'] = -math.log(max(sum(probs[n] for n in winners) / total, 1e-15))
    value['log_loss_gain_uniform'] = math.log(len(order) / len(winners)) - value['log_loss']
    value['calibration'] = [{'p': probs[n] / total, 'y': int(n in winners)} for n in order]

    # The scored quantity: smoothed log gain plus the delivery-gated hit bonus.
    smoothed = _smoothed(prediction, order, tariff.smoothing)
    gain = _log_gain(smoothed, winners)
    calibration = [{'n': 0, 'sum_probability': 0.0, 'sum_target': 0.0} for _ in range(10)]
    for i in order:
        bucket = calibration[min(9, int(smoothed[i] * 10))]
        bucket['n'] += 1
        bucket['sum_probability'] += smoothed[i]
        bucket['sum_target'] += int(i in winners) / len(winners)
    revenue_log = tariff.score_scale * gain
    revenue_hit = hit_revenue(len(order), hit, tariff.hit_bonus_yen, delivery)
    value.update(
        scoring_outcome={'active': order, 'winners': winners, 'scratches': fact['scratches']},
        smoothed_log_gain=gain,
        smoothed_brier=sum((smoothed[i] - int(i in winners) / len(winners)) ** 2 for i in order),
        smoothed_calibration=calibration,
        score_currency='score_yen_not_money',
        dead_heat_convention='log of average assigned probability over official winners',
        revenue_log_yen=revenue_log,
        revenue_hit_yen=revenue_hit,
        score_revenue_yen=revenue_log + revenue_hit,
    )

    # Private evaluator diagnostic against the closing market; never shown to agents.
    odds = {i: number(rows[i].get('final_odds')) for i in order}
    value['private_market_log_gain'] = None
    value['private_gain_over_market'] = None
    if all(v is not None and v > 0 for v in odds.values()):
        z = sum(1 / v for v in odds.values())
        market = {i: (1 - tariff.smoothing) * (1 / odds[i] / z) + tariff.smoothing / len(order) for i in order}
        value.update(private_market_log_gain=_log_gain(market, winners), private_gain_over_market=gain - _log_gain(market, winners))
    return value


def summarize(records):
    """Diagnostic hit rates, log loss and calibration over scored races."""
    valid = [r for r in records if r['status'] == 'ok']
    denom = sum(r['top3_n'] for r in valid)
    stakes = sum(r['stake_yen'] for r in records)
    bins = []
    for i in range(10):
        vals = [v for r in valid for v in r.get('calibration', []) if min(int(v['p'] * 10), 9) == i]
        bins.append(
            {
                'bin': i,
                'n': len(vals),
                'mean_p': sum(v['p'] for v in vals) / len(vals) if vals else None,
                'observed': sum(v['y'] for v in vals) / len(vals) if vals else None,
            }
        )
    ll = [r['log_loss'] for r in valid if 'log_loss' in r]
    clean = [r for r in valid if not r.get('format_fallback')]
    return {
        'n': len(records),
        'scored': len(valid),
        'statuses': {s: sum(r['status'] == s for r in records) for s in sorted({r['status'] for r in records})},
        'top1_accuracy': sum(r['top1'] for r in valid) / len(valid) if valid else None,
        'top3_precision': sum(r['top3_hits'] for r in valid) / denom if denom else None,
        'all3_accuracy': sum(r['all3'] for r in valid) / sum(r['all3_eligible'] for r in valid)
        if any(r['all3_eligible'] for r in valid)
        else None,
        'log_loss': sum(ll) / len(ll) if ll else None,
        'calibration': bins,
        'probabilities_provided_races': sum(r.get('probabilities_provided', False) for r in valid),
        'probabilities_zero_mass_races': sum(r.get('probabilities_zero_mass', False) for r in valid),
        'probability_policy': 'Absent or zero post-scratch mass uses uniform; rounding is normalized',
        'log_loss_gain_uniform': sum(r.get('log_loss_gain_uniform', 0) for r in valid) / len(valid) if valid else None,
        'private_nominal_no_takeout_roi': sum(r.get('nominal_net_yen', 0) for r in records) / stakes if stakes else None,
        'at_risk_yen': stakes,
        'refund_yen': sum(r['refund_yen'] for r in records),
        'format_failure_rate': sum(bool(r.get('format_fallback')) for r in records) / len(records) if records else 0,
        'success_only_top1': sum(r['top1'] for r in clean) / len(clean) if clean else None,
        'success_only_top3': sum(r['top3_hits'] for r in clean) / sum(r['top3_n'] for r in clean) if clean else None,
        'dead_heat_probability_note': 'log loss uses probability mass on winner set; calibration uses winner membership',
    }

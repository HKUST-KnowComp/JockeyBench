"""Integer score-yen ledger (micro-units). No odds, payouts or real-profit claims.

Every fee and every booked score is one idempotent entry; ``reconcile`` checks
the totals, the evidence receipts and the sealed revenue against each other.
"""

from decimal import ROUND_HALF_UP, Decimal

from .errors import Rejected

MICRO = 1000000
REVENUE_CATEGORIES = frozenset({'revenue_log', 'revenue_hit'})


def micro(value):
    return int((Decimal(str(value)) * MICRO).to_integral_value(rounding=ROUND_HALF_UP))


def fee(tariff, rate, units=1):
    return micro(Decimal(str(getattr(tariff, rate))) * Decimal(str(tariff.multiplier)) * Decimal(str(units)))


def ledger(state):
    return state.setdefault('ledger', {'entries': [], 'ids': {}, 'cost_micro': 0, 'revenue_micro': 0})


def book(state, ident, category, amount, **details):
    """Record ``amount`` once under ``ident``; rebooking the same entry is a no-op."""
    value = ledger(state)
    row = {'id': ident, 'category': category, 'amount_micro': amount, 'phase': state['phase'], 'index': state['index'], **details}
    if state.get('charging_disabled'):  # re-execution audit: quote fees, book nothing
        return row
    if ident in value['ids']:
        old = value['entries'][value['ids'][ident]]
        if old != row:
            raise Rejected('Ledger operation conflict')
        return old
    value['ids'][ident] = len(value['entries'])
    value['entries'].append(row)
    value['revenue_micro' if category in REVENUE_CATEGORIES else 'cost_micro'] += amount
    if category in REVENUE_CATEGORIES:
        value[category + '_micro'] = value.get(category + '_micro', 0) + amount
    return row


def compute_cost(state):
    return sum(e['amount_micro'] for e in ledger(state)['entries'] if e['category'] == 'compute')


def summary(state, config):
    """The ledger view shown to agents."""
    value = ledger(state)
    income = value['revenue_micro']
    return {
        'currency': 'score_yen_not_money',
        'tariff': config.tariff.version,
        'multiplier': config.tariff.multiplier,
        'cost_yen': value['cost_micro'] / MICRO,
        'settled_revenue_yen': income / MICRO,
        'net_score': (income - value['cost_micro']) / MICRO,
        'hit_bonus_yen': config.tariff.hit_bonus_yen,
        **{name + '_yen': value.get(name + '_micro', 0) / MICRO for name in ('revenue_log', 'revenue_hit')},
        'remaining_days': max(0, len(state['schedule']) - state['index'] - int(state['phase'] == 'reflect')),
        'monetary_cap': None,
    }


def reconcile(state):
    value = ledger(state)
    if len(value['entries']) != len(value['ids']):
        raise Rejected('Duplicate ledger identities')
    for offset, row in enumerate(value['entries']):
        if value['ids'].get(row['id']) != offset:
            raise Rejected('Ledger identity index mismatch')
    for name, is_revenue in [('cost_micro', False), ('revenue_micro', True)]:
        if value[name] != sum(r['amount_micro'] for r in value['entries'] if (r['category'] in REVENUE_CATEGORIES) == is_revenue):
            raise Rejected('Ledger total mismatch')
    if 'read_digests' in state:
        from .protocol.metering import repeat_index

        if repeat_index(state.get('audit', [])) != state['read_digests']:
            raise Rejected('Repeat-read index mismatch')
        for row in state.get('audit', []):
            if row.get('status') != 'ok' or 'read_key' not in row:
                continue
            offset = value['ids'].get(row['operation'] + ':data')
            actual = value['entries'][offset] if offset is not None else None
            if (actual['amount_micro'] if actual else 0) != row['_cost']['data_micro']:
                raise Rejected('Evidence receipt does not match ledger')
            if actual and actual.get('units') != row['charged_cells']:
                raise Rejected('Charged evidence does not match ledger units')
    tariff = state.get('binding', {}).get('config', {}).get('tariff')
    if tariff:
        reconcile_revenue(state, tariff)
    return {
        'entries': len(value['entries']),
        'cost_micro': value['cost_micro'],
        'revenue_micro': value['revenue_micro'],
        'reconciled': True,
    }


def reconcile_revenue(state, tariff):
    """Recompute every booked score from the sealed prediction and the booked outcome."""
    from .config import Tariff
    from .scoring import revenue_components, sealed_revenue

    tariff = Tariff(**tariff)
    expected = {}
    for race, score in state.get('scores', {}).items():
        sealed = state['submissions'][race]
        if sealed.get('delivery') == 'predictor' and sealed.get('strategy') not in state['valid_strategies']:
            raise Rejected('Hit bonus requires a validated program')
        parts = (
            sealed_revenue(sealed['prediction'], score['scoring_outcome'], tariff, sealed.get('delivery'))
            if score['status'] == 'ok'
            else {'revenue_log_yen': None, 'revenue_hit_yen': 0.0}
        )
        total = sum(parts.values()) if score['status'] == 'ok' else None
        if revenue_components(score) != parts or score['score_revenue_yen'] != total:
            raise Rejected('Sealed revenue components do not reconcile')
        for category in ('revenue_log', 'revenue_hit'):
            expected[category + ':' + race] = (category, race, micro(parts[category + '_yen'] or 0))
    actual = {
        r['id']: (r['category'], r.get('race_id'), r['amount_micro'])
        for r in ledger(state)['entries']
        if r['category'] in REVENUE_CATEGORIES
    }
    if actual != expected:
        raise Rejected('Revenue entries do not reconcile with sealed predictions')
    for category in ('revenue_log', 'revenue_hit'):
        if ledger(state).get(category + '_micro', 0) != sum(amount for kind, _, amount in expected.values() if kind == category):
            raise Rejected('Revenue component total mismatch')

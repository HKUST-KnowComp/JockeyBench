"""Agent-facing field catalogue: type, unit and meaning of every projected field."""

from .fields import CHA, CONNECTIONS, CONTEXT, CYB, KKA, KYI, LABELS, PEDIGREE, RACE, RESULT, RUNNER, TRAINING

DESCRIPTIONS = {
    'distance': ('metres', 'Race distance'),
    'surface': ('JRDB code', 'Surface category'),
    'condition': ('JRDB code', 'Race eligibility condition; retain A1/A2/A3/OP or numeric codes'),
    'draw': ('gate group', 'Declared bracket/gate group, distinct from runner number'),
    'sex': ('JRDB code', 'Declared sex category'),
    'age_days': ('days', 'Age at that race'),
    'carried_weight_tenths_kg': ('0.1 kg', 'Declared carried weight'),
    'card_body_weight': ('kg', 'Body weight if actually present in the previous-evening card'),
    'card_body_weight_change': ('kg', 'Body-weight change if present in the previous-evening card'),
    'body_weight': ('kg', 'Observed body weight in an initial-support label'),
    'body_weight_change': ('kg', 'Signed change; sign-padding whitespace is normalized'),
    'finish': ('rank', 'Official rank, in initial-support labels only; nonfinish is null'),
    'early3f_tenths_sec': ('0.1 second', 'Observed first three-furlong time'),
    'last3f_tenths_sec': ('0.1 second', 'Observed last three-furlong time'),
    'margin_tenths_sec': ('0.1 second', 'Observed time margin'),
    'early_time': ('source numeric unit', 'CHA opening training segment; no inferred conversion'),
    'middle_time': ('source numeric unit', 'CHA middle training segment; no inferred conversion'),
    'late_time': ('source numeric unit', 'CHA final training segment; no inferred conversion'),
    'furlongs': ('source numeric code', 'CHA recorded workout segment count'),
}
LABEL_MEANINGS = {
    'finish': 'Official finish rank in labels permitted at the current clock; nonfinish is null',
    'body_weight': 'Observed body weight in a permitted released label',
}
UNITS_V5 = {
    'late_start_rate': ('source rate unit', 'Recorded late-start rate; no inferred rescaling'),
    'partner_age': ('years', 'Workout partner age'),
    'slow_start': ('boolean', 'Whether the past start has a non-null start-adjustment record; no adjustment value is exposed'),
    'declared_runners': ('runners', 'Declared field size on that past race card'),
    'draw': ('gate group', 'Declared draw on that past race card'),
}
for _name in KKA:
    UNITS_V5[_name] = (
        'source distance unit' if 'distance' in _name else 'source rate unit',
        _name.replace('_', ' ') + '; recorded pedigree statistic, no inferred rescaling',
    )


def _base_entry(name):
    unit, meaning = DESCRIPTIONS.get(name, ('source numeric unit/code', name.replace('_', ' ')))
    return {
        'type': 'number_or_reviewed_code' if name == 'condition' else 'number',
        'nullable': True,
        'unit': unit,
        'meaning': meaning,
        'missing': 'null is unknown, not zero; consult missing map for absent/blank/invalid/unusable',
    }


def _v5_entry(group, name):
    identifier, count = name.endswith('_id'), group == 'connections'
    if name in UNITS_V5:
        unit, meaning = UNITS_V5[name]
    elif identifier:
        unit, meaning = 'HMAC identifier', 'Pseudonymous pedigree or past-start connection identifier'
    elif count:
        unit, meaning = 'starts', name.replace('_', ' ') + ' over the previous 365 days, from visible labels only'
    else:
        unit, meaning = 'opaque JRDB categorical code', name.replace('_', ' ') + '; opaque categorical code, no code table is assumed'
    return {
        'type': 'boolean' if name == 'slow_start' else 'string' if identifier else 'number',
        'nullable': not count,
        'unit': unit,
        'meaning': meaning,
        'missing': 'null is unknown; withheld labels have not_released reasons',
    }


def catalog():
    groups = {'race_facts': CONTEXT, 'runner_facts': RUNNER + ('age_days',), 'training': TRAINING, 'support_labels': RESULT}
    result = {group: {name: _base_entry(name) for name in names} for group, names in groups.items()}
    for name, meaning in LABEL_MEANINGS.items():
        result['support_labels'][name]['meaning'] = meaning
    additions = {
        'race_facts': RACE,
        'runner_facts': KYI + KKA + PEDIGREE,
        'training': CHA + CYB,
        'support_labels': LABELS,
        'connections': CONNECTIONS,
    }
    for group, names in additions.items():
        target = result.setdefault(group, {})
        for name in names:
            target[name] = _v5_entry(group, name)
    return result

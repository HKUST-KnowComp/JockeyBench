"""Exact decoder, including absent fields, explicit nulls and missing reasons."""
import copy
from .schema import VERSION, schema_fields, exact, CompactIntegrityError


def expand_compact_view(view):
    fields = schema_fields()
    expected = {name: {k: i for i, k in enumerate(keys)} for name, keys in fields.items()}
    if view.get('version') != VERSION or exact(view.get('schema_maps')) != exact(expected):
        raise CompactIntegrityError('Compact schema/version mismatch')

    def identities(mapping):
        if any(type(k) is not str for k in mapping) or any(type(v) is not int for v in mapping.values()) or sorted(mapping.values()) != list(range(len(mapping))):
            raise CompactIntegrityError('Non-bijective index map')
        return [k for k, v in sorted(mapping.items(), key=lambda pair: pair[1])]

    horses = identities(view['horse_map']); races = identities(view['race_map'])

    def identity(value, names):
        if not view['index_maps']:
            if value not in names:raise CompactIntegrityError('Unknown identity')
            return value
        if type(value) is not int or not 0 <= value < len(names):raise CompactIntegrityError('Invalid identity index')
        return names[value]

    def row(value, name, transforms=None):
        if value is None:return None
        keys = fields[name]; transforms = transforms or {}
        if type(value) is not list or len(value) != 2:raise CompactIntegrityError('Invalid row')
        values, absent = value
        if len(values) != len(keys) or any(type(i) is not int or not 0 <= i < len(keys) for i in absent) or len(set(absent)) != len(absent):
            raise CompactIntegrityError('Invalid row positions')
        if any(values[i] is not None for i in absent):raise CompactIntegrityError('Absent slot contains data')
        return {k: transforms.get(k, lambda x: copy.deepcopy(x))(values[i]) for i, k in enumerate(keys) if i not in absent}

    def missing(value, name):
        if not view['index_maps'] or value is None:return copy.deepcopy(value)
        kind, items = value; keys = fields[name]
        def field(i):
            if type(i) is not int or not 0 <= i < len(keys):raise CompactIntegrityError('Invalid missing index')
            return keys[i]
        if kind == 'list':return [field(i) for i in items]
        if kind != 'reasons' or len({i for i, _ in items}) != len(items):raise CompactIntegrityError('Invalid missing reasons')
        return {field(i): copy.deepcopy(reason) for i, reason in items}

    shared = [row(v, 'shared_race', {'race_facts': lambda x: row(x, 'race_facts')}) for v in view['historical_races']]
    if len(shared) != len(races):raise CompactIntegrityError('Historical map/rows mismatch')

    def start(value):
        result = row(value, 'shared_start' if view['shared_races'] else 'history_start', {
            'race_id': lambda x: identity(x, races) if view['shared_races'] else x,
            'race_facts': lambda x: row(x, 'race_facts'), 'runner_facts': lambda x: row(x, 'runner_facts'),
            'values': lambda x: row(x, 'history_values'), 'missing': lambda x: missing(x, 'history_values')})
        if view['shared_races']:result.update(copy.deepcopy(shared[races.index(result['race_id'])]))
        return result

    observations = []
    for domain, horse, value in view['observations']:
        if domain not in ('track_context', 'recent_history', 'training', 'condition_stats'):
            raise CompactIntegrityError('Unknown domain')
        observation = row(value, domain, {
            'horse_number': lambda x: identity(x, horses), 'facts': lambda x: row(x, 'race_facts'),
            'starts': lambda x: [start(v) for v in x], 'training': lambda x: row(x, 'training_values'),
            'groups': lambda x: [row(v, 'condition_group') for v in x],
            'missing': lambda x: missing(x, 'race_facts' if domain == 'track_context' else 'training_values')})
        observations.append({'domain': domain, 'arguments': {} if horse is None else {'horse_number': identity(horse, horses)},
                             'observation': observation})
    return {'schema': view['evidence_schema'], 'race_id': view['race_id'], 'entrants': horses, 'observations': observations}

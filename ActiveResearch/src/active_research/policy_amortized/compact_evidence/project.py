"""Pure projection/encoding, with no service, database, provider or ledger access."""
import copy
from .schema import VERSION, schema_fields, checked_keys, exact, CompactIntegrityError


def model_visible_semantic_projection(full):
    checked_keys(full, ('schema', 'race_id', 'entrants', 'observations'), 'package')
    if set(full) != {'schema', 'race_id', 'entrants', 'observations'}:
        raise CompactIntegrityError('Incomplete evidence package')
    result = copy.deepcopy(full)
    fields = schema_fields()
    for item in result['observations']:
        checked_keys(item, ('domain', 'arguments', 'observation', 'provenance', 'receipt'), 'observation')
        checked_keys(item['arguments'], ('horse_number',), 'arguments')
        domain = item['domain']
        if domain not in ('track_context', 'recent_history', 'training', 'condition_stats'):
            raise CompactIntegrityError('Unapproved domain')
        checked_keys(item['observation'], fields[domain] + (('billing_scope',) if domain == 'training' else ()), domain)
        item.pop('provenance', None); item.pop('receipt', None)
        if domain == 'training':item['observation'].pop('billing_scope', None)
    return result


def compact_view(full, *, shared_races=True, index_maps=True, retain_metadata=False):
    """Flags are offline component ablations; production always uses all defaults."""
    fields = schema_fields(); semantic = model_visible_semantic_projection(full)
    horses = semantic['entrants']
    if any(type(h) is not str for h in horses) or len(horses) != len(set(horses)):
        raise CompactIntegrityError('Invalid entrant identities')
    horse_map = {h: i for i, h in enumerate(horses)}
    shared = {}
    for item in semantic['observations']:
        if item['domain'] == 'recent_history':
            for start in item['observation'].get('starts', []):
                checked_keys(start, fields['history_start'], 'history start')
                rid = start['race_id']
                if type(rid) is not str:raise CompactIntegrityError('Invalid historical identity')
                facts = {k: copy.deepcopy(start[k]) for k in fields['shared_race'] if k in start}
                if rid in shared and exact(shared[rid]) != exact(facts):
                    raise CompactIntegrityError('Conflicting facts for historical race ' + rid)
                shared[rid] = facts
    race_map = {rid: i for i, rid in enumerate(sorted(shared))}

    def identity(value, mapping):
        if value not in mapping:raise CompactIntegrityError('Unknown identity')
        return mapping[value] if index_maps else value

    def row(value, name, transforms=None):
        if value is None:return None
        keys = fields[name]; checked_keys(value, keys, name)
        transforms = transforms or {}
        return [[transforms.get(k, lambda x: copy.deepcopy(x))(value[k]) if k in value else None for k in keys],
                [i for i, k in enumerate(keys) if k not in value]]

    def missing(value, name):
        if not index_maps:return copy.deepcopy(value)
        if value is None:return None
        keys = fields[name]
        names = value if type(value) in (dict, list) else None
        if names is None or any(k not in keys for k in names):raise CompactIntegrityError('Unknown missing field')
        if type(value) is list:return ['list', [keys.index(k) for k in value]]
        return ['reasons', [[keys.index(k), copy.deepcopy(value[k])] for k in keys if k in value]]

    def start(value):
        transforms = {'race_id': lambda x: identity(x, race_map) if shared_races else x,
                      'race_facts': lambda x: row(x, 'race_facts'),
                      'runner_facts': lambda x: row(x, 'runner_facts'),
                      'values': lambda x: row(x, 'history_values'),
                      'missing': lambda x: missing(x, 'history_values')}
        if shared_races:
            value = {k: v for k, v in value.items() if k not in fields['shared_race']}
        return row(value, 'shared_start' if shared_races else 'history_start', transforms)

    encoded = []
    for item in semantic['observations']:
        domain = item['domain']; transforms = {'horse_number': lambda x: identity(x, horse_map)}
        transforms.update({
            'facts': lambda x: row(x, 'race_facts'),
            'starts': lambda x: [start(v) for v in x],
            'training': lambda x: row(x, 'training_values'),
            'groups': lambda x: [row(v, 'condition_group') for v in x],
            'missing': lambda x: missing(x, 'race_facts' if domain == 'track_context' else 'training_values'),
        })
        args = item['arguments']
        encoded.append([domain, identity(args['horse_number'], horse_map) if 'horse_number' in args else None,
                        row(item['observation'], domain, transforms)])
    result = {'version': VERSION, 'schema_maps': {name: {k: i for i, k in enumerate(keys)} for name, keys in fields.items()},
              'row_encoding': '[values, absent_indices]; explicit null stays null; missing=[reasons,[[field_index,reason],...]] or [list,[field_index,...]]',
              'shared_races': shared_races, 'index_maps': index_maps,
              'evidence_schema': semantic['schema'], 'race_id': semantic['race_id'],
              'horse_map': horse_map, 'race_map': race_map if shared_races else {},
              'historical_races': [row(shared[rid], 'shared_race', {'race_facts': lambda x: row(x, 'race_facts')}) for rid in race_map] if shared_races else [],
              'observations': encoded}
    if retain_metadata:
        result['framework_metadata'] = [
            {**{k: copy.deepcopy(item[k]) for k in ('provenance', 'receipt') if k in item},
             **({'billing_scope': copy.deepcopy(item['observation']['billing_scope'])}
                if item['domain'] == 'training' and 'billing_scope' in item['observation'] else {})}
            for item in full['observations']]
    return result

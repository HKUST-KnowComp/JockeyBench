"""Pure lossless horse-local view. Flat rows, direct identities, named missingness."""
import copy
from ..compact_evidence.project import model_visible_semantic_projection
from .schema import VERSION, NOTE, columns, schema_fields, exact, checked_keys, CompactIntegrityError


def _missing(value, allowed):
    if value is not None and (type(value) not in (dict, list) or any(k not in allowed for k in value)):
        raise CompactIntegrityError('Unknown missing field or invalid missing representation')
    return copy.deepcopy(value)


def _row(value, headers, nested=()):
    if value is None:
        return None
    allowed = {h.split('.')[0] for h in headers}
    checked_keys(value, allowed, 'readable row')
    absent = []; nulls = []; result = []
    for parent in nested:
        if parent not in value:
            absent.append(parent)
        elif value[parent] is None:
            nulls.append(parent)
        else:
            checked_keys(value[parent], [h.split('.', 1)[1] for h in headers if h.startswith(parent + '.')], parent)
    for header in headers:
        if '.' in header:
            parent, key = header.split('.', 1)
            if parent in absent or parent in nulls:
                result.append(None); continue
            source = value[parent]
        else:
            source = value; key = header
        if key not in source:
            absent.append(header); result.append(None)
        else:
            result.append(copy.deepcopy(source[key]))
    row = {'values': result}
    if absent: row['absent_fields'] = absent
    if nulls: row['null_objects'] = nulls
    return row


def _unrow(row, headers, nested=()):
    if row is None: return None
    checked_keys(row, ('values', 'absent_fields', 'null_objects'), 'readable values')
    if type(row.get('values')) is not list or len(row['values']) != len(headers):
        raise CompactIntegrityError('Column/row width mismatch')
    absent = row.get('absent_fields', []); nulls = row.get('null_objects', [])
    if type(absent) is not list or type(nulls) is not list:
        raise CompactIntegrityError('Named absence must be a list')
    if any(type(k) is not str or k not in headers + list(nested) for k in absent) or any(k not in nested for k in nulls):
        raise CompactIntegrityError('Unknown absent/null field name')
    if len(set(absent)) != len(absent) or len(set(nulls)) != len(nulls) or set(absent) & set(nulls):
        raise CompactIntegrityError('Conflicting named absence')
    result = {p: None if p in nulls else {} for p in nested if p not in absent}
    for h, value in zip(headers, row['values']):
        parent = h.split('.')[0] if '.' in h else None
        if h in absent or parent in absent or parent in nulls:
            if value is not None: raise CompactIntegrityError('Nonempty absent value')
            continue
        if parent: result[parent][h.split('.', 1)[1]] = copy.deepcopy(value)
        else: result[h] = copy.deepcopy(value)
    if exact(_row(result, headers, nested)) != exact(row):
        raise CompactIntegrityError('Noncanonical readable row')
    return result


def readable_view(full, *, training_style='key_value'):
    """Flat training columns are an offline size ablation; production uses direct keys."""
    if training_style not in ('columns', 'key_value'): raise CompactIntegrityError('Unknown training representation')
    semantic = model_visible_semantic_projection(full); f = schema_fields(); c = columns(training_style)
    entrants = semantic['entrants']
    if any(type(h) is not str for h in entrants) or len(entrants) != len(set(entrants)):
        raise CompactIntegrityError('Invalid horse identities')
    groups = {h: {'horse_number': h, 'observations': []} for h in entrants}
    race = []
    for position, item in enumerate(semantic['observations']):
        domain = item['domain']; obs = copy.deepcopy(item['observation'])
        horse = item['arguments'].get('horse_number')
        if domain == 'track_context':
            if horse is not None: raise CompactIntegrityError('Race context has horse argument')
            if obs.get('facts') is not None: checked_keys(obs['facts'], f['race_facts'], 'track facts')
            if 'missing' in obs: _missing(obs['missing'], f['race_facts'])
            target = race
        else:
            if horse not in groups: raise CompactIntegrityError('Unknown direct horse identity')
            target = groups[horse]['observations']
        if domain == 'recent_history' and 'starts' in obs:
            rows = []
            for start in obs.pop('starts'):
                checked_keys(start, f['history_start'], 'history start')
                value = {k: v for k, v in start.items() if k != 'missing'}
                encoded = _row(value, c['HISTORY_COLUMNS'], ('race_facts', 'runner_facts', 'values'))
                if 'missing' in start: encoded['missing'] = _missing(start['missing'], f['history_values'])
                rows.append(encoded)
            obs['HISTORY_ROWS'] = rows
        elif domain == 'training':
            if 'missing' in obs: _missing(obs['missing'], f['training_values'])
            if 'training' in obs:
                training = obs.pop('training')
                if training_style == 'columns': obs['TRAINING_ROW'] = _row(training, c['TRAINING_COLUMNS'])
                else:
                    if training is not None: checked_keys(training, f['training_values'], 'training')
                    obs['TRAINING_KEY_VALUE'] = training
        elif domain == 'condition_stats' and 'groups' in obs:
            obs['CONDITION_ROWS'] = [_row(g, c['CONDITION_COLUMNS']) for g in obs.pop('groups')]
        target.append({'observation_order': position, 'domain': domain,
                       'arguments': copy.deepcopy(item['arguments']), 'observation': obs})
    result = {'version': VERSION, 'format': NOTE, 'columns': c,
              'training_style': training_style, 'evidence_schema': semantic['schema'],
              'race_id': semantic['race_id'], 'RACE_CONTEXT': race, 'HORSES': list(groups.values())}
    exact(result)
    return result


def expand_readable_evidence(view):
    checked_keys(view, ('version', 'format', 'columns', 'training_style', 'evidence_schema', 'race_id', 'RACE_CONTEXT', 'HORSES'), 'readable view')
    if view.get('version') != VERSION or exact(view.get('columns')) != exact(columns(view.get('training_style'))) or view.get('format') != NOTE:
        raise CompactIntegrityError('Readable schema/version drift')
    c = columns(view['training_style']); items = []; entrants = []
    for group in view['HORSES']:
        checked_keys(group, ('horse_number', 'observations'), 'horse section')
        entrants.append(group['horse_number'])
        if any(x['domain'] == 'track_context' or x['arguments'].get('horse_number') != group['horse_number'] for x in group['observations']):
            raise CompactIntegrityError('Horse locality mismatch')
        items.extend(group['observations'])
    if any(x['domain'] != 'track_context' for x in view['RACE_CONTEXT']):
        raise CompactIntegrityError('Race context locality mismatch')
    items.extend(view['RACE_CONTEXT'])
    order = [x.get('observation_order') for x in items]
    if any(type(i) is not int for i in order) or sorted(order) != list(range(len(items))):
        raise CompactIntegrityError('Observation order mismatch')
    observations = []
    for item in sorted(items, key=lambda x: x['observation_order']):
        checked_keys(item, ('observation_order', 'domain', 'arguments', 'observation'), 'readable observation')
        domain = item['domain']; obs = copy.deepcopy(item['observation'])
        if domain == 'recent_history' and 'HISTORY_ROWS' in obs:
            starts = []
            for row in obs.pop('HISTORY_ROWS'):
                values = {k: v for k, v in row.items() if k != 'missing'}
                start = _unrow(values, c['HISTORY_COLUMNS'], ('race_facts', 'runner_facts', 'values'))
                if 'missing' in row: start['missing'] = copy.deepcopy(row['missing'])
                starts.append(start)
            obs['starts'] = starts
        elif domain == 'training':
            key = 'TRAINING_ROW' if view['training_style'] == 'columns' else 'TRAINING_KEY_VALUE'
            if key in obs:
                value = obs.pop(key)
                obs['training'] = _unrow(value, c['TRAINING_COLUMNS']) if key == 'TRAINING_ROW' else value
        elif domain == 'condition_stats' and 'CONDITION_ROWS' in obs:
            obs['groups'] = [_unrow(row, c['CONDITION_COLUMNS']) for row in obs.pop('CONDITION_ROWS')]
        observations.append({'domain': domain, 'arguments': copy.deepcopy(item['arguments']), 'observation': obs})
    result = {'schema': view['evidence_schema'], 'race_id': view['race_id'], 'entrants': entrants, 'observations': observations}
    if exact(readable_view(result, training_style=view['training_style'])) != exact(view):
        raise CompactIntegrityError('Noncanonical readable view')
    return result


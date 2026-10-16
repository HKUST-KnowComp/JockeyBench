"""One-way quality projection. No raw observations enter the policy interpreter."""
import copy
from active_research.adaptive.domains import SUPPORTED

DEPTHS = (1, 3, 5)
DOMAIN_FIELDS = ('queried', 'query_count', 'queried_depth', 'records_returned',
    'missing_count', 'observed_slots', 'missing_ratio', 'feature_asof', 'freshness_days',
    'available_history_start_count', 'deeper_available', 'same_surface_records',
    'same_distance_records', 'sample_starts', 'labelled_starts', 'matching_groups')

def empty_domain():
    return dict(queried=False, query_count=0, queried_depth=0, records_returned=0,
        missing_count=0, observed_slots=0, missing_ratio=None, feature_asof=None,
        freshness_days=None, available_history_start_count=None, deeper_available=None,
        same_surface_records=0, same_distance_records=0, sample_starts=0,
        labelled_starts=0, matching_groups=0)

def quality(action, observation, native, card):
    """Value magnitudes/outcome labels never survive this fixed whitelist."""
    domain = action['domain']; result = empty_domain()
    result['queried'] = True
    if domain == 'recent_history':
        rows = observation['starts']; depth = action.get('depth', 5)
        result.update(queried_depth=depth, records_returned=len(rows), sample_starts=len(rows),
            labelled_starts=sum(s.get('values') is not None for s in rows),
            deeper_available=bool(native.get('next_cursor')) if depth < 5 else False)
        # A short delivered page proves the bounded total. A full page only
        # establishes a lower bound; exact unseen availability is not free.
        if len(rows) < depth: result['available_history_start_count'] = len(rows)
        current = card['facts']
        result['same_surface_records'] = sum(current.get('surface') is not None and
            s['race_facts'].get('surface') == current['surface'] for s in rows)
        result['same_distance_records'] = sum(current.get('distance') is not None and
            s['race_facts'].get('distance') == current['distance'] for s in rows)
        if rows: result['freshness_days'] = card['day_index'] - max(s['day_index'] for s in rows)
        slots = [v for s in rows for bucket in ('race_facts', 'runner_facts', 'values')
                 for v in (s.get(bucket) or {}).values()]
    elif domain == 'training':
        slots = list(observation['training'].values())
        result.update(records_returned=1, feature_asof=observation['feature_asof'])
    elif domain == 'condition_stats':
        slots = []
        groups = observation['groups']; facts = card['facts']
        result.update(records_returned=len(groups), sample_starts=observation['sample_starts'],
            labelled_starts=observation['labelled_starts'],
            matching_groups=sum(g.get('surface') == facts.get('surface') and
                g.get('distance') == facts.get('distance') and facts.get('surface') is not None
                and facts.get('distance') is not None for g in groups),
            same_surface_records=sum(g['starts'] for g in groups if facts.get('surface') is not None
                and g.get('surface') == facts['surface']),
            same_distance_records=sum(g['starts'] for g in groups if facts.get('distance') is not None
                and g.get('distance') == facts['distance']))
    else:
        slots = list(observation['facts'].values())
        result.update(records_returned=1, feature_asof=observation['feature_asof'])
    result.update(observed_slots=len(slots), missing_count=sum(v is None for v in slots))
    result['missing_ratio'] = result['missing_count'] / len(slots) if slots else None
    return result

def context(card):
    # No basic-card horse facts, draws, weights, sex, prize or hidden identity.
    return {'race_id':card['race_id'], 'day_index':card['day_index'],
        'feature_asof':card['feature_asof'], 'field_size':len(card['runners']),
        'runners':[{'number':r['number']} for r in card['runners']]}

def information_state(card, entries, events, stops=()):
    numbers = [r['number'] for r in card['runners']]
    state = {'field_size':len(numbers), 'query_count':len(events),
        'weighted_research_cost':sum(e['cost']['total_micro'] for e in events)/1e6,
        'raw_research_cost':sum(e['cost']['total_micro'] for e in events)/500000,
        'domains':{d:empty_domain() for d in SUPPORTED},
        'runners':{n:{'number':n,'stopped':n in stops,
                      'domains':{d:empty_domain() for d in SUPPORTED}} for n in numbers}}
    for action, obs, native in entries:
        d = action['domain']; n = action.get('horse_number')
        bucket = state['runners'][n]['domains'] if n else state['domains']
        old_count = bucket[d]['query_count']; bucket[d] = quality(action,obs,native,card)
        bucket[d]['query_count'] = old_count+1
    for d in SUPPORTED:
        if d == 'track_context': continue
        buckets = [state['runners'][n]['domains'][d] for n in numbers]
        state['domains'][d].update(queried=any(x['queried'] for x in buckets),
            query_count=sum(x['query_count'] for x in buckets),
            records_returned=sum(x['records_returned'] for x in buckets))
    state['domain_coverage'] = {d:sum(state['runners'][n]['domains'][d]['queried']
        for n in numbers)/len(numbers) if d!='track_context' else int(state['domains'][d]['queried'])
        for d in SUPPORTED}
    return copy.deepcopy(state)

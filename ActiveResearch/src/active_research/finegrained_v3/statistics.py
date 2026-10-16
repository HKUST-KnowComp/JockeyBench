"""Deterministic descriptive acquisition statistics; no learned thresholds."""
import copy
from collections import Counter
from active_research.adaptive.domains import SUPPORTED

def ratio(a,b):return a/b if b else None
def acquisition_statistics(s):
    events=[e for e in s['active_trace'] if e.get('evidence_id')]
    numbers=[r['number'] for r in s['active_initial']['card']['runners']]
    counts=Counter();costs=Counter();cells=Counter();horses={d:set() for d in SUPPORTED}
    paths={n:[] for n in numbers};triggers={'training':[],'condition_stats':[]}
    for e in events:
        a=e['action'];d=a['domain'];n=a.get('horse_number')
        counts[d]+=1;costs[d]+=e['cost']['total_micro']/1e6;cells[d]+=e['cost']['charged_cells']
        if n is not None:horses[d].add(n)
        if d=='recent_history':paths[n].append(a.get('depth',5))
        if d in triggers:
            before=e['control_before'];h=before['domains']['recent_history']
            triggers[d].append({'horse_number':n,'information_state_category':
                'history_unqueried' if not h['queried'] else 'history_observed',
                'records_returned':h['records_returned'],'missing_count':h['missing_count'],
                'missing_ratio':h['missing_ratio'],'freshness_days':h['freshness_days'],
                'sample_starts':h['sample_starts'],'labelled_starts':h['labelled_starts'],
                'same_surface_records':h['same_surface_records'],
                'same_distance_records':h['same_distance_records']})
    initial=Counter(v[0] for v in paths.values() if v);final=Counter(v[-1] for v in paths.values() if v)
    depth1=sum(v[0]==1 for v in paths.values() if v);depth3=sum(3 in v for v in paths.values())
    per_domain={d:{'calls':counts[d],'horses_queried':len(horses[d]),
        'horse_coverage':ratio(len(horses[d]),len(numbers)) if d!='track_context' else None,
        'logical_policy_actions':counts[d],
        'native_acquisition_operations':sum(e.get('native_acquisition_operations',0) for e in events if e['action']['domain']==d),
        'newly_acquired_cells':cells[d],'local_projections':sum(e.get('local_projection_operations',0) for e in events if e['action']['domain']==d),
        'cache_only_actions':sum(e.get('local_projection',False) for e in events if e['action']['domain']==d),
        'evidence_cells':cells[d],'weighted_cost':costs[d],'raw_cost':costs[d]/.5}
        for d in SUPPORTED}
    per_domain['recent_history'].update(initial_depth_distribution=dict(initial),final_depth_distribution=dict(final),
        final_depth_sum=sum(v[-1] for v in paths.values() if v),queried_horses=sum(bool(v) for v in paths.values()),
        mean_final_depth=ratio(sum(v[-1] for v in paths.values() if v),sum(bool(v) for v in paths.values())),
        stop_at_depth1_rate=ratio(final[1],sum(bool(v) for v in paths.values())),
        initial_depth1_horses=depth1,queried_depth3_horses=depth3,
        expand_1_to_3_count=sum(any(a==1 and b==3 for a,b in zip(v,v[1:])) for v in paths.values()),
        expand_3_to_5_count=sum(any(a==3 and b==5 for a,b in zip(v,v[1:])) for v in paths.values()))
    per_domain['recent_history']['expand_1_to_3_rate']=ratio(per_domain['recent_history']['expand_1_to_3_count'],depth1)
    per_domain['recent_history']['expand_3_to_5_rate']=ratio(per_domain['recent_history']['expand_3_to_5_count'],depth3)
    for d in triggers:per_domain[d].update(trigger_rate=ratio(len(horses[d]),len(numbers)),trigger_states=triggers[d])
    per_domain['track_context']['race_usage_rate']=int(counts['track_context']>0)
    domains_per_horse={n:sum(n in horses[d] for d in SUPPORTED if d!='track_context') for n in numbers}
    final_state=copy.deepcopy(s.get('information_state_final'))
    return {'accounting':{'logical_policy_actions':len(events),'native_acquisition_operations':sum(e.get('native_acquisition_operations',0) for e in events),'newly_acquired_cells':sum(e['cost']['charged_cells'] for e in events),'local_projections':sum(e.get('local_projection_operations',0) for e in events),'cache_only_actions':sum(e.get('local_projection',False) for e in events)},'domains':per_domain,'stopping':{'queries':len(events),'field_size':len(numbers),
        'mean_queries_per_race':len(events),'mean_domains_per_horse':ratio(sum(domains_per_horse.values()),len(numbers)),
        'horse_level_early_stop_count':sum(v<3 for v in domains_per_horse.values()),
        'horse_level_early_stop_rate':ratio(sum(v<3 for v in domains_per_horse.values()),len(numbers)),
        'explicit_stops':copy.deepcopy(s.get('acquisition_stops',[])),
        'acquisition_weighted_cost':sum(costs.values()),'acquisition_raw_cost':sum(costs.values())/.5,
        'evidence_completeness_at_stopping':final_state,
        'final_coverage_by_domain':final_state.get('domain_coverage') if final_state else None,
        'completeness_interpretation':'Observed quality and coverage; sufficiency is a policy decision, not a framework quality threshold'}}

def aggregate(rows):
    if not rows:return {'races':0,'domains':{},'stopping':{}}
    n=len(rows);result={'races':n,'domains':{},'stopping':{}}
    fields=sum(r['stopping']['field_size'] for r in rows)
    for d in SUPPORTED:
        rs=[r['domains'][d] for r in rows];x={k:sum(v[k] for v in rs) for k in ('calls','horses_queried','evidence_cells','weighted_cost','raw_cost','logical_policy_actions','native_acquisition_operations','newly_acquired_cells','local_projections','cache_only_actions')}
        x['horse_coverage']=ratio(x['horses_queried'],fields) if d!='track_context' else None
        if d=='recent_history':
            for key in ('initial_depth_distribution','final_depth_distribution'):
                c=Counter()
                for v in rs:c.update(v[key])
                x[key]=dict(c)
            for key in ('final_depth_sum','queried_horses','initial_depth1_horses','queried_depth3_horses','expand_1_to_3_count','expand_3_to_5_count'):x[key]=sum(v[key] for v in rs)
            x.update(mean_final_depth=ratio(x['final_depth_sum'],x['queried_horses']),
                stop_at_depth1_rate=ratio(x['final_depth_distribution'].get(1,0),x['queried_horses']),
                expand_1_to_3_rate=ratio(x['expand_1_to_3_count'],x['initial_depth1_horses']),
                expand_3_to_5_rate=ratio(x['expand_3_to_5_count'],x['queried_depth3_horses']))
        elif d in {'training','condition_stats'}:
            x.update(trigger_rate=ratio(x['horses_queried'],fields),trigger_states=[s for v in rs for s in v['trigger_states']])
        else:x['race_usage_rate']=sum(v['race_usage_rate'] for v in rs)/n
        result['domains'][d]=x
    result['stopping']={'mean_queries_per_race':sum(r['stopping']['queries'] for r in rows)/n,
        'mean_domains_per_horse':ratio(sum(r['stopping']['mean_domains_per_horse']*r['stopping']['field_size'] for r in rows),fields),
        'horse_level_early_stop_rate':ratio(sum(r['stopping']['horse_level_early_stop_count'] for r in rows),fields),
        'acquisition_weighted_cost_per_race':sum(r['stopping']['acquisition_weighted_cost'] for r in rows)/n,
        'evidence_completeness_at_stopping':[r['stopping']['evidence_completeness_at_stopping'] for r in rows],
        'final_coverage_by_domain':{d:result['domains'][d]['horse_coverage'] if d!='track_context' else result['domains'][d]['race_usage_rate'] for d in SUPPORTED}}
    result['accounting']={k:sum(r['accounting'][k] for r in rows) for k in ('logical_policy_actions','native_acquisition_operations','newly_acquired_cells','local_projections','cache_only_actions')}
    return result

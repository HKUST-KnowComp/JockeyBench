"""Released, deterministic descriptive program feedback; no raw winner identities."""
import math
import statistics
from collections import Counter
from jockeybench.errors import Rejected
from jockeybench.clock import release
from jockeybench.canonical import digest, packed
from jockeybench.research.aligned.scoring import checkpoint_components,DELIVERIES
from jockeybench.research.adaptive.domains import SUPPORTED
from . import FRAME

def extract(s,policy,clock):
    day=s['schedule'][0]['day'];race=s['schedule'][0]['races'][0]
    if clock<release(day) or s['release_ledger'].get(race)!=release(day) or race not in s['scores']:raise Rejected('Frame requires released outcome')
    score=checkpoint_components(s);row=score['races'][0];fact=row.get('scoring_outcome')
    ranking=s['submissions'][race]['prediction']['ranking']
    ranks=[fact['active'].index(w)+1 for w in fact['winners'] if w in fact['active']] if fact else []
    domains={};audit={r['operation']:r for r in s['audit']}
    for d in SUPPORTED:
        events=[e for e in s['active_trace'] if e.get('action',{}).get('domain')==d]
        horses=sorted({e['action']['horse_number'] for e in events if 'horse_number' in e['action']})
        domains[d]={'calls':len(events),'horses_queried':len(horses),'horse_coverage':len(horses)/len(ranking),
            'evidence_cells':sum(e['cost'].get('charged_cells',0) for e in events),
            **{k:sum(e.get(k,0) for e in events) for k in ('native_acquisition_operations','newly_acquired_cells','local_projection')},
            'local_projections':sum(e.get('local_projection_operations',0) for e in events),'cache_only_actions':sum(e.get('local_projection',False) for e in events),
            'logical_policy_actions':len(events),'raw_research_cost':sum(e['cost']['total_micro'] for e in events)/1e6/.5}
    starts=[len(o['observation']['starts']) for o in s['evidence_package']['observations'] if o['domain']=='recent_history']
    return {'race_id':race,'day':day,'field_size':len(ranking),'policy_version':policy['policy_version'],
        'policy_hash':policy['policy_hash'],'policy_execution_success':s['policy_execution']['success'],
        'tool_calls':sum(v['calls'] for v in domains.values()),'evidence_cells':sum(v['evidence_cells'] for v in domains.values()),
        'research_cost':score['cost']['raw_research_cost'],'weighted_research_cost':score['cost']['weighted_research_cost'],
        'EvidencePackage_bytes':len(packed(s['evidence_package'])),'domains':domains,
        'history_starts_per_read':starts,'delivery':row['delivery'],'top1_hit':row.get('top1_hit'),
        'graceful_trigger':s.get('graceful_trigger'),
        'top3_hit':min(ranks)<=3 if ranks else None,'winner_rank':min(ranks) if ranks else None,
        'log_reward':row['log_reward'],'B_contribution':row['common_B_contribution'],
        'gross_reward':row['common_reward'],'aligned_reward':score['aligned_reward'],'aligned_net':score['common_Net'],
        'release_clock':release(day),'record_digest':digest([s['ledger'],s['scores'],s['submissions'],s['evidence_package']]),
        'finegrained': acquisition_statistics(s)}

def mean(v):return statistics.mean(v) if v else None
def summary(rows):
    n=len(rows);counts=Counter(r['delivery'] for r in rows)
    return {'races':n,'tool_calls_per_race':mean([r['tool_calls'] for r in rows]),
        'research_cost_per_race':mean([r['research_cost'] for r in rows]),
        'aligned_reward':mean([r['aligned_reward'] for r in rows]),
        'Top1':mean([int(r['top1_hit']) for r in rows if r['top1_hit'] is not None]),
        'Top3':mean([int(r['top3_hit']) for r in rows if r['top3_hit'] is not None]),
        'winner_rank':mean([r['winner_rank'] for r in rows if r['winner_rank'] is not None]),
        'recent_history_horse_coverage':mean([r['domains']['recent_history']['horse_coverage'] for r in rows]),
        'training_usage_rate':mean([int(r['domains']['training']['calls']>0) for r in rows]),
        'fallback_rate':counts['execution_fallback']/n if n else None}

def association(x,y):
    if len(x)<5:return {'status':'insufficient_sample','n':len(x),'pearson_r':None}
    mx,my=mean(x),mean(y);a=sum((v-mx)**2 for v in x);b=sum((v-my)**2 for v in y)
    return {'status':'ok' if a and b else 'unavailable_constant_input','n':len(x),
        'pearson_r':sum((u-mx)*(v-my) for u,v in zip(x,y))/math.sqrt(a*b) if a and b else None}

def make_frame(rows,history,day,policy,clock,expected_races):
    if clock<release(day) or len(rows)!=len(expected_races) or {r['race_id'] for r in rows}!=set(expected_races):raise Rejected('Incomplete day release')
    if any(r['day']!=day or r['release_clock']!=release(day) or r['policy_hash']!=policy['policy_hash'] for r in rows):raise Rejected('Released frame binding')
    if any(r['day']>=day for r in history):raise Rejected('Future history')
    rows=sorted(rows,key=lambda r:r['race_id']);allrows=history+rows;n=len(rows)
    current=summary(rows);prevday=max((r['day'] for r in history),default=None)
    previous=summary([r for r in history if r['day']==prevday]) if prevday else None
    domains={}
    for d in SUPPORTED:
        records=[r['domains'][d] for r in rows];calls=sum(v['calls'] for v in records);cells=sum(v['evidence_cells'] for v in records);cost=sum(v['raw_research_cost'] for v in records)
        total_calls=sum(r['tool_calls'] for r in rows);total_cells=sum(r['evidence_cells'] for r in rows);domain_cost=sum(v['raw_research_cost'] for r in rows for v in r['domains'].values())
        domains[d]={'calls':calls,'call_share':calls/total_calls if total_calls else 0,
            'races_used':sum(v['calls']>0 for v in records),'race_usage_rate':sum(v['calls']>0 for v in records)/n,
            'horse_coverage':mean([v['horse_coverage'] for v in records]),'mean_horses_queried_per_race':mean([v['horses_queried'] for v in records]),
            'evidence_cells':cells,'evidence_cell_share':cells/total_cells if total_cells else 0,
            'raw_research_cost':cost,'cost_share':cost/domain_cost if domain_cost else 0}
    sizes=[r['EvidencePackage_bytes'] for r in rows];starts=[v for r in rows for v in r['history_starts_per_read']]
    domains['recent_history']['exposed_history_starts']={'mean':mean(starts),'median':statistics.median(starts) if starts else None,'basis':'per actual horse read; repeated reads retained'}
    frame={'protocol':FRAME,'day':day,'release_clock':release(day),
        'policy_identity':{k:policy[k] for k in ('policy_version','policy_hash','parent_policy_hash')},
        'races_executed':n,'policy_execution_success_rate':mean([int(r['policy_execution_success']) for r in rows]),
        'acquisition':{'total_tool_calls':sum(r['tool_calls'] for r in rows),'mean_tool_calls_per_race':current['tool_calls_per_race'],
            'total_evidence_cells':sum(r['evidence_cells'] for r in rows),'mean_evidence_cells_per_race':mean([r['evidence_cells'] for r in rows]),
            'total_research_cost':sum(r['research_cost'] for r in rows),'mean_research_cost_per_race':current['research_cost_per_race'],
            'EvidencePackage_bytes':{'mean':mean(sizes),'median':statistics.median(sizes),'max':max(sizes)},
            'cost_basis':'raw original ledger including card, submit, released results and interpreter CPU; domain totals cover acquisition calls only'},
        'domains':domains,'per_race':rows,'current_day':current,'rolling':summary(allrows),
        'trend':{'previous_day':previous,'current_day':current,'delta':{k:current[k]-previous[k] if previous and type(current[k]) in (float,int) and type(previous.get(k)) in (float,int) else None for k in current}},
        'delivery':{k:sum(r['delivery']==k for r in rows)/n for k in DELIVERIES},
        'policy_execution_error_rate':mean([int(not r['policy_execution_success']) for r in rows]),
        'prediction_delivery_failure_rate':mean([int(r.get('graceful_trigger') is not None or r['delivery']=='no_delivery') for r in rows]),
        'observed_association':{'interpretation':'descriptive association only; not causal contribution'}}
    for key in ('tool_calls','research_cost','evidence_cells'):
        frame['observed_association'][key+'_vs_aligned_reward']=association([r[key] for r in allrows],[r['aligned_reward'] for r in allrows])
    for d in SUPPORTED:
        for metric in ('horse_coverage','calls'):
            frame['observed_association'][d+'_'+metric+'_vs_aligned_reward']=association([r['domains'][d][metric] for r in allrows],[r['aligned_reward'] for r in allrows])
    frame['acquisition'].update({k:sum(r['finegrained']['accounting'][k] for r in rows) for k in ('logical_policy_actions','native_acquisition_operations','newly_acquired_cells','local_projections','cache_only_actions')})
    frame['finegrained']=aggregate([r['finegrained'] for r in rows])
    frame['frame_digest']=digest(frame);return frame

from .statistics import acquisition_statistics, aggregate

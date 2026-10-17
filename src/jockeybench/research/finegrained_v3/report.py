"""Derived reports; no outcome reads until the day release commits."""
import csv
import json
from pathlib import Path
from jockeybench.canonical import digest
from jockeybench.clock import release
from jockeybench.research.policy_amortized.semantic_json.report import provider,representation,outcomes,day_states,diff
from jockeybench.research.common_scoring import checkpoint_components
from .policy_builder import write
from .policy_contract import source_hash
from .frame import extract,make_frame
from .runtime import verify_semantics
from .audit_live import evidence_lifecycle

def read(path):return json.loads(Path(path).read_text())
def policy_for(out,identity):
    matches=[read(p) for p in (Path(out)/'policies').glob('research_policy_v*.json')]
    matches=[p for p in matches if p['policy_hash']==identity['policy_hash'] and p['policy_version']==identity['policy_version']]
    if len(matches)!=1:raise ValueError('Exact policy identity required')
    return matches[0]

def daily(out,day):
    out=Path(out);frame=read(out/('frame-'+day+'.json'));ss=day_states(out,day)
    if any(s['release_ledger']!={s['schedule'][0]['races'][0]:release(day)} for s in ss):raise ValueError('Day not released')
    result={'day':day,'races':len(ss),'policy_identity':frame['policy_identity'],
        'finegrained':frame['finegrained'],'acquisition':frame['acquisition'],'domains':frame['domains'],
        'representation':representation(ss),'provider':provider(ss),'outcomes':outcomes(frame['per_race']),
        'observed_association':frame['observed_association']}
    write(out/('daily-summary-'+day+'.json'),result);return result

def table(path,rows):
    if not rows:return
    keys=list(dict.fromkeys(k for r in rows for k in r))
    with Path(path).open('x',newline='') as f:
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(rows)

def economic_totals(common,warm):
    gross=sum(x['common_reward_booked'] for x in common)
    formal=sum(x['cost']['weighted_research_cost'] for x in common)
    historical=warm['weighted_ledger_cost']
    return {'evaluator':'common-score-yen-v2-b100-ungated-v1','B':100,'multiplier':.5,
        'Log':sum(r['log_reward'] or 0 for x in common for r in x['races']),
        'B_reward':sum(r['common_B_contribution'] for x in common for r in x['races']),
        'Gross':gross,'formal_weighted_cost':formal,'warmup_weighted_cost':historical,
        'weighted_cost':formal+historical,'common_net':gross-formal-historical,
        'API_USD_included_in_common_net':False}

def finalize(out):
    from .lifecycle import verify_frozen
    from . import PROTOCOL
    out=Path(out);m=read(out/'manifest.json');verify_frozen(out,m)
    status=read(out/'execution-status.json');gate=read(out/'five-race-gate.json')
    if not status['finished'] or status['errors'] or status['attempted']!=324 or not gate['passed']:raise ValueError('Incomplete trajectory')
    rows=[];history=[];all_states=[];days=[];allocations=[];evolution=[];common=[]
    for b in m['schedule']:
        ss=day_states(out,b['day']);frame=read(out/('frame-'+b['day']+'.json'));p=policy_for(out,frame['policy_identity'])
        current=[]
        for s in ss:
            if s['release_ledger']!={s['schedule'][0]['races'][0]:release(b['day'])}:raise ValueError('Release changed')
            evidence_lifecycle(s);verify_semantics(s)
            current.append(extract(s,p,release(b['day'])));common.append(checkpoint_components(s));all_states.append(s)
        expected=make_frame(current,history,b['day'],p,release(b['day']),b['races'])
        if digest(frame)!=digest(expected):raise ValueError('Frame regeneration mismatch')
        rows.extend(current);history.extend(current);d=daily(out,b['day']);days.append(d)
        for domain,stats in frame['finegrained']['domains'].items():
            allocations.append({'day':b['day'],'domain':domain,**{k:json.dumps(v,sort_keys=True) if isinstance(v,(dict,list)) else v for k,v in stats.items()}})
        nxt=policy_for(out,read(out/('next-policy-'+b['day']+'.json')))
        evolution.append({'day':b['day'],'active_version':p['policy_version'],'active_hash':p['policy_hash'],
            'next_version':nxt['policy_version'],'next_hash':nxt['policy_hash'],'source_diff':diff(p['source'],nxt['source'])})
    warm=read(out/'warmup-accounting.json')
    updates=[read(p) for p in sorted(out.glob('policy_update-*.json'))]
    result={'protocol':PROTOCOL,'completed':324,'frames_regenerated':10,'outcomes':outcomes(rows),
        'representation':representation(all_states),'provider':provider(all_states+updates),'warmup':warm,
        'common_totals':economic_totals(common,warm),
        'lifecycle_API_USD_estimate':provider(all_states+updates)['estimated_cost_usd']+warm['api_cost_usd_estimate'],
        'days':days,'causal_claim':False,'interpretation':'descriptive quality-cost tradeoff'}
    write(out/'active-v3-324-summary.json',result)
    (out/'DeepSeek-Active-v3-324-Final-Report.md').write_text('# DeepSeek Active-v3 324 final report\n\n'+json.dumps(result,indent=2))
    table(out/'active-v3-daily-trajectory.csv',[{'day':d['day'],'races':d['races'],
        **{k:v.get('mean') if isinstance(v,dict) else v for k,v in d['outcomes'].items()},
        'calls_per_race':d['finegrained']['stopping']['mean_queries_per_race'],
        'history_mean_final_depth':d['finegrained']['domains']['recent_history']['mean_final_depth'],
        'acquisition_weighted_cost_per_race':d['finegrained']['stopping']['acquisition_weighted_cost_per_race']}
        for d in days])
    table(out/'active-v3-policy-evolution.csv',evolution);table(out/'active-v3-domain-allocation.csv',allocations)
    return result

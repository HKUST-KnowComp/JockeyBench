import json
import statistics
from collections import Counter
from pathlib import Path
from jrdb_lh.common import packed
from active_research.aligned.scoring import DELIVERIES
from .policy_builder import write
from .model_usage import session_totals
from .specification import PROFILE

def stats(v):
    v=sorted(x for x in v if x is not None)
    return {'n':len(v),'sum':sum(v),'mean':statistics.mean(v) if v else None,
            'median':statistics.median(v) if v else None,'max':max(v) if v else None}

def rate(rows,key):
    values=[int(r[key]) for r in rows if r[key] is not None]
    return statistics.mean(values) if values else None

def usage(states):
    outputs=[o for s in states for o in s.get('model_outputs',[])]
    reqs=[r for s in states for r in s.get('llm_trajectory',[])]
    return {'logical_requests':len(reqs),'provider_attempts':sum(len(s.get('provider_attempts',[])) for s in states),
        'input_tokens':sum(o.get('prompt_tokens') or 0 for o in outputs),'output_tokens':sum(o.get('completion_tokens') or 0 for o in outputs),
        'estimated_cost_usd':sum(o.get('cost_usd') or 0 for o in outputs),'unknown_cost_outputs':sum(o.get('cost_usd') is None for o in outputs),
        'failed_attempts':sum(a['status']!='complete' for s in states for a in s.get('provider_attempts',[])),
        'latency_seconds':stats([r.get('logical_request_elapsed_seconds') for r in reqs])}

def split(states,phase):
    result=[]
    for s in states:
        rows=[r for r in s.get('llm_trajectory',[]) if r['phase'].startswith(phase)]
        ids={r['request_id'] for r in rows}
        result.append({'llm_trajectory':rows,'model_outputs':[o for o in s.get('model_outputs',[]) if o['request_id'] in ids],
            'provider_attempts':[a for a in s.get('provider_attempts',[]) if a['request_id'] in ids]})
    return result

def build_report(out,states,sessions):
    out=Path(out)
    # Include failed construction/update sessions persisted before an exception.
    sessions=[json.loads(p.read_text()) for p in sorted(out.glob('policy_*.json')) if p.name.startswith(('policy_build','policy_update'))]
    counts=Counter(d['delivery'] for s in states for d in s.get('aligned_delivery',{}).values())
    status=json.loads((out/'execution-status.json').read_text())
    frames=[json.loads(p.read_text()) for p in sorted(out.glob('frame-*.json'))]
    rows=[r for f in frames for r in f['per_race']]
    decomposition={k:usage(split(pool,phase)) for k,pool,phase in [('builder',sessions,'policy_build-'),('updater',sessions,'policy_update-'),('prediction',states,'prediction-'),('repair',states,'finalization-')]}
    builders=[s for s in sessions if s.get('phase')=='policy_build']
    updaters=[s for s in sessions if s.get('phase')=='policy_update']
    if (out/'warmup-session.json').exists():builders.append(json.loads((out/'warmup-session.json').read_text()))
    decomposition['builder']=session_totals(builders,'builder')
    decomposition['updater']=session_totals(updaters,'updater')
    result={'protocol':PROFILE['experiment_protocol'],'usage_accounting_protocol':PROFILE['usage_accounting_protocol'],
        'builder_session_usage':decomposition['builder'],'updater_session_usage':decomposition['updater'],
        'planned_races':24,'attempted':len(states),
        'delivery':{k:counts[k] for k in DELIVERIES},'hard_integrity_failures':sum(e['hard_integrity_failure'] for e in status['errors']),
        'status':status,'api':decomposition,'total_api_estimate':sum(v['estimated_cost_usd'] for v in decomposition.values()),
        'total_logical_requests':sum(v['logical_requests'] for v in decomposition.values()),
        'prediction_logical_requests_per_race':decomposition['prediction']['logical_requests']/len(states) if states else None,
        'amortized_build_update_cost_per_race':sum(decomposition[k]['estimated_cost_usd'] for k in ('builder','updater'))/len(states) if states else None,
        'policy_execution_success_rate':sum(s.get('policy_execution',{}).get('success',False) for s in states)/len(states) if states else None,
        'policy_execution_seconds':stats([s.get('policy_execution',{}).get('elapsed_seconds') for s in states]),
        'EvidencePackage_bytes':stats([len(packed(s['evidence_package'])) for s in states if 'evidence_package' in s]),
        'prediction_request_bytes':stats([x['bytes'] for s in states for x in s.get('request_sizes',[]) if x['phase']=='prediction-1']),
        'context_limit_frequency':sum(s.get('graceful_trigger')=='context_limit' for s in states),
        'cost_safety_stop_races':sum(bool(s.get('funding_stop')) for s in states),
        'frames_generated':len(frames),'frame_bytes':stats([len(packed(f)) for f in frames]),
        'acquisition':frames[0]['acquisition'] if frames else None,'domains':frames[0]['domains'] if frames else None,
        'per_race_acquisition_outcomes':rows,
        'outcomes':{k:stats([r[k] for r in rows]) for k in ('winner_rank','log_reward','B_contribution','gross_reward','research_cost','aligned_reward')},
        'Top1':rate(rows,'top1_hit'),
        'Top3':rate(rows,'top3_hit'),
        'initial_policy':json.loads((out/'initial-policy.json').read_text()) if (out/'initial-policy.json').exists() else None,
        'day2_candidate':json.loads((out/'day2-candidate.json').read_text()) if (out/'day2-candidate.json').exists() else None,
        'comparison':{'sequential_day1_logical_requests':534,'sequential_calls_per_race':22.25,
            'interpretation':'Single-day descriptive scaffold diagnostic; no superiority or causal claim.'},
        'cost_note':'Failed-attempt actual charges may be unknown; reservations are exposure, not observed cost.'}
    result['all_logical_requests_per_race']=result['total_logical_requests']/len(states) if states else None
    write(out/'smoke-report.json',result)
    (out/'Policy-Amortized-Research-Day1-Smoke-Report.md').write_text('# Policy-Amortized Research Day1 smoke\n\n'+
        '24-race 2024-01-06 scope. Day2 races are not executed.\n\n```json\n'+json.dumps(result,ensure_ascii=False,indent=2)+'\n```\n')

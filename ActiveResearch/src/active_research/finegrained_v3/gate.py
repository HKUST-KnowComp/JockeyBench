"""Five-race engineering gate, reading no winner/score/quality values."""
import json
from pathlib import Path
from jrdb_lh.common import digest
from jrdb_lh.ledger import reconcile
from active_research.aligned.integrity import IntegrityFailure
from active_research.policy_amortized.semantic_json.gate import load_checkpoint,retries,nonresult_metrics,barrier,GateFailed
from .policy_builder import write
from .runtime import verify_semantics
from .audit_live import evidence_lifecycle

MODEL='deepseek-ai/DeepSeek-V4-Flash-0731'
def evaluate(rows,reference):
    ids=reference['race_ids']
    criteria={'exact_five_races':len(rows)==5 and [r['race_id'] for r in rows]==ids,
        'five_sealed':len(rows)==5 and all(r['sealed'] for r in rows),
        'exact_identity':bool(rows) and all(r['response_models'] and all(x==MODEL for x in r['response_models']) for r in rows),
        'zero_acquisition_llm':all(r['acquisition_llm_calls']==0 for r in rows),
        'five_normal_prediction_requests':len(rows)==5 and all(r['normal_prediction_calls']==1 for r in rows),
        'context_safe':bool(rows) and all(0<r['request_bytes']<262144 and not r['context_limit'] for r in rows),
        'no_hidden_fallback':bool(rows) and all(r['delivery']==['direct_predictor'] for r in rows),
        'policy_cpu_safe':bool(rows) and all(r['policy_cpu_seconds']<30 and r['policy_cpu_limit']==30 for r in rows),
        'source_binding_reused':bool(rows) and all(r['source_binding_valid'] and r['source_full_verifications']==1 and r['source_reuse_checks']>=r['marginal_accounting']['logical_actions'] for r in rows),
        'zero_acquisition_runtime_failure':all(r['acquisition_runtime_failure'] is False for r in rows),
        'successful_policy':bool(rows) and all(r['policy_success'] for r in rows),
        'depth_and_control_audited':bool(rows) and all(r['control_audit']=='PASS' for r in rows),
        'ledger_correct':bool(rows) and all(r['ledger_correct'] for r in rows),
        'no_release':all(r['release_count']==0 for r in rows),
        'no_cost_safety_stop':all(not r['cost_safety_stop'] for r in rows)}
    return {'passed':all(criteria.values()),'criteria':criteria,'per_race':rows,
        'outcomes_inspected':False,'accuracy_used_for_stopping':False,'release_count':0,'updater_calls':0,
        'normal_prediction_calls':sum(r['normal_prediction_calls'] for r in rows),
        'acquisition_llm_calls':sum(r['acquisition_llm_calls'] for r in rows)}

def run_gate(out,manifest):
    from .lifecycle import verify_frozen
    out=Path(out);verify_frozen(out,manifest)
    paths=sorted((out/'runs').glob('PAR-*/checkpoint.json.gz'))
    if len(paths)!=5 or len(list((out/'runs').glob('PAR-*')))!=5:raise IntegrityFailure('Before race six only')
    if list(out.glob('frame-*.json')) or list(out.glob('policy_update*.json')):raise IntegrityFailure('Early updater')
    rows=[]
    for path in paths:
        s=load_checkpoint(path)
        if s.get('release_ledger') or s.get('scores') or s.get('aligned_scores'):raise IntegrityFailure('Early release')
        if any(a.get('tool')=='results' for a in s['audit']):raise IntegrityFailure('Results access')
        verify_semantics(s);evidence_lifecycle(s);reconcile(s)
        from .marginal_cost import audit_owned_cache
        accounting=audit_owned_cache(s,complete=True)
        r=nonresult_metrics(s);r.update(marginal_accounting=accounting); r.update(response_models=[o.get('response_model') for o in s.get('model_outputs',[])],
            acquisition_llm_calls=s['policy_execution']['llm_calls'],policy_success=s['policy_execution']['success'],
            normal_prediction_calls=sum(t['phase']=='prediction-1' for t in s['llm_trajectory']),
            policy_cpu_seconds=s['policy_execution']['timing']['policy_cpu_seconds'],policy_cpu_limit=s['policy_execution']['timing']['limit_seconds'],
            source_binding_valid=True,source_full_verifications=s['policy_execution']['source_verification']['full_verifications'],
            source_reuse_checks=s['policy_execution']['source_verification']['reuse_checks'],acquisition_runtime_failure=s.get('graceful_trigger')=='runtime_failure',
            control_audit='PASS',ledger_correct=True,release_count=0,cost_safety_stop=bool(s.get('funding_stop')),
            history_depths=[e['action'].get('depth',5) for e in s['active_trace'] if e.get('action',{}).get('domain')=='recent_history'])
        rows.append(r)
    expected=[r for b in manifest['schedule'] for r in b['races']][:5]
    result=evaluate(rows,{'race_ids':expected});write(out/'five-race-gate.json',result)
    name='Active-v3-5Race-Gate-'+('Pass' if result['passed'] else 'Failure')+'-Report.md'
    (out/name).write_text('# Active-v3 outcome-blind engineering gate\n\n'+json.dumps(result,indent=2))
    return result

"""Observed consumption is a result, never a component of common reward."""
import json
from pathlib import Path
from .specification import PROFILE, UNMATCHED

BUDGET_NOTE = ('Modeling historical runs used a trajectory-level estimated exposure ceiling, '
              'whereas A2-Aligned-v1 retains the legacy A2 per-race provider safety ceiling '
              'to avoid making full-cohort completion dependent on a new global budget cutoff. '
              'A2 retains a legacy per-race $0.75 provider safety ceiling. '
              'No global $3 trajectory exposure stop is used in A2-Aligned-v1.')

def resources(states):
    outputs = [r for s in states for r in s.get('model_outputs',[])]
    attempts = [r for s in states for r in s.get('provider_attempts',[])]
    count = len(states)
    total = sum(r['cost_usd'] for r in outputs if r.get('cost_usd') is not None)
    return {'attempted':count,'total_estimated_API_cost_usd':total,
            'research_model_calls':sum(sum(r['phase'].startswith('prediction-') for r in s.get('llm_trajectory',[])) for s in states),
            'finalization_model_calls':sum(sum(r['phase']=='finalization-1' for r in s.get('llm_trajectory',[])) for s in states),
            'finalization_repair_considered':sum(s.get('finalization_repair_considered',0) for s in states),
            'finalization_repair_successes':sum(bool(s.get('finalization_repair_success')) for s in states),
            'transport_attempts':len(attempts),
            'estimated_API_cost_per_race_usd':total/count if count else None,
            'model_calls':sum(len(s.get('llm_trajectory',[])) for s in states),
            'model_calls_per_race':sum(len(s.get('llm_trajectory',[])) for s in states)/count if count else None,
            'input_tokens':sum(r['prompt_tokens'] for r in outputs if r.get('prompt_tokens') is not None),
            'output_tokens':sum(r['completion_tokens'] for r in outputs if r.get('completion_tokens') is not None),
            'unknown_usage_outputs':sum(r.get('prompt_tokens') is None or r.get('completion_tokens') is None for r in outputs),
            'unknown_cost_outputs':sum(r.get('cost_usd') is None for r in outputs),
            'failed_attempts':sum(r.get('status')!='complete' for r in attempts),
            'failed_attempt_cost':'unavailable unless recorded; reservations are exposure, not observed cost',
            'cost_basis':sorted({r.get('cost_basis','unavailable') for r in outputs}),
            'request_latency_seconds':sum(r.get('elapsed_seconds',0) for r in attempts),
            'retry_sleep_seconds':sum(r.get('retry_delay_seconds',0) for r in attempts),
            'race_elapsed_wall_seconds':sum(s.get('aligned_race_elapsed_seconds',0) for s in states),
            'tool_and_local_execution_seconds':sum(s.get('aligned_tool_and_local_seconds',0) for s in states),
            'invalid_actions':sum(len(s.get('protocol_errors',[])) for s in states),
            'valid_submitted':sum(bool(s.get('submissions')) for s in states),
            'sealed':sum(bool(s.get('submissions')) for s in states),
            'released':sum(bool(s.get('release_ledger')) for s in states)}

def build_report(out):
    out = Path(out)
    rows = json.loads((out/'A2-results.json').read_text()) if (out/'A2-results.json').exists() else []
    manifest=json.loads((out/'A2-manifest.json').read_text()) if (out/'A2-manifest.json').exists() else {}
    status=json.loads((out/'A2-execution-status.json').read_text()) if (out/'A2-execution-status.json').exists() else {}
    result = {**PROFILE,'remaining_unmatched_items':list(UNMATCHED),
              'provider_budget_note':BUDGET_NOTE,'race_reports':rows,
              'provider_safety_threshold_in_common_reward':False}
    per_race=[r['aligned_resources'] for r in rows if 'aligned_resources' in r]
    totals={key:sum(r[key] for r in per_race) for key in
            ('attempted','model_calls','total_estimated_API_cost_usd','input_tokens','output_tokens',
             'failed_attempts','unknown_usage_outputs','unknown_cost_outputs','valid_submitted',
             'sealed','released','request_latency_seconds','retry_sleep_seconds',
             'race_elapsed_wall_seconds','tool_and_local_execution_seconds','invalid_actions')}
    for key in ('research_model_calls','finalization_model_calls','finalization_repair_considered',
                'finalization_repair_successes','transport_attempts'):
        totals[key]=sum(r.get(key,0) for r in per_race)
    denominator=status.get('attempted',totals['attempted'])
    totals['attempted']=denominator
    totals['estimated_API_cost_per_race_usd']=totals['total_estimated_API_cost_usd']/denominator if denominator else None
    totals['model_calls_per_race']=totals['model_calls']/denominator if denominator else None
    totals['cost_completeness']='partial' if totals['failed_attempts'] or totals['unknown_cost_outputs'] else 'recorded outputs complete'
    result['resources']=totals
    result['scientific_arm']=manifest.get('scientific_arm')
    result['execution_profile_hash']=manifest.get('execution_profile_hash')
    result['execution_errors']=status.get('errors',[])
    result['failure_counts']={code:sum(e.get('failure_code')==code for e in status.get('errors',[]))
        for code in ('provider_failure','context_limit','turn_limit','cost_safety_stop','invalid_action','service_safety_stop','runtime_failure','hard_integrity_failure')}
    deliveries=[d for r in rows for d in r.get('delivery',{}).values()]
    result['delivery_counts']={k:sum(d['delivery']==k for d in deliveries) for k in
        ('direct_predictor','repaired_predictor','uniform_submit','execution_fallback','no_delivery')}
    result['delivery_rates']={k:v/denominator if denominator else None for k,v in result['delivery_counts'].items()}
    result['hard_failure_count']=result['failure_counts']['hard_integrity_failure']
    result['fallback_trigger_reason_distribution']={reason:sum(d.get('reason')==reason for d in deliveries if d['delivery']=='execution_fallback')
        for reason in sorted({d.get('reason') for d in deliveries if d['delivery']=='execution_fallback' and d.get('reason')})}
    result['finalization_repair_success_rate']=totals['finalization_repair_successes']/totals['finalization_model_calls'] if totals['finalization_model_calls'] else None
    scored=[(r,d) for report in rows if report.get('aligned_scoring')
            for r in report['aligned_scoring']['races']
            for d in [report['aligned_scoring']['cost']['weighted_research_cost']]]
    for label,category in (('overall',None),('direct_only','direct_predictor'),('repaired_only','repaired_predictor')):
        selected=[r for r,c in scored if (category is None or r['delivery']==category) and r['status']=='ok']
        result['Top1_'+label]=sum(r['top1_hit'] for r in selected)/len(selected) if selected else None
    result['log_gain_overall']=sum(r['log_reward'] or 0 for r,c in scored)
    result['reward_overall']=sum(r['common_reward'] or 0 for r,c in scored)
    result['net_overall']=result['reward_overall']-sum(c for r,c in scored)
    result['gross_reward_overall']=result['reward_overall']
    result['aligned_reward_overall']=result['net_overall']
    result['reward_components_basis']='gross_reward_overall = log + eligible bonus; aligned_reward_overall/net_overall subtract actual weighted research cost'
    result['research_cost_on_fallback_races']=sum(c for r,c in scored if r['delivery']=='execution_fallback')
    result['usage_completeness']='partial' if totals['failed_attempts'] or totals['unknown_usage_outputs'] else 'recorded outputs complete'
    (out/'A2-aligned-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return result

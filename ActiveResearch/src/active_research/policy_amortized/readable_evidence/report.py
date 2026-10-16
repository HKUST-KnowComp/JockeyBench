"""Read-only post-Day1 audits and full three-version report. No model/DB calls."""
import copy
import difflib
import gzip
import json
import math
from pathlib import Path
from jrdb_lh.common import digest, packed, file_hash, release
from jrdb_lh.ledger import REVENUE_CATEGORIES
from ..frame import extract, make_frame
from ..model_usage import session_totals
from ..compact_evidence.offline import stats
from ..compact_evidence.project import model_visible_semantic_projection
from .audit import audit_package
from .runtime import verify_semantics
from .lifecycle import verify_frozen, P0_HASH
from .schema import exact, VERSION


def read(path):return json.loads(Path(path).read_text())


def states(folder):
    return [json.loads(gzip.decompress(p.read_bytes())) for p in sorted((Path(folder)/'runs').glob('PAR-*/checkpoint.json.gz'))]


def statistics(values):
    values=[v for v in values if v is not None]
    return {'n':len(values),'sum':sum(values),**(stats(values) if values else {k:None for k in ('mean','median','p90','p95','max')})}


def provider_metrics(checkpoints):
    rows=[]; failures=[]
    for s in checkpoints:
        outputs=s.get('model_outputs',[]);requests=s.get('llm_trajectory',[]);attempts=s.get('provider_attempts',[])
        inp=sum(o.get('prompt_tokens') or 0 for o in outputs);output=sum(o.get('completion_tokens') or 0 for o in outputs)
        rows.append({'run_id':s['run_id'],'race_id':s['schedule'][0]['races'][0],
          'input_tokens':inp,'output_tokens':output,'output_input_ratio':output/inp if inp else None,
          'api_estimate_usd':sum(o.get('cost_usd') or 0 for o in outputs),
          'latency_seconds':sum(x.get('logical_request_elapsed_seconds') or 0 for x in requests),
          'logical_requests':len(requests),'provider_attempts':len(attempts),
          'retry_count':sum(x.get('attempt_index',0)>0 for x in attempts),
          'unknown_cost_outputs':sum(o.get('cost_usd') is None for o in outputs)})
        failures.extend({'run_id':s['run_id'],**{k:a[k] for k in ('status','attempt_index','http_status','elapsed_seconds','reserved_cost_usd') if k in a}}
                        for a in attempts if a['status']!='complete')
    result={k:statistics([r[k] for r in rows]) for k in ('input_tokens','output_tokens','api_estimate_usd','latency_seconds','output_input_ratio')}
    result.update(per_race=rows,logical_requests=sum(r['logical_requests'] for r in rows),
        provider_attempts=sum(r['provider_attempts'] for r in rows),retry_count=sum(r['retry_count'] for r in rows),
        failed_attempts_actual_charge_unknown=failures,unknown_cost_outputs=sum(r['unknown_cost_outputs'] for r in rows))
    result['aggregate_output_input_ratio']=result['output_tokens']['sum']/result['input_tokens']['sum'] if result['input_tokens']['sum'] else None
    return result


def next_policy(out, initial):
    sessions=[read(p) for p in sorted(out.glob('policy_update*.json'))]
    actions=[];proposals=[]
    for s in sessions:
        for output in s['model_outputs']:
            for call in output['message'].get('tool_calls',[]):
                function=call['function'];args=json.loads(function['arguments']);actions.append(function['name'])
                if function['name'] in ('validate_research_policy','save_research_policy') and 'source' in args:
                    from ..policy_contract import source_hash
                    source=args['source'];proposals.append({'action':function['name'],'source':source,'hash':source_hash(source),
                        'diff_from_P0':''.join(difflib.unified_diff(initial['source'].splitlines(True),source.splitlines(True),fromfile='P0',tofile=function['name']))})
    records=[read(p) for p in sorted((out/'policies').glob('research_policy_v*.json'))]
    saved={r['policy_hash'] for r in records[1:]}
    for proposal in proposals:
        proposal['status']='saved' if proposal['hash'] in saved else 'validated-but-uncommitted' if proposal['action']=='validate_research_policy' else 'save_not_adopted'
    candidate=read(out/'day2-candidate.json');adopted=next(r for r in reversed(records) if r['policy_hash']==candidate['policy_hash'])
    assert all(r['model_usage']==r['model_usage_at_save'] and r['model_usage_scope']=='through_save_research_policy' for r in records)
    return {'candidate':candidate,'state':'saved new policy' if len(records)>1 else 'retained P0',
            'actions':actions,'proposals':proposals,'adopted_source':adopted['source'],'adopted_hash':adopted['policy_hash'],
            'adopted_diff_from_P0':''.join(difflib.unified_diff(initial['source'].splitlines(True),adopted['source'].splitlines(True),fromfile='P0',tofile='adopted')),
            'saved_artifacts':records,'updater_session_usage':session_totals(sessions,'updater'),
            'session_finish_received':[s['updater_session_usage']['finish_received'] for s in sessions],
            'day2_executed':0}


def finalize(out):
    out=Path(out);root=out.parent.parent;manifest=read(out/'manifest.json');verify_frozen(out,manifest)
    run=read(out/'readable-smoke-report.json');status=run['status']
    assert status['finished'] and not status['errors'] and status['day2_races_executed']==0 and not status['full_experiment_started']
    assert status['completed_days']==['2024-01-06']
    refs=read(out/'comparison-source.json');old=Path(refs['directory']);compact=Path(refs['compact_directory']);offline=Path(refs['offline_directory'])
    initial=read(out/'initial-policy.json');assert initial['policy_hash']==P0_HASH
    full_sets={'original_warmed_v2':states(old),'compact_v1':states(compact),'readable_v2':states(out)}
    assert all(len(s)==24 for s in full_sets.values())
    reports={'original_warmed_v2':read(old/'v2-smoke-report.json'),'compact_v1':read(compact/'compact-smoke-report.json'),'readable_v2':run}
    old_by_race={s['schedule'][0]['races'][0]:s for s in full_sets['original_warmed_v2']}
    compact_by_race={s['schedule'][0]['races'][0]:s for s in full_sets['compact_v1']}
    per_race=[];frame_rows=[]
    fees=lambda s:sum(e['amount_micro'] for e in s['ledger']['entries'] if e['category'] not in REVENUE_CATEGORIES and e['category']!='compute')
    cpu=lambda s:sum(e['amount_micro'] for e in s['ledger']['entries'] if e['category']=='compute')
    for s in full_sets['readable_v2']:
        immutable=packed(s);race=s['schedule'][0]['races'][0];baseline=old_by_race[race]
        proof=audit_package(s['evidence_package'],s['prediction_context']['ReadableCompactEvidence'])
        assert all(s[k]==v for k,v in proof.items())
        for prior in (baseline,compact_by_race[race]):
            assert exact(model_visible_semantic_projection(s['evidence_package']))==exact(model_visible_semantic_projection(prior['evidence_package']))
            actions=lambda x:[e['action'] for e in x['active_trace'] if e.get('evidence_id')]
            assert exact(actions(s))==exact(actions(prior))
            assert fees(s)==fees(prior)
        semantic=verify_semantics(s)
        row=extract(s,initial,release('2024-01-06'));previous=extract(baseline,initial,release('2024-01-06'))
        assert row['tool_calls']==previous['tool_calls'] and row['evidence_cells']==previous['evidence_cells']
        assert exact(row['domains'])==exact(previous['domains'])
        assert math.isclose(row['research_cost']-previous['research_cost'],2*(cpu(s)-cpu(baseline))/1e6,abs_tol=1e-9)
        frame_rows.append(row)
        per_race.append({'run_id':s['run_id'],'race_id':race,**proof,'semantic_audit':semantic,
                        'matched_input_actions_fees_cells':True,'weighted_cpu_delta_micro':cpu(s)-cpu(baseline)})
        assert packed(s)==immutable
    frame=read(out/'frame-2024-01-06.json')
    assert exact(frame)==exact(make_frame(frame_rows,[],frame['day'],initial,frame['release_clock'],manifest['schedule'][0]['races']))
    historical=read(offline/'historical-before.json');assert all(file_hash(Path(k))==h for k,h in historical.items())
    assert file_hash(out/'provider.json')==file_hash(old/'provider.json')
    current_config=read(out/'config.json');prior_config=read(old/'config.json')
    assert {k:v for k,v in current_config.items() if k!='managed_root'}=={k:v for k,v in prior_config.items() if k!='managed_root'}
    comparison={}
    for name,ss in full_sets.items():
        report=reports[name]
        key={'original_warmed_v2':'EvidencePackage','compact_v1':'CompactEvidenceView','readable_v2':'ReadableCompactEvidence'}[name]
        comparison[name]={'provider':provider_metrics(ss),'full_evidence_bytes':statistics([len(packed(s['evidence_package'])) for s in ss]),
            'view_bytes':statistics([len(packed(s['prediction_context'][key])) for s in ss]),
            'request_bytes':statistics([x['bytes'] for s in ss for x in s['request_sizes'] if x['phase']=='prediction-1']),
            'policy_execution_seconds':statistics([s['policy_execution']['elapsed_seconds'] for s in ss]),
            **{k:report[k] for k in ('delivery','api','acquisition','domains','outcomes','Top1','Top3','context_limit_frequency','cost_safety_stop_races')}}
    supervisor=read(out/'supervisor-audit.json');assert supervisor['completed'] and supervisor['manual_scientific_interventions']==0
    nxt=next_policy(out,initial);assert exact(nxt['updater_session_usage'])==exact(run['updater_session_usage'])
    usage={'current_session_API_estimate_usd':run['total_api_estimate'],'logical_requests':run['total_logical_requests'],
        'provider_attempts':sum(v['provider_attempts'] for v in run['api'].values()),'by_phase':run['api'],
        'shared_warmup':reports['original_warmed_v2']['warmup'],
        'shared_warmup_lifecycle_API_estimate_usd':run['total_api_estimate']+reports['original_warmed_v2']['warmup']['api_cost_usd_estimate'],
        'note':'Recorded response-cost estimates only. Failed-attempt actual charges are unknown; reservations are exposure, not billed cost. No warmup was rerun.'}
    settings=read(out/'provider.json')
    result={'run_identity':{'protocol':run['protocol'],'run_id':out.name,'race_day':'2024-01-06','provider':'DeepInfra','model':settings['model'],
        'P0_hash':P0_HASH,'representation':VERSION,'source_snapshot_hash':file_hash(out/'frozen-run-sources.tar.gz'),
        'started_utc':supervisor['started_utc'],'ended_utc':supervisor['ended_utc'],'experiment_elapsed_seconds':status['elapsed_seconds']},
        'offline_gate':read(out/'readable-offline-gate.json'),'offline_statistics':read(offline/'replay/replay.json')['statistics'],
        'delivery':{'planned':24,'attempted':24,'sealed':24,'released':24,'hard_integrity_failures':0,**run['delivery']},
        'comparison':comparison,'usage':usage,'next_policy':nxt,'supervisor':supervisor,
        'integrity':{'semantic_pass':24,'full_packages_intact':True,'frame_exact_from_full':True,'provenance_receipts_verified':True,
            'release_barrier_verified':True,'historical_files_unchanged':len(historical),'source_files_unchanged':len(manifest['protected']),
            'config_hashes':manifest['files'],'scorer_hashes':{k:v for k,v in manifest['protected'].items() if 'scor' in k},
            'source_hashes':manifest['protected'],'per_race':per_race},'day2_executed':0,'full_cohort_started':False}
    result['output_behavior']={name:{k:metrics['provider'][k] for k in ('input_tokens','output_tokens','aggregate_output_input_ratio','latency_seconds','api_estimate_usd')} for name,metrics in comparison.items()}
    cur=comparison['readable_v2']['provider'];v1=comparison['compact_v1']['provider']
    result['output_behavior_diagnostic']={'vs_compact_v1_output_token_change_percent':100*(cur['output_tokens']['sum']/v1['output_tokens']['sum']-1),
        'vs_compact_v1_mean_latency_change_percent':100*(cur['latency_seconds']['mean']/v1['latency_seconds']['mean']-1),
        'output_and_latency_both_lower_than_v1':cur['output_tokens']['sum']<v1['output_tokens']['sum'] and cur['latency_seconds']['mean']<v1['latency_seconds']['mean'],
        'interpretation':'Single matched-input Day1 description only; stochastic generation, provider service time and retries prevent a causal superiority claim.'}
    (out/'readable-day1-summary.json').write_text(json.dumps(result,indent=2))
    (out/'ReadableCompactEvidence-v2-Day1-Full-Report.md').write_text(render(result))
    return result


def render(result):
    parts=['# ReadableCompactEvidence-v2 Day1 Full Report','',
        '24/24 sealed and legally released. Frame and updater completed. Day2 = 0; full324 not started. Raw checkpoints and provider transcripts remain on Mini.',
        '', '## A. Run identity','', '```json',json.dumps(result['run_identity'],indent=2),'```',
        '', '## B. Offline gate','',
        '24/24 exact frozen replay, unchanged full state/accounting/Frame and protected history. Full regression and source hashes:',
        '```json',json.dumps({k:v for k,v in result['offline_gate'].items() if k!='source_hashes'},indent=2),'```',
        '', '## C. Delivery','', '```json',json.dumps(result['delivery'],indent=2),'```',
        '', '## D–H. Three-version acquisition, representation, provider behavior and quality','',
        '| Metric | Original warmed v2 | Compact v1 | Readable v2 |','|---|---:|---:|---:|']
    names=['original_warmed_v2','compact_v1','readable_v2'];c=result['comparison']
    def add(label,fn):
        vals=[fn(c[n]) for n in names]
        parts.append('| '+label+' | '+' | '.join(f'{v:,.6f}' if isinstance(v,float) else str(v) for v in vals)+' |')
    for label,key in [('Tool calls/race','mean_tool_calls_per_race'),('Evidence cells/race','mean_evidence_cells_per_race'),('Raw research cost/race','mean_research_cost_per_race')]:
        add(label,lambda x,k=key:x['acquisition'][k])
    for metric in ('full_evidence_bytes','view_bytes','request_bytes','policy_execution_seconds'):
        for stat in ('mean','median','p90','p95','max'):add(metric+' '+stat,lambda x,m=metric,s=stat:x[m][s])
    add('Minimum request headroom bytes',lambda x:262144-x['request_bytes']['max'])
    for metric in ('input_tokens','output_tokens','latency_seconds','api_estimate_usd'):
        for stat in ('sum','mean','median','p90','p95','max'):add(metric+' '+stat,lambda x,m=metric,s=stat:x['provider'][m][s])
    for key in ('aggregate_output_input_ratio','logical_requests','provider_attempts','retry_count'):
        add(key,lambda x,k=key:x['provider'][k])
    for key in ('context_limit_frequency','cost_safety_stop_races','Top1','Top3'):add(key,lambda x,k=key:x[k])
    for key in ('winner_rank','log_reward','B_contribution','gross_reward','aligned_reward'):
        for stat in ('sum','mean','median','max'):add(key+' '+stat,lambda x,k=key,s=stat:x['outcomes'][k][s])
    parts += ['', 'All versions use identical warmed P0 and semantic input. Research fees, domain calls, coverage and cells were compared race by race; any raw research cost delta is exactly explained by interpreter CPU metering. The scorer and its 0.5 research multiplier are unchanged.',
        'Output lengths are provider completion tokens. Latencies include normal transport retries. API costs are estimates of recorded responses; failed-attempt charges remain unknown and are not replaced by reservation amounts.',
        '', '### Output-behavior diagnostic','', '```json',json.dumps(result['output_behavior_diagnostic'],indent=2),'```',
        'The boolean above states whether BOTH output tokens and mean latency were lower than Compact v1 in this run. It is a descriptive answer to the decoding-burden hypothesis, not proof of a causal improvement. Quality metrics are from one day only.',
        '', '### Full domain calls, coverage, cells and research cost','', '```json',json.dumps({n:{'acquisition':c[n]['acquisition'],'domains':c[n]['domains']} for n in names},indent=2),'```',
        '', '### Provider failures, per-race lengths and latency','', '```json',json.dumps({n:c[n]['provider'] for n in names},indent=2),'```',
        '', '### Complete-session phase accounting and shared historical warmup','', '```json',json.dumps(result['usage'],indent=2),'```',
        'New artifacts keep model_usage as an alias of model_usage_at_save with scope through_save_research_policy. Grand totals use complete builder/updater sessions including finish. Reused P0 costs are historical provenance, not new builder charges.',
        '', '## I. Updater and next policy','', '```json',json.dumps(result['next_policy'],indent=2),'```',
        'Validated-but-uncommitted proposals are not adopted policies. Complete source, diff and hash appear above for every proposal and the adopted policy. No Day2 races were run.',
        '', '## J. Supervisor audit','', '```json',json.dumps(result['supervisor'],indent=2),'```',
        'The detached local supervisor monitors control/integrity state only, never scientific quality. It does not send provider requests, modify checkpoints, or restart experiments. No per-race operator approvals are required.',
        '', '## K. Integrity and source snapshots','', '```json',json.dumps(result['integrity'],indent=2),'```',
        '', 'Runtime source archive: frozen-run-sources.tar.gz. Config and manifest, raw checkpoints, provider transcripts, view digests, supervisor heartbeat log and machine-readable readable-day1-summary.json are retained. No historical report was overwritten.','']
    return '\n'.join(parts)

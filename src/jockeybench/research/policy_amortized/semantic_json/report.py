"""Derived daily/final reports; only legally released races have outcome metrics."""
import difflib
import json
from collections import Counter
from pathlib import Path
from jockeybench.canonical import digest, file_hash, packed
from jockeybench.clock import release
from ..policy_builder import write
from ..policy_contract import source_hash
from ..frame import extract, make_frame
from ..report import usage, split, rate
from ..model_usage import session_totals
from ..readable_evidence.report import statistics
from .gate import load_checkpoint, retries
from .runtime import verify_semantics
from .view import audit_package, semantic_view, exact, VERSION


def read(path):return json.loads(Path(path).read_text())


def day_states(out,day):
    m=read(out/'manifest.json');flat=[(b['day'],r) for b in m['schedule'] for r in b['races']]
    return [load_checkpoint(out/'runs'/('PAR-'+str(i+1).zfill(3))/'checkpoint.json.gz') for i,(d,r) in enumerate(flat) if d==day]


def provider(states):
    result=usage(states)
    result['retries']=sum(retries(s.get('provider_attempts',[])) for s in states)
    for field,key in [('prompt_tokens','input_tokens'),('completion_tokens','output_tokens')]:
        result[key+'_per_request']=statistics([o.get(field) for s in states for o in s.get('model_outputs',[])])
    result['latency_seconds']=statistics([r.get('logical_request_elapsed_seconds') for s in states for r in s.get('llm_trajectory',[])])
    result['failed_attempts_actual_charge_unknown']=[{'session':s.get('run_id'),'request_phase':a.get('phase'),**{k:a[k] for k in ('request_id','status','http_status','elapsed_seconds','reserved_cost_usd') if k in a}}
        for s in states for a in s.get('provider_attempts',[]) if a['status']!='complete']
    result['cost_note']='Recorded-response USD estimates; failed-attempt charges unknown. Reservations are not billed charges.'
    return result


def representation(states):
    sizes=[x['bytes'] for s in states for x in s.get('request_sizes',[]) if x['phase'].startswith(('prediction-','finalization-'))]
    return {'full_evidence_bytes':statistics([len(packed(s['evidence_package'])) for s in states if 'evidence_package' in s]),
        'semantic_json_bytes':statistics([len(packed(semantic_view(s['evidence_package']))) for s in states if 'evidence_package' in s]),
        'request_bytes':statistics(sizes),'headroom_bytes':statistics([262144-x for x in sizes]),
        'context_limit_races':sum(s.get('graceful_trigger')=='context_limit' for s in states),
        'cost_safety_stop_races':sum(bool(s.get('funding_stop')) for s in states)}


def outcomes(rows):
    return {'Top1':rate(rows,'top1_hit'),'Top3':rate(rows,'top3_hit'),
        **{k:statistics([r.get(k) for r in rows]) for k in ('winner_rank','log_reward','B_contribution','gross_reward','research_cost','aligned_reward','aligned_net')}}


def diff(before,after):
    return ''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile='previous',tofile='next'))


def policy_for(out,h):
    records=[read(p) for p in sorted((out/'policies').glob('research_policy_v*.json'))]
    return next(p for p in reversed(records) if p['policy_hash']==h)


def policy_record(record):
    validation=record['validation_result']
    cases=validation.get('cases',[])
    formula=None
    if len(cases)>=2:
        a,b=cases[:2];slope=(b['tool_calls']-a['tool_calls'])/(b['field_size']-a['field_size'])
        intercept=a['tool_calls']-slope*a['field_size']
        if all(c['tool_calls']==slope*c['field_size']+intercept for c in cases):
            formula={'expression':f'{slope:g} * field_size + {intercept:g}',
                     'basis':'Fits deterministic synthetic validation cases; not a proof for untested field sizes.'}
    return {**record,'tool_call_formula_diagnostic':formula,'source_hash_verified':source_hash(record['source'])==record['policy_hash']}


def updater_record(out,day,current,nxt):
    s=read(out/('policy_update-'+day+'.json'));proposals=[];actions=[]
    saved={p['policy_hash'] for p in [read(f) for f in (out/'policies').glob('research_policy_v*.json')] if p['created_after_day']==day}
    for o in s.get('model_outputs',[]):
        for call in o['message'].get('tool_calls',[]):
            f=call['function'];actions.append(f['name']);args=json.loads(f['arguments'])
            if f['name'] in ('validate_research_policy','save_research_policy') and 'source' in args:
                h=source_hash(args['source'])
                valid=any(e.get('valid') and e.get('policy_hash')==h for e in s.get('validation_events',[]))
                proposals.append({'hash':h,'source':args['source'],'diff':diff(current['source'],args['source']),
                    'status':'saved' if h in saved else 'validated-but-uncommitted' if valid else 'not_accepted'})
    return {'actions':actions,'proposals':proposals,'adopted_hash':nxt['policy_hash'],
            'complete_session_usage':session_totals([s],'updater'),'finish_received':s.get('updater_session_usage',{}).get('finish_received')}


def daily(out,day):
    out=Path(out);frame=read(out/('frame-'+day+'.json'));ss=day_states(out,day)
    if any(s.get('release_ledger',{}).get(s['schedule'][0]['races'][0])!=release(day) for s in ss):
        raise ValueError('Outcome report requires complete legal day release')
    active=policy_for(out,frame['policy_identity']['policy_hash'])
    candidate=read(out/('next-policy-'+day+'.json'));nxt=policy_for(out,candidate['policy_hash'])
    m=read(out/'manifest.json');idx=next(i for i,b in enumerate(m['schedule']) if b['day']==day)
    prev=policy_for(out,read(out/('frame-'+m['schedule'][idx-1]['day']+'.json'))['policy_identity']['policy_hash']) if idx else active
    result={'day':day,'races':len(ss),'active_policy':policy_record(active),'active_policy_diff_from_previous_day':diff(prev['source'],active['source']),
        'next_policy':policy_record(nxt),'next_policy_diff':diff(active['source'],nxt['source']),
        'acquisition':frame['acquisition'],'domains':frame['domains'],
        'representation':representation(ss),'provider':provider(ss),'prediction_provider':provider(split(ss,'prediction-')),
        'repair_provider':provider(split(ss,'finalization-')),'delivery':dict(Counter(d['delivery'] for s in ss for d in s.get('aligned_delivery',{}).values())),
        'outcomes':outcomes(frame['per_race']),'updater':updater_record(out,day,active,nxt),
        'observed_association':frame['observed_association'],
        'graceful_records':[{'race_id':s['schedule'][0]['races'][0],'trigger':s.get('graceful_trigger'),'delivery':s.get('aligned_delivery'),
                            'request_sizes':s.get('request_sizes',[])} for s in ss if s.get('graceful_trigger')]}
    write(out/('daily-summary-'+day+'.json'),result)
    compact={k:v for k,v in result.items() if k not in ('active_policy','next_policy','updater')}
    compact.update(active_policy_hash=active['policy_hash'],next_policy_hash=nxt['policy_hash'],updater_usage=result['updater']['complete_session_usage'])
    (out/('Daily-'+day+'.md')).write_text('# SemanticJSON-Light daily log: '+day+'\n\n```json\n'+json.dumps(compact,indent=2)+'\n```\n')
    return result


def finalize(out):
    from .lifecycle import verify_frozen, P0_HASH, PROTOCOL
    out=Path(out);m=read(out/'manifest.json');verify_frozen(out,m)
    status=read(out/'execution-status.json');supervisor=read(out/'supervisor-audit.json');gate=read(out/'five-race-gate.json')
    assert status['finished'] and not status['errors'] and status['attempted']==324 and supervisor['completed'] and gate['passed']
    assert status['completed_days']==[b['day'] for b in m['schedule']]
    assert gate['outcomes_inspected'] is False and gate['release_count']==0 and gate['updater_calls']==0
    initial=read(out/'initial-policy.json');assert source_hash(initial['source'])==initial['policy_hash']==P0_HASH
    refs=read(out/'comparison-source.json');offline=Path(refs['offline_directory'])
    protected=read(offline/'historical-before.json');assert all(file_hash(Path(k))==v for k,v in protected.items())
    assert file_hash(out/'provider.json')==file_hash(Path(refs['directory'])/'provider.json')
    current_config=read(out/'config.json');old_config=read(Path(refs['directory'])/'config.json')
    assert {k:v for k,v in current_config.items() if k!='managed_root'}=={k:v for k,v in old_config.items() if k!='managed_root'}
    history=[];days=[];all_states=[];audit_rows=[]
    for b in m['schedule']:
        day=b['day'];ss=day_states(out,day);frame=read(out/('frame-'+day+'.json'))
        policy=policy_for(out,frame['policy_identity']['policy_hash']);rows=[]
        for s in ss:
            proof=audit_package(s['evidence_package']);semantic=verify_semantics(s)
            assert s['policy_identity']['policy_hash']==policy['policy_hash']
            rows.append(extract(s,policy,release(day)))
            audit_rows.append({'run_id':s['run_id'],**proof,'semantic_audit':semantic})
        assert exact(frame)==exact(make_frame(rows,history,day,policy,release(day),b['races']))
        history.extend(rows);all_states.extend(ss);days.append(read(out/('daily-summary-'+day+'.json')))
    records=[read(p) for p in sorted((out/'policies').glob('research_policy_v*.json'))]
    assert all(p['model_usage']==p['model_usage_at_save'] and p['model_usage_scope']=='through_save_research_policy' for p in records)
    sessions=[read(p) for p in sorted(out.glob('policy_update*.json'))]
    shared=read(Path(refs['directory'])/'v2-smoke-report.json')['warmup']
    updater=session_totals(sessions,'updater');new_provider=provider(all_states+sessions)
    aggregate={'tool_calls':sum(r['tool_calls'] for r in history),'evidence_cells':sum(r['evidence_cells'] for r in history),
        'research_cost':sum(r['research_cost'] for r in history),'tool_calls_per_race':statistics([r['tool_calls'] for r in history]),
        'domains':{d:{'calls':sum(r['domains'][d]['calls'] for r in history),'evidence_cells':sum(r['domains'][d]['evidence_cells'] for r in history),
            'raw_research_cost':sum(r['domains'][d]['raw_research_cost'] for r in history),'horse_coverage':statistics([r['domains'][d]['horse_coverage'] for r in history])}
            for d in days[0]['domains']}}
    baseline=read(Path(refs['readable_directory'])/'readable-day1-summary.json')['comparison']
    comparison={k:{f:v[f] for f in ('provider','request_bytes','Top1','Top3','outcomes','acquisition','domains','delivery')} for k,v in baseline.items()}
    comparison['semantic_json_day1']={k:days[0][k] for k in ('provider','prediction_provider','representation','outcomes','acquisition','domains','delivery')}
    result={'A_protocol':{'protocol':PROTOCOL,'representation':VERSION,'run_id':out.name,'manifest':m,'elapsed_seconds':status['elapsed_seconds']},
        'B_P0_warmup':{'initial_policy':initial,'reuse':read(out/'initial-reuse.json'),'shared_historical_warmup':shared,'warmup_reexecuted':False},
        'C_five_race_gate':gate,'D_completion':{'planned':324,'attempted':len(all_states),'sealed':sum(bool(s['submissions']) for s in all_states),
            'released':sum(bool(s['release_ledger']) for s in all_states),'days':status['completed_days']},
        'E_policy_evolution':[{k:d[k] for k in ('day','active_policy','active_policy_diff_from_previous_day','next_policy','next_policy_diff','updater','domains')} for d in days],
        'F_acquisition':{'whole_horizon':aggregate,'by_day':[{k:d[k] for k in ('day','acquisition','domains')} for d in days]},
        'G_representation_context':representation(all_states),'H_provider':{'all_new_sessions':new_provider,'prediction':provider(split(all_states,'prediction-')),
            'repair':provider(split(all_states,'finalization-')),'updater_complete_sessions':updater,'new_builder_requests':0},
        'I_prediction_metrics':{'total':outcomes(history),'by_day':[{k:d[k] for k in ('day','outcomes')} for d in days]},
        'J_adaptation':{'interpretation':'Descriptive associations only. Stochastic outputs and provider variation prevent causal claims.',
            'by_day':[{k:d[k] for k in ('day','observed_association','acquisition','domains')} for d in days]},
        'K_amortized_costs':{'shared_warmup_API_usd':shared['api_cost_usd_estimate'],'shared_warmup_weighted_research_cost':shared['weighted_ledger_cost'],
            'updater_API_usd':updater['estimated_cost_usd'],'new_run_API_usd':new_provider['estimated_cost_usd'],
            'lifecycle_API_usd':new_provider['estimated_cost_usd']+shared['api_cost_usd_estimate'],
            'warmup_plus_updater_API_per_race':(shared['api_cost_usd_estimate']+updater['estimated_cost_usd'])/324,
            'warmup_weighted_research_cost_per_race':shared['weighted_ledger_cost']/324,
            'scope':'Complete sessions through finish; at-save model_usage is provenance only; historical warmup counted once.'},
        'L_graceful':{'delivery':dict(Counter(d['delivery'] for s in all_states for d in s.get('aligned_delivery',{}).values())),
            'records':[r for d in days for r in d['graceful_records']]},
        'M_supervisor':supervisor,'N_integrity':{'semantic_pass':len(audit_rows),'frames_exact':10,'historical_files_unchanged':len(protected),
            'source_files_unchanged':len(m['protected']),'config_hashes':m['files'],'scorer_hashes':{k:v for k,v in m['protected'].items() if 'scor' in k},
            'runtime_archive_hash':file_hash(out/'frozen-run-sources.tar.gz'),'per_race':audit_rows},
        'O_day1_comparison':comparison}
    write(out/'semantic-324-summary.json',result)
    text=['# SemanticJSON-Light-v1 324 Full Report','','324/324 completed on the same trajectory. Engineering gate inspected no outcomes. Historical artifacts remain unchanged.','']
    for key,value in result.items():text.extend(['## '+key.replace('_',' '),'','```json',json.dumps(value,indent=2),'```',''])
    text.extend(['Raw checkpoints, provider transcripts, Frames, policies, daily summaries, frozen sources, manifest and supervisor logs are retained on Mini. Comparisons are descriptive, not causal.',''])
    (out/'SemanticJSON-Light-v1-324-Full-Report.md').write_text('\n'.join(text))
    return result

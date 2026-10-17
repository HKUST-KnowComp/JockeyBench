"""Historical support builder; its analysis sandbox is separate from saved policy."""
import collections,copy,json,time
from pathlib import Path
from types import FunctionType
from . import transport
from .policy_builder import Registry,toolset,write
from .policy_contract import CONTRACT,validate_source,PolicyInvalid,synthetic_cards,SyntheticSDK,interpret
from jockeybench.research.policy_warmup.archive import load,support_types,CLOCK,ORIGIN,HORIZON
from jockeybench.research.policy_amortized.model_usage import summarize,capture_session

READ={'history','card','horse_history','training','statistics','coverage','form','results','connections','ledger','status','describe'}
CUSTOM={'inspect_contract','validate_research_policy','save_research_policy','finish'}
PROMPT=('Build a reusable information-acquisition program using legal historical support. '
    'Improve future information acquisition efficiency, not horse outcome prediction. '
    'The program will be reused for future races. Inspect the contract and schema; explore historical evidence and released outcomes; '
    'use compute to analyze it; validate and save an acquisition-only program, then finish. '
    'Historical labels are for analysis only: the saved program has no results, prediction or submit capability. '
    'No predictive parameters, auxiliary files or analysis workspace are transferred to the runtime. '
    'Do not guess unavailable facts. Use sdk.call(name, **arguments) inside compute; assign its final output to result. '
    'Each compute starts fresh; only its own flat workspace persists. No network or external file access. '
    'All data and compute are metered. No prebuilt acquisition policy is supplied. ')

def tools(mod):
    return [t for t in mod('economic_service').economic_specs(8,'objective-v5','A')
            if t['function']['name'] in READ|{'compute'}]+toolset()

def service_type(mod):
    class SupportService(mod('economic_service').EconomicService):
        def call(self,name,arguments=None,from_sdk=False,operation_id=None):
            from .source_binding import ensure
            ensure(self)
            if self.state['clock']!=CLOCK or self.state['phase']!='warmup':raise mod('common').Rejected('Frozen support session')
            if name not in READ|{'compute'} or (from_sdk and name=='compute'):
                raise mod('common').Rejected('Unavailable support capability')
            return super().call(name,arguments,from_sdk,operation_id)
        def dispatch(self,name,args,meter=None):
            if name=='describe':
                return {'tools':tools(mod),'runtime_contract':CONTRACT,'tariff':self.config.tariff.public(),
                    'clock':CLOCK,'relative_day_origin':ORIGIN,'support':'All eligible reviewed historical races before origin; no formal cards or outcomes.',
                    'compute':'sdk.call(tool, **arguments); Python/numpy/scipy/sklearn if installed; result must be JSON serializable.',
                    'limits':self.config.public()['limits'],'schema':mod('schema').catalog('objective-v5','economic-v1')}
            return super().dispatch(name,args,meter)
    return SupportService

def accounting(state,mod=None,*,reconcile=None):
    entries=state['ledger']['entries'];categories={}
    for e in entries:categories[e['category']]=categories.get(e['category'],0)+e['amount_micro']/1e6
    outputs=state.get('model_outputs',[]);api=sum(o.get('cost_usd') or 0 for o in outputs)
    weighted=sum(categories.values())
    return {'currency':'benchmark score units; API separately in USD','weighted_ledger_cost':weighted,
        'raw_ledger_cost':weighted/.5,'weighted_compute_cost':categories.get('compute',0),
        'weighted_data_tool_cost':weighted-categories.get('compute',0),'categories':categories,
        'logical_requests':len(state.get('llm_trajectory',[])),'provider_attempts':len(state.get('provider_attempts',[])),
        'api_cost_usd_estimate':api,'unknown_outputs':sum(o.get('cost_usd') is None for o in outputs),
        'unknown_attempts':sum(a['status']!='complete' for a in state.get('provider_attempts',[])),
        'input_tokens':sum(o.get('prompt_tokens') or 0 for o in outputs),'output_tokens':sum(o.get('completion_tokens') or 0 for o in outputs),
        'latency_seconds':sum(r['logical_request_elapsed_seconds'] for r in state.get('llm_trajectory',[])),
        'warmup_weighted_cost_per_324':weighted/324,'warmup_api_usd_per_324':api/324,
        'harmonized_150_units_per_usd':weighted+api*150,
        'ledger_reconciled':(reconcile or mod('ledger').reconcile)(state),
        'compute_settled':all(j['status']=='settled' for j in state.get('compute_jobs',[]))}

def build(out,profile,caller=None,*,backend=None):
    """Build P0 with the frozen archive, or an explicitly supplied public backend.

    The default still verifies the historical archive. The public backend owns
    its separate version, boundary, tool schema, source, and provider contract.
    """
    out=Path(out);start=time.monotonic()
    if backend is None:
        mod=load(profile)
        config=mod('config').Config(**{**profile['config'],'source':profile['source'],'python':profile['python'],
            'managed_root':str(out/'warmup-runs'),'limits':mod('config').Limits(**profile['config']['limits']),
            'tariff':mod('config').Tariff(**profile['config']['tariff'])})
        settings=profile['provider'];agent=mod('agents').CompatibleAgent(settings)
        source=support_types(mod)(config.source,config.limits.cache_mib)
        run_type=mod('runner').Run;service_class=service_type(mod);tool_specs=tools(mod)
        rejected=mod('common').Rejected;reconcile=mod('ledger').reconcile
        default_request=FunctionType(transport.request.__code__,{**transport.request.__globals__,'provider':transport.checked_provider(mod('provider'))})
        clock,origin,horizon=CLOCK,ORIGIN,HORIZON
        lineage={'archive_code_hash':profile['archive_code_hash']}
    else:
        config,settings,agent=backend.config,backend.settings,backend.agent
        source=backend.source;run_type=backend.run_type;service_class=backend.service_class
        tool_specs=backend.tools;rejected=backend.rejected;reconcile=backend.reconcile
        default_request=backend.request
        clock,origin,horizon=backend.clock,backend.origin,backend.horizon
        lineage=backend.lineage
    source_started=time.process_time();verified_manifest=source.manifest();source_cpu=time.process_time()-source_started
    if verified_manifest!=profile['source_manifest']:source.close();raise PolicyInvalid('Historical source fingerprint mismatch')
    original_manifest=source.manifest
    def verified_initial_manifest():
        source.verify();return copy.deepcopy(verified_manifest)
    source.manifest=verified_initial_manifest
    try:run=run_type(config,'historical-support',agent,source=source)
    finally:source.manifest=original_manifest
    from .source_binding import establish
    establish(run,verified_manifest,source_cpu)
    s=run.state
    if s.get('llm_trajectory'):run.close();raise PolicyInvalid('No warmup resume')
    service=service_class(source,config,s,run.persist)
    service._verified_source_epoch=run._verified_source_epoch
    service.executor=run.executor;run.service=service;run.executor.service=service
    registry=Registry(out/'policies');accepted=None;validated=set();inspected=False;errors=[];finish_received=False
    messages=[{'role':'system','content':PROMPT+CONTRACT},{'role':'user','content':json.dumps({
        'task':'Construct initial acquisition policy from legal historical support.',
        'support_clock':clock,'relative_day_origin':origin,'formal_horizon':horizon,
        'objective':{'B':100,'research_cost_multiplier':.5,'score_scale':100},
        'tariff':config.tariff.public(),'limits':config.public()['limits'],'normal_requests':config.limits.warmup_model_calls-config.limits.repair_model_calls,'repair_reserve':config.limits.repair_model_calls})}]
    from .warmup_context_fit import fit
    # The backend selects the trusted provider without changing global imports.
    request=caller or default_request
    trusted_request=request
    def request(service,settings,body,phase):
        try:fitted=fit(body,service.state,phase)
        finally:service.persist()
        return trusted_request(service,settings,fitted,phase)
    normal=config.limits.warmup_model_calls-config.limits.repair_model_calls
    try:
        run.preflight()
        for turn in range(min(config.limits.warmup_model_calls,config.limits.model_turns)):
            if turn==normal:
                if accepted is not None:break
                s['repair_session']=True;messages.append({'role':'user','content':'Normal build allowance ended without a valid saved acquisition policy. Use the bounded repair reserve to validate and save, then finish.'})
            message=request(service,settings,transport.body(settings,messages,tool_specs),f'warmup-{turn+1}')
            s['usage']['model_calls']=s['usage'].get('model_calls',0)+1;messages.append(message)
            calls=message.get('tool_calls') or []
            if not calls:
                if accepted is not None:break
                messages.append({'role':'user','content':'No valid program saved. Inspect, validate and save an acquisition-only program.'});continue
            finished=False
            for call in calls:
                name=call['function']['name']
                try:
                    args=json.loads(call['function']['arguments'])
                    if name=='inspect_contract' and args=={}:inspected=True;reply={'contract':CONTRACT}
                    elif name in {'validate_research_policy','save_research_policy'} and set(args)=={'source'}:
                        if not inspected:raise PolicyInvalid('Inspect contract first')
                        reply=validate_source(args['source']);h=reply['policy_hash']
                        if name=='validate_research_policy':validated.add(h)
                        else:
                            if h not in validated:raise PolicyInvalid('Validate exact source before saving')
                            accepted=registry.save(args['source'],None,None,summarize(s,'through_save_request','warmup'))
                            reply={'saved':True,'policy_hash':accepted['policy_hash']}
                    elif name=='finish' and args=={}:
                        if accepted is None:raise PolicyInvalid('No valid initial policy')
                        finished=True;finish_received=True;reply={'finished':True}
                    elif name in READ|{'compute'}:reply=service.call(name,args,operation_id='warmup-'+call['id'])
                    else:raise PolicyInvalid('Unavailable warmup tool')
                except (PolicyInvalid,rejected,ValueError,KeyError,TypeError) as exc:
                    reply={'error':type(exc).__name__,'detail':str(exc)[:300]}
                s.setdefault('warmup_actions',[]).append({'turn':turn+1,'tool':name,'reply':copy.deepcopy(reply) if name in CUSTOM else {'recorded_in_tool_audit':True}})
                messages.append({'role':'tool','tool_call_id':call['id'],'content':json.dumps(reply)});run.persist()
            if finished:break
    except Exception as exc:
        errors.append({'type':type(exc).__name__,'message':str(exc)[:400]})
    finally:
        try:
            if source.manifest()!=profile['source_manifest']:errors.append({'type':'SourceChanged','message':'Historical source identity changed'})
        except Exception as exc:errors.append({'type':'SourceVerification','message':str(exc)[:300]})
        session_usage=capture_session(s,'builder','warmup',finish_received)
        s['warmup_unpruned_messages']=copy.deepcopy(messages);s['warmup_finished']=True;run.persist()
        write(out/'warmup-context-audit.json',{'guard_bytes':262144,
            'requests':s.get('warmup_context_raw_requests',[]),'evictions':s.get('warmup_context_evictions',[])})
        write(out/'warmup-unpruned-transcript.json',{'messages':messages,'all_tool_observations_preserved':True})
        cost=backend.accounting(s) if backend is not None else accounting(s,reconcile=reconcile)
        write(out/'warmup-accounting.json',{**cost,'builder_session_usage':session_usage,'elapsed_seconds':time.monotonic()-start,'errors':errors})
        write(out/'warmup-session.json',{'builder_session_usage':session_usage,'llm_trajectory':s.get('llm_trajectory',[]),'provider_attempts':s.get('provider_attempts',[]),'model_outputs':s.get('model_outputs',[])})
        run.close()
    if errors or accepted is None or not cost['compute_settled']:raise PolicyInvalid('Warmup failed; no formal races authorized')
    if any(x['clock']!=clock for x in s['audit']):raise PolicyInvalid('Warmup clock drift')
    write(out/'warmup-lineage.json',{'policy_hash':accepted['policy_hash'],'parent':None,'support_cutoff':clock,'session_id':'historical-support',
        **lineage,'total_session_accounting':cost,'builder_session_usage':session_usage,'saved_artifact_accounting_scope':'through_save_request'})
    calls=accepted['validation_result']['cases']
    domains=[]
    for card in synthetic_cards():
        package=interpret(accepted['source'],card,SyntheticSDK(card))
        domains.append({'entrants':len(card['runners']),'domains':dict(collections.Counter(x['domain'] for x in package['observations']))})
    historical_tools=dict(collections.Counter(x['tool'] for x in s['audit'] if x['status']=='ok'))
    write(out/'warmup-p0-audit.json',{'policy_hash':accepted['policy_hash'],'validation':accepted['validation_result'],
        'synthetic_domain_usage':domains,'historical_tool_usage':historical_tools,'source_verified':True,
        'general_call_formula':'Not asserted automatically: source may branch on card facts. Exact 1/5/18 synthetic counts supplied.'})
    (out/'Warmup-P0-Audit.md').write_text('# Warmup P0 audit\n\n```python\n'+accepted['source']+'```\n\n'+
        'Policy hash: '+accepted['policy_hash']+'\n\nSynthetic execution: '+json.dumps(calls)+'\n\nDomain usage: '+json.dumps(domains)+'\n\nHistorical tool usage: '+json.dumps(historical_tools)+'\n\n'+
        'Only the unchanged four-domain acquisition contract is executable. Domain calls and any call formula are determined by this source; no fitted predictor or workspace is transferred.\n\n'+
        'Support cutoff: '+str(clock)+'; complete session cost (including finish):\n```json\n'+json.dumps(cost,indent=2)+'\n```\n')
    return accepted

"""Terminal-only matched descriptive comparison from saved artifacts."""
import collections,csv,gzip,json,math
from pathlib import Path
from jockeybench.canonical import digest, file_hash
from jockeybench.clock import release
from jockeybench.research.common_scoring import checkpoint_components
from jockeybench.research.policy_amortized.semantic_json.report import provider
from .frame import extract,make_frame
from .statistics import aggregate
from .marginal_cost import audit_owned_cache
from .policy_contract import source_hash
from .policy_builder import write

DISCLAIMER='Same-P0 single matched descriptive comparison. Fresh predictions; no randomization or repeat-based uncertainty estimate. This does not establish a causal benefit of daily adaptation.'

def read(p):return json.loads(Path(p).read_text())
def inventory(p):return {f.relative_to(p).as_posix():file_hash(f) for f in sorted(p.rglob('*')) if f.is_file()}
def csvw(p,rows):
    keys=list(dict.fromkeys(k for r in rows for k in r))
    with p.open('x',newline='') as f:
        w=csv.DictWriter(f,keys);w.writeheader()
        w.writerows({k:json.dumps(v,sort_keys=True) if isinstance(v,(dict,list)) else v for k,v in r.items()} for r in rows)

def collect(out,label,initial_hash,shared_warmup):
    m=read(out/'manifest.json');status=read(out/'execution-status.json')
    assert Path(read(out/'config.json')['managed_root']).resolve()==out/'runs'
    assert status['finished'] and not status['errors'] and status['attempted']==324
    assert read(out/'five-race-gate.json')['passed']
    frozen=label=='Frozen-P0';history=[];states=[];days=[];evolution=[];components=[]
    expected=[(b['day'],r) for b in m['schedule'] for r in b['races']]
    records=[read(p) for p in (out/'policies').glob('research_policy_v*.json')]
    audits={r['run_id']:r for r in read(out/'race-reports.json')}
    initial=read(out/'initial-policy.json');assert initial['policy_hash']==initial_hash==source_hash(initial['source'])
    assert len(list((out/'runs').glob('PAR-*/checkpoint.json.gz')))==324
    index=0;cumulative_gross=0;cumulative_cost=0;deliveries=collections.Counter()
    for batch in m['schedule']:
        frame=read(out/('frame-'+batch['day']+'.json'));identity=frame['policy_identity']
        policy=next(p for p in records if p['policy_hash']==identity['policy_hash'] and p['policy_version']==identity['policy_version'])
        if frozen:assert policy==initial
        current=[];cost=0;gross=0;daystates=[]
        for race in batch['races']:
            index+=1;s=json.loads(gzip.decompress((out/'runs'/('PAR-'+str(index).zfill(3))/'checkpoint.json.gz').read_bytes()))
            assert s['schedule']==[{'day':batch['day'],'races':[race]}]
            assert s['release_ledger']=={race:release(batch['day'])}
            # Scientific Config.binding intentionally omits the managed path.
            if 'managed_root' in s['binding']['config']:
                assert Path(s['binding']['config']['managed_root']).resolve()==out/'runs'
            assert s['checkpoint_digest']==digest({k:v for k,v in s.items() if k!='checkpoint_digest'})
            assert s['policy_identity']['policy_hash']==policy['policy_hash']
            assert source_hash(s['policy_source'])==policy['policy_hash']
            audit_owned_cache(s,complete=s.get('policy_execution',{}).get('success',False))
            assert audits[s['run_id']]['semantic_audit']['semantic_json_equality']=='PASS'
            c=checkpoint_components(s);row=extract(s,policy,release(batch['day']))
            # JSON artifact depth-distribution keys are strings; compare the
            # canonical serialized record, preserving native int keys for aggregate.
            assert digest(row)==digest(audits[s['run_id']]['row'])
            current.append(row);components.append(c);states.append(s);daystates.append(s)
            cost+=c['cost']['weighted_research_cost'];gross+=c['common_reward_booked'];deliveries.update([row['delivery']])
        regenerated=make_frame(current,history,batch['day'],policy,release(batch['day']),batch['races'])
        assert digest(frame)==digest(regenerated)
        history.extend(current);cumulative_gross+=gross;cumulative_cost+=cost
        fg=aggregate([r['finegrained'] for r in current])
        day={'arm':label,'day':batch['day'],'races':len(current),'Top1':sum(r['top1_hit'] for r in current if r['top1_hit'] is not None)/len(current),
             'Top3':sum(r['top3_hit'] for r in current if r['top3_hit'] is not None)/len(current),
             'gross':gross,'weighted_cost':cost,'raw_cost':cost/.5,
             'acquisition_weighted_cost':sum(v['weighted_cost'] for v in fg['domains'].values()),
             'cumulative_formal_net':cumulative_gross-cumulative_cost,
             'cumulative_matched_net_including_common_P0':cumulative_gross-cumulative_cost-shared_warmup,
             'policy_version':policy['policy_version'],'policy_hash':policy['policy_hash'],
             'history_mean_final_depth':fg['domains']['recent_history']['mean_final_depth'],
             'training_calls':fg['domains']['training']['calls'],'condition_stats_calls':fg['domains']['condition_stats']['calls'],
             **{k:fg['accounting'][k] for k in fg['accounting']},'prediction_api':provider(daystates)}
        days.append(day)
        next_policy=read(out/('next-policy-'+batch['day']+'.json'))
        update_path=out/('policy_update-'+batch['day']+'.json')
        update=read(update_path) if update_path.exists() else {}
        evolution.append({'arm':label,'day':batch['day'],'active_version':policy['policy_version'],'active_hash':policy['policy_hash'],
            'next_version':next_policy['policy_version'],'next_hash':next_policy['policy_hash'],
            'updater_requests':len(update.get('llm_trajectory',[])),
            'decision':'frozen_no_updater' if frozen else 'saved' if update.get('saved') else 'legal_keep' if update.get('updater_lifecycle_checkpoint',{}).get('passed') else 'error_retention',
            'updater_lifecycle_passed':update.get('updater_lifecycle_checkpoint',{}).get('passed')})
    updates=[read(p) for p in sorted(out.glob('policy_update-*.json'))]
    if frozen:assert not updates and len(records)==1 and not (out/'warmup-session.json').exists()
    else:assert len(updates)==10
    fg=aggregate([r['finegrained'] for r in history]);usage=provider(states+updates)
    warm=read(out/'warmup-accounting.json') if not frozen else {}
    return {'arm':label,'races':324,'Top1':sum(bool(r['top1_hit']) for r in history)/324,'Top3':sum(bool(r['top3_hit']) for r in history)/324,
            'gross':cumulative_gross,'formal_weighted_cost':cumulative_cost,'actual_new_warmup_weighted_cost':0 if frozen else warm['weighted_ledger_cost'],
            'matched_weighted_cost_including_common_P0':cumulative_cost+shared_warmup,
            'formal_net':cumulative_gross-cumulative_cost,'matched_common_net':cumulative_gross-cumulative_cost-shared_warmup,
            'provider':usage,'prediction_provider':provider(states),'updater_provider':provider(updates),
            'actual_new_API_USD_estimate':usage['estimated_cost_usd']+(0 if frozen else warm['api_cost_usd_estimate']),
            'elapsed_seconds':status['elapsed_seconds'],'delivery':dict(deliveries),'days':days,'evolution':evolution,
            'finegrained':fg,'graceful_triggers':dict(collections.Counter(s.get('graceful_trigger') or 'none' for s in states)),
            'policy_versions':len(records),'saved_initial_hash':initial_hash,'frames_regenerated':10}

def plots(folder,arms):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    colors=['#197B71','#B87935'];labels=[a['arm'] for a in arms]
    fig,axes=plt.subplots(1,3,figsize=(12,4))
    for ax,k,title in zip(axes,['gross','matched_weighted_cost_including_common_P0','matched_common_net'],['Gross','Weighted cost incl. common P0','Common net incl. common P0']):
        ax.bar(labels,[a[k] for a in arms],color=colors);ax.set_title(title);ax.set_ylabel('Benchmark score-yen');ax.axhline(0,color='#64748B',lw=.7)
    fig.suptitle('Single same-P0 matched descriptive comparison');fig.tight_layout()
    fig.savefig(folder/'matched-economics.png',dpi=180);fig.savefig(folder/'matched-economics.pdf');plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(11,4))
    for a,color in zip(arms,colors):
        x=list(range(1,11));axes[0].plot(x,[d['acquisition_weighted_cost'] for d in a['days']],'-o',label=a['arm'],color=color)
        axes[1].plot(x,[d['cumulative_matched_net_including_common_P0'] for d in a['days']],'-o',label=a['arm'],color=color)
    for ax,title in zip(axes,['Daily acquisition cost','Cumulative common net incl. common P0']):
        ax.set_title(title);ax.set_xlabel('Race day');ax.set_ylabel('Benchmark score-yen');ax.set_xticks(range(1,11));ax.legend()
    fig.tight_layout();fig.savefig(folder/'daily-cost-and-net.png',dpi=180);fig.savefig(folder/'daily-cost-and-net.pdf');plt.close(fig)
    fig,axes=plt.subplots(1,3,figsize=(12,4))
    for a,color in zip(arms,colors):
        for ax,k in zip(axes,['history_mean_final_depth','training_calls','condition_stats_calls']):ax.plot(range(1,11),[d[k] for d in a['days']],'-o',label=a['arm'],color=color)
    for ax,title in zip(axes,['History final depth','Training logical actions','Condition stats logical actions']):ax.set_title(title);ax.set_xlabel('Race day');ax.legend()
    fig.tight_layout();fig.savefig(folder/'acquisition-behavior.png',dpi=180);fig.savefig(folder/'acquisition-behavior.pdf');plt.close(fig)
    fig,ax=plt.subplots(figsize=(8,4))
    for a,color in zip(arms,colors):
        versions=[int(e['active_version'][1:]) for e in a['evolution']]
        ax.step(range(1,11),versions,where='mid',label=a['arm'],color=color)
        ax.scatter(range(1,11),versions,color=color)
    ax.set_xlabel('Race day');ax.set_ylabel('Executed saved policy version');ax.set_title('Executed policy evolution; single matched pair');ax.set_xticks(range(1,11));ax.legend()
    fig.tight_layout();fig.savefig(folder/'policy-evolution.png',dpi=180);fig.savefig(folder/'policy-evolution.pdf');plt.close(fig)

def finalize(out):
    from .lifecycle import verify_frozen
    out=Path(out);m=read(out/'manifest.json');verify_frozen(out,m)
    original=Path(read(out/'matched-r1-bindings.json')['R1'])
    before=read(out/'private-original-inventory.json');assert inventory(original)==before
    warm=read(original/'warmup-accounting.json')['weighted_ledger_cost']
    arms=[collect(out,'Frozen-P0',m['reused_initial_policy_hash'],warm),collect(original,'Adaptive-R1',m['reused_initial_policy_hash'],warm)]
    assert read(original/'manifest.json')['schedule']==m['schedule']
    assert inventory(original)==before
    folder=out/'matched-comparison';folder.mkdir(exist_ok=False)
    summary={'comparison':DISCLAIMER,'B':100,'multiplier':.5,'API_USD_in_common_net':False,
             'shared_historical_P0_weighted_cost':warm,'warmup_allocation':'Shared historical P0 allocated once to each arm in matched totals; Frozen incurred zero new warmup cost.',
             'original_R1_preserved':True,'original_inventory_digest':digest(before),'arms':arms}
    write(folder/'matched-summary.json',summary)
    csvw(folder/'headline.csv',[{k:a[k] for k in ('arm','races','Top1','Top3','gross','formal_weighted_cost','matched_weighted_cost_including_common_P0','matched_common_net','actual_new_API_USD_estimate','elapsed_seconds','policy_versions')} for a in arms])
    csvw(folder/'daily-cost-and-net.csv',[d for a in arms for d in a['days']])
    csvw(folder/'policy-evolution.csv',[e for a in arms for e in a['evolution']])
    domains=[]
    for a in arms:
        for domain,v in a['finegrained']['domains'].items():domains.append({'arm':a['arm'],'domain':domain,**{k:v[k] for k in ('logical_policy_actions','native_acquisition_operations','newly_acquired_cells','local_projections','weighted_cost')},'mean_final_depth':v.get('mean_final_depth'),'final_depth_distribution':v.get('final_depth_distribution')})
    csvw(folder/'acquisition-domain-comparison.csv',domains)
    lines=['# Frozen-v3 vs matched Adaptive-v3 R1','',DISCLAIMER,'',
        'Scorer common-score-yen-v2-b100-ungated-v1; B=100, multiplier=0.5. API USD is reported separately. Shared historical P0 cost is allocated once per arm; Frozen performed no new warmup.','',
        '| Arm | N | Top1 | Top3 | Gross | Weighted cost incl. P0 | Common net | New API USD estimate | Runtime hours |',
        '| --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for a in arms:lines.append(f"| {a['arm']} | 324 | {a['Top1']:.2%} | {a['Top3']:.2%} | {a['gross']:.2f} | {a['matched_weighted_cost_including_common_P0']:.2f} | {a['matched_common_net']:.2f} | {a['actual_new_API_USD_estimate']:.6f} | {a['elapsed_seconds']/3600:.2f} |")
    lines+=['','Both runs passed their outcome-blind engineering gates. All 324 saved checkpoints were audited for ledger, actual request semantics, legally released outcomes and regenerated Frames. Original R1 inventory unchanged. Frozen retains exact v000 on all 10 days with zero updater calls. Predictions were newly executed in isolated run storage.','',
            'Adaptive R1 includes its recorded Day10 error retention; this is not a legal keep or a completed updater lifecycle. No rerun or omission is made. Provider metadata includes request/attempt counts, retries, tokens, estimated USD and latency; failed-attempt charges remain unknown.','',
            'See headline.csv, daily-cost-and-net.csv, acquisition-domain-comparison.csv and policy-evolution.csv. No mid-run outcome-based admission, stopping or adjustment.','',
            '![Economics](matched-economics.png)','','![Daily costs and net](daily-cost-and-net.png)','','![Acquisition behavior](acquisition-behavior.png)','','![Policy evolution](policy-evolution.png)']
    (folder/'Frozen-v3-vs-Adaptive-R1-Report.md').write_text('\n'.join(lines)+'\n')
    plots(folder,arms)
    write(folder/'read-only-audit.json',{'passed':True,'original_unchanged':inventory(original)==before,'provider_calls':0,'tool_reruns':0,'policy_reruns':0,'source_inventory_digest':digest(before)})
    return summary

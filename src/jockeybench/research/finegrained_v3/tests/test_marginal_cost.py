"""Native tariff over in-memory fixtures only; no model or real DB calls."""
import copy,csv,json,os
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
import pytest
from jockeybench.research.adaptive.test_adaptive import source,config,service,active,run
from jockeybench.research.runner import basic_card
from jockeybench.research.finegrained_v3.marginal_cost import MarginalAcquisition
from jockeybench.research.finegrained_v3.domains import legacy_fetch as fetch
from jockeybench.research.adaptive.domains import project
from jockeybench.clock import cutoff, release
from jockeybench.errors import Rejected
from jockeybench.canonical import digest
from jockeybench.protocol.metering import example_cells
from jockeybench.ledger import reconcile, fee


@pytest.fixture(autouse=True)
def absolutely_offline():
    with (patch('urllib.request.urlopen',side_effect=AssertionError('NO PROVIDER')),
          patch('socket.socket.connect',side_effect=AssertionError('NO NETWORK'))):
        yield


@pytest.fixture
def prepared(run):
    # Extend the existing tiny memory fixture to exactly five earlier starts.
    for i,day in enumerate(('2023-11-27','2023-11-28','2023-11-29')):
        rid='marginal-support-'+str(i)
        run.source.con.execute('INSERT INTO races SELECT ?,?, ?, ?, ?, context FROM races WHERE id=?',
            (rid,day,cutoff(day),release(day)-3600,release(day)+5*86400,'raw0'))
        run.source.con.execute('INSERT INTO entries SELECT ?,number,horse_id,jockey_id,trainer_id,pre,history,settlement,? FROM entries WHERE race_id=?',
            (rid,release(day)+5*86400,'raw0'))
        run.source.con.execute('INSERT INTO expanded_race_ids VALUES(?,?)',(rid,run.source.token('race',rid)))
    run.source.con.commit()
    run.config=replace(run.config,tariff=replace(run.config.tariff,multiplier=.5))
    run.service.config=run.config;run.source.projection_version=run.config.projection
    race=run.state['schedule'][0]['races'][0]
    run._test_precard=copy.deepcopy(run.state)
    raw=run.service.call('card',{'race_id':race})
    run.state['active_initial']={'card':basic_card(raw),'receipt':raw['_cost']}
    return run


def clone(run):
    r=copy.copy(run);r.state=json.loads(json.dumps(run.state));r.service=copy.copy(run.service)
    r.__dict__.pop('_verified_source_epoch',None)
    r.service.state=r.state;r.persist=lambda:None
    return r


def totals(run):
    rows=run.state.get('marginal_acquisition',{}).get('logical_actions',[])
    return {k:sum(r['receipt'][k] for r in rows) for k in ('charged_cells','call_micro','data_micro','total_micro')}


def baseline(run,depth):
    return fetch(run,{'type':'query','domain':'recent_history','horse_number':'01','depth':depth},
        run.state['schedule'][0]['races'][0])


@pytest.mark.parametrize('depth',[1,3,5])
def test_one_shot_depth_native_cost_and_full_evidence(prepared,depth):
    a=MarginalAcquisition(prepared);row=a.call('recent_history','01',depth)
    v,c,_,_=baseline(clone_initial(prepared),depth)
    assert row['observation']==v and len(v['starts'])==depth
    assert row['receipt']['data_micro']==c['data_micro']
    assert row['receipt']['total_micro']==c['total_micro']
    assert row['receipt']['total_micro']==fee(prepared.config.tariff,'call_yen')+fee(prepared.config.tariff,'field_yen',row['receipt']['charged_cells'])
    assert a.verify()['complete_evidence']


def clone_initial(run):
    r=clone(run);s=r.state
    # Initial card is the shared mandatory acquisition, excluded from scenarios.
    s.pop('marginal_acquisition',None)
    s['audit']=[s['audit'][0]];s['economic_operations']={};s['economic_sequence']=1
    s['read_digests']={s['audit'][0]['read_key']:[s['audit'][0]['data_digest']]}
    ids={s['audit'][0]['operation']+':call',s['audit'][0]['operation']+':data'}
    entries=[e for e in s['ledger']['entries'] if e['id'] in ids]
    s['ledger']={'entries':entries,'ids':{e['id']:i for i,e in enumerate(entries)},
        'cost_micro':sum(e['amount_micro'] for e in entries),'revenue_micro':0}
    s['cursors']={};s['usage']['calls']=1;s['usage']['evidence_cells']=s['audit'][0]['charged_cells']
    return r


@pytest.mark.parametrize('depths,gap_calls',[((1,3),1),((1,3,5),2),((3,5),1)])
def test_progressive_cells_equal_one_shot_but_total_has_native_call_gap(prepared,depths,gap_calls):
    a=MarginalAcquisition(prepared)
    for d in depths:a.call('recent_history','01',d)
    one_view,one,_,_=baseline(clone_initial(prepared),depths[-1]);t=totals(prepared)
    assert a.data['logical_actions'][-1]['observation']==one_view
    assert t['charged_cells']==one['charged_cells'] and t['data_micro']==one['data_micro']
    assert t['total_micro']==one['total_micro']+gap_calls*fee(prepared.config.tariff,'call_yen')
    h=a.data['histories']['01']
    assert [len(p['response']['examples']) for p in h['pages']]==[depths[0]]+[b-a for a,b in zip(depths,depths[1:])]
    assert len({e['card']['race_id'] for p in h['pages'] for e in p['response']['examples']})==depths[-1]
    assert a.verify()['duplicate_data_purchase'] is False


def test_repeated_depth3_no_backend_no_fee_and_logical_provenance(prepared):
    a=MarginalAcquisition(prepared);a.call('recent_history','01',3);before=totals(prepared);n=len(prepared.state['audit'])
    r=a.call('recent_history','01',3)
    assert totals(prepared)==before and len(prepared.state['audit'])==n
    assert r['receipt']['total_micro']==0 and r['new_native_operations']==[]
    assert len(a.data['logical_actions'])==2 and len(a.data['histories']['01']['pages'])==1
    a.verify()


@pytest.mark.parametrize('first,last',[(1,3),(3,5)])
def test_checkpoint_resume_expands_only_new_starts(prepared,first,last):
    a=MarginalAcquisition(prepared);r=a.call('recent_history','01',first,logical_id='before-crash')
    resumed=clone(prepared);b=MarginalAcquisition(resumed);before=totals(resumed);n=len(resumed.state['audit'])
    assert b.call('recent_history','01',first,logical_id='before-crash')==r
    assert totals(resumed)==before and len(resumed.state['audit'])==n
    row=b.call('recent_history','01',last)
    assert len(b.data['histories']['01']['pages'][-1]['response']['examples'])==last-first
    _,one,_,_=baseline(clone_initial(resumed),last)
    assert totals(resumed)['data_micro']==one['data_micro']
    assert row['receipt']['charged_cells']==sum(example_cells(e) for e in b.data['histories']['01']['pages'][-1]['response']['examples'])
    b.verify()


def test_native_paid_delivery_crash_window_is_idempotent(prepared):
    a=MarginalAcquisition(prepared);original=a._native
    def interrupted(*args):
        result=original(*args);raise RuntimeError('crash after paid native commit before broker commit')
    a._native=interrupted
    with pytest.raises(RuntimeError):a.call('recent_history','01',1)
    ledger=copy.deepcopy(prepared.state['ledger']);n=len(prepared.state['audit'])
    resumed=clone(prepared);b=MarginalAcquisition(resumed);b.call('recent_history','01',1)
    assert resumed.state['ledger']==ledger and len(resumed.state['audit'])==n
    b.call('recent_history','01',3);b.verify()


def test_sparse_one_start_no_expansion_reads_or_charges(prepared):
    prepared.source.con.execute("DELETE FROM entries WHERE horse_id='horse5' AND race_id NOT IN ('raw0','raw2','raw3','raw4','raw5','raw6','raw7')")
    prepared.source.con.commit();a=MarginalAcquisition(prepared)
    one=a.call('recent_history','05',1);n=len(prepared.state['audit']);cost=totals(prepared)
    for d in (3,5):assert len(a.call('recent_history','05',d)['observation']['starts'])==1
    assert len(prepared.state['audit'])==n and totals(prepared)==cost
    assert cost['call_micro']==fee(prepared.config.tariff,'call_yen')
    a.verify()


def test_condition_stats_after_full_history_is_local_aggregation(prepared):
    a=MarginalAcquisition(prepared);h=a.call('recent_history','01',5);before=totals(prepared);n=len(prepared.state['audit'])
    c=a.call('condition_stats','01')
    assert c['observation']['sample_starts']==5 and c['receipt']['total_micro']==0
    assert c['native_sources']==h['native_sources'] and totals(prepared)==before and len(prepared.state['audit'])==n
    a.verify()


def test_condition_stats_after_shallow_history_buys_only_missing_four(prepared):
    a=MarginalAcquisition(prepared);a.call('recent_history','01',1);c=a.call('condition_stats','01')
    assert len(a.data['histories']['01']['pages'][-1]['response']['examples'])==4
    assert c['observation']['sample_starts']==5
    _,one,_,_=baseline(clone_initial(prepared),5)
    assert totals(prepared)['data_micro']==one['data_micro']
    assert totals(prepared)['total_micro']==one['total_micro']+fee(prepared.config.tariff,'call_yen')
    a.verify()


def test_condition_multiple_horses_are_separate_target_history(prepared):
    a=MarginalAcquisition(prepared)
    for n in ('01','02'):a.call('condition_stats',n)
    pages=[h['pages'][0] for h in a.data['histories'].values()]
    assert len(pages)==2 and all(len(e['card']['runners'])==1 for p in pages for e in p['response']['examples'])
    assert totals(prepared)['charged_cells']==sum(sum(example_cells(e) for e in p['response']['examples']) for p in pages)
    a.verify()


def test_training_explicit_race_acquisition_then_free_selected_projections(prepared):
    a=MarginalAcquisition(prepared);first=a.call('training','01');n=len(prepared.state['audit']);cost=totals(prepared)
    response=a.data['training']['page']['response']
    assert len(response['records'])==5
    assert first['receipt']['charged_cells']==1+sum(1+len(r['values']) for r in response['records'])
    assert first['receipt']['charged_cells']>len(first['observation']['training'])
    for number in ('02','03'):assert a.call('training',number)['receipt']['total_micro']==0
    assert len(prepared.state['audit'])==n and totals(prepared)==cost
    b=MarginalAcquisition(clone(prepared));assert b.call('training','04')['receipt']['total_micro']==0
    assert len(b.run.state['audit'])==n;b.verify()


def test_original_training_and_condition_repeat_rule_is_exact_native_read(prepared):
    race=prepared.state['schedule'][0]['races'][0]
    _,x,_,_=fetch(prepared,{'domain':'training','horse_number':'01'},race)
    _,y,_,_=fetch(prepared,{'domain':'training','horse_number':'02'},race)
    assert x['charged_cells']>0 and y['charged_cells']==0
    assert y['call_micro']==fee(prepared.config.tariff,'call_yen')
    baseline(prepared,5)
    _,c,_,_=fetch(prepared,{'domain':'condition_stats','horse_number':'01'},race)
    assert c['charged_cells']==0 and c['data_micro']==0 and c['call_micro']>0
    reconcile(prepared.state)


def test_initial_card_already_contains_workouts_but_native_training_has_other_read_identity(prepared):
    race=prepared.state['schedule'][0]['races'][0]
    card=prepared.service.dispatch('card',{'race_id':race},[])
    training=prepared.service.call('training',{'race_id':race})
    assert [r['training'] for r in card['runners']]==[r['values'] for r in training['records']]
    assert training['_cost']['charged_cells']>0 and 'training' not in str(prepared.state['active_initial']['card'])


def test_checkpoint_tamper_release_and_unsupported_depth_stop(prepared):
    a=MarginalAcquisition(prepared);a.call('recent_history','01',1)
    broken=clone(prepared);broken.state['marginal_acquisition']['histories']['01']['examples'][0]['labels']['01']['finish']=99
    with pytest.raises(Rejected,match='digest'):MarginalAcquisition(broken)
    for d in (0,2,6,True):
        with pytest.raises(Rejected):a.call('recent_history','01',d)
    prepared.state['release_ledger']={'anything':1}
    with pytest.raises(Rejected,match='release'):a.call('recent_history','01',3)


def test_no_hidden_backend_reads_for_cached_actions(prepared):
    a=MarginalAcquisition(prepared);a.call('recent_history','01',5);a.call('training','01')
    with (patch.object(prepared.source,'card',side_effect=AssertionError('hidden card read')),
          patch.object(prepared.source,'history_page',side_effect=AssertionError('hidden history read')),
          patch.object(prepared.service,'call',side_effect=AssertionError('hidden service read'))):
        a.call('recent_history','01',5);a.call('condition_stats','01');a.call('training','02')


def test_native_replay_is_read_only_and_does_not_rebuy(prepared):
    a=MarginalAcquisition(prepared)
    for d in (1,3,5):a.call('recent_history','01',d)
    a.call('condition_stats','01');a.call('training','01');a.call('training','02')
    before=digest(prepared.state);a.verify()
    assert digest(prepared.state)==before


def test_native_training_cannot_accept_per_horse_request(prepared):
    with pytest.raises(Rejected,match='arguments'):
        prepared.service.call('training',{'race_id':prepared.state['schedule'][0]['races'][0],'number':'01'})


def test_emit_native_validation_csv_and_launch_equality_checks(prepared):
    rows=[];checks={}
    cases=[('one-shot 1',(1,)),('one-shot 3',(3,)),('one-shot 5',(5,)),('1→3',(1,3)),
        ('1→3→5',(1,3,5)),('3→5 from checkpoint replay',(3,5)),('repeated depth3',(3,3))]
    for name,depths in cases:
        run=clone_initial(prepared);a=MarginalAcquisition(run)
        for i,d in enumerate(depths):
            if i and 'checkpoint' in name:run=clone(run);a=MarginalAcquisition(run)
            r=a.call('recent_history','01',d)
        t=totals(run);one=baseline(clone_initial(prepared),depths[-1])[1]
        checks[name]=t['total_micro']==one['total_micro']
        rows.append({'tool':'recent_history','scenario':name,'logical_calls':len(depths),
            'backend_reads':len(a.data['histories']['01']['pages']),'new_evidence_units':t['charged_cells'],
            'final_starts':len(r['observation']['starts']),'raw_cost':t['total_micro']/1e6/run.config.tariff.multiplier,
            'weighted_cost':t['total_micro']/1e6,'duplicate_charge':False,
            'notes':'Integrated incremental native paging; units=evidence cells; original per-read call fees retained; excludes shared mandatory card and unchanged policy CPU',
            'total_equals_one_shot':checks[name]})
        a.verify()
    for domain,cases in [('training',[('native X then Y',False),('integrated race read then X,Y projections',True)]),
        ('condition_stats',[('native full history then stats',False),('integrated full history then local stats',True),
            ('native depth1 then stats',False),('integrated depth1 then stats',True)])]:
        for name,candidate in cases:
            run=clone_initial(prepared);race=run.state['schedule'][0]['races'][0]
            if candidate:
                a=MarginalAcquisition(run)
                if domain=='training':
                    a.call(domain,'01');r=a.call(domain,'02')
                else:
                    a.call('recent_history','01',1 if 'depth1' in name else 5);r=a.call(domain,'01')
                t=totals(run);n=sum(len(h['pages']) for h in a.data['histories'].values())+int(a.data['training'] is not None);a.verify()
            else:
                receipts=[]
                if domain=='training':
                    for number in ('01','02'):receipts.append(fetch(run,{'domain':domain,'horse_number':number},race)[1])
                else:
                    receipts.append(baseline(run,1 if 'depth1' in name else 5)[1]);receipts.append(fetch(run,{'domain':domain,'horse_number':'01'},race)[1])
                t={k:sum(c[k] for c in receipts) for k in ('total_micro','charged_cells')};n=2
            rows.append({'tool':domain,'scenario':name,'logical_calls':2,'backend_reads':n,
                'new_evidence_units':t['charged_cells'],'final_starts':5 if domain=='condition_stats' else '',
                'raw_cost':t['total_micro']/1e6/run.config.tariff.multiplier,'weighted_cost':t['total_micro']/1e6,
                'duplicate_charge':(domain=='condition_stats' and not candidate and 'depth1' in name),
                'notes':'training: WHOLE RACE; overlaps workouts already bought in mandatory initial card, charged under another native read identity. condition_stats: includes preceding history acquisition; shallow native read is bought again by full-five stats. Initial card cost and policy CPU excluded.',
                'total_equals_one_shot':''})
    run=clone_initial(prepared);receipts=[]
    for d in (1,3,5):receipts.append(baseline(run,d)[1])
    total=sum(c['total_micro'] for c in receipts)
    rows.append({'tool':'recent_history','scenario':'original prefix 1→3→5','logical_calls':3,'backend_reads':3,
        'new_evidence_units':sum(c['charged_cells'] for c in receipts),'final_starts':5,
        'raw_cost':total/1e6/run.config.tariff.multiplier,'weighted_cost':total/1e6,
        'duplicate_charge':True,'notes':'BEFORE: native full prefixes; start1 charged three times, starts2/3 twice; initial card and policy CPU excluded.',
        'total_equals_one_shot':False})
    assert checks['one-shot 1'] and checks['one-shot 3'] and checks['one-shot 5']
    assert not checks['1→3'] and not checks['1→3→5'] and not checks['3→5 from checkpoint replay']
    output=os.environ.get('V3_MARGINAL_VALIDATION_DIR')
    if output:
        out=Path(output);out.mkdir(exist_ok=True)
        with (out/'active-v3-marginal-cost-validation.csv').open('w',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
        (out/'marginal-launch-requirements.json').write_text(json.dumps({'verdict':'PASS for accepted marginal semantics',
            'progressive_total_equals_native_one_shot':checks,'native_tariff_changed':False,
            'training_is_per_horse_marginal':False,'formal_runtime_connected':True,'extra_native_call_fees_accepted':True,'training_semantics':'once-per-race native acquisition + local projections',
            'provider_calls':0,'P0_created':False,'rows':rows},indent=2),encoding='utf-8')

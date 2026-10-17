"""Offline only: real provider transport with a mocked HTTP boundary; no credentials."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from jockeybench.research.daily import lifecycle
from jockeybench.research.daily_session import (DailyReflectionSession,DailyReflectionFailed,load_budget,
    DAILY_REFLECTION_PROMPT,OLD_REFLECTION_SENTENCE,NEW_REFLECTION_SENTENCE,verify_session)
from jockeybench.research.adaptive.model import PROMPT,AdaptiveAgent
from jockeybench.research.adaptive.domains import neutral,tools
from jockeybench.clock import release
from jockeybench.canonical import digest

SETTINGS={'model':'XiaomiMiMo/MiMo-V2.5-Pro','base_url':'https://api.deepinfra.com/v1/openai',
 'api_key_env':'A2_OFFLINE_UNUSED_KEY','timeout_seconds':180,'output_token_field':'max_tokens',
 'temperature':0,'seed':0,'input_usd_per_million':.39,'output_usd_per_million':1.17,
 'retry_attempts':2,'retry_base_seconds':1,'retry_max_seconds':1,'reservation_output_tokens':4096,
 'max_estimated_cost_usd':.75}
BUDGET=load_budget(Path(__file__).with_name('daily_reflection_budget.json'))


def rows(day,ids):
    return [{'race_id':r,'release_clock':release(day),'final_prediction':{'ranking':['1','2','3'],
       'probabilities':{'1':.5,'2':.3,'3':.2}},'actual_result':{'records':[{'number':'1','finish':1}]},
       'prediction_score':40,'prediction_phase_cost':{'cost_yen':2},'ledger':{'cost_yen':2},
       'model_cost_so_far':{'observed_or_estimated_usd':.75},
       'domain_trajectory':[{'domain':'training','horse_number':'1'}],
       'allocation':{'domains':{'training':1}},'stopping_mode':'voluntary'} for r in ids]


class Response:
    def __init__(self,value): self.value=value
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def read(self,*args): return json.dumps(self.value).encode()


def answer(req,timeout):
    body=json.loads(req.data)
    obs=json.loads(body['messages'][1]['content'])
    if obs['mode']=='reflection':
        policy=copy.deepcopy(obs['old_policy'])
        policy.update(updated_after_race=obs['race_id'],strategy_notes='offline daily memory '+obs['day'])
        name='update_policy';arguments={'reflection':'offline fixture, all selected races','new_policy':policy}
    else:
        name='submit';arguments={'ranking':['1','2','3'],'probabilities':{'1':.5,'2':.3,'3':.2}}
    return Response({'model':SETTINGS['model'],'choices':[{'finish_reason':'tool_calls','message':
        {'role':'assistant','tool_calls':[{'id':'offline-call','type':'function',
          'function':{'name':name,'arguments':json.dumps(arguments)}}]}}],
        'usage':{'prompt_tokens':100,'completion_tokens':50,'estimated_cost':.001}})


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='A2-offline-')
        self.addCleanup(self.tmp.cleanup)
        self.out=Path(self.tmp.name)
        self.guard=patch('socket.socket.connect',side_effect=AssertionError('REAL NETWORK FORBIDDEN'))
        self.guard.start();self.addCleanup(self.guard.stop)
        self.http=patch('urllib.request.urlopen',side_effect=answer)
        self.open=self.http.start();self.addCleanup(self.http.stop)
        self.sleep=patch('jockeybench.agents.provider.time.sleep')
        self.sleep.start();self.addCleanup(self.sleep.stop)

    def session(self,day='2024-01-06',name='X'):
        return DailyReflectionSession(self.out/name,name,day,SETTINGS,BUDGET)

    def test_01_exhausted_race_budget_does_not_block(self):
        exhausted={'model_turns':40,'deadline':0,'model_usd':.75,'remaining_turns':0}
        s=self.session();r=s.run(rows('2024-01-06',['A','B']),neutral())
        self.assertEqual(exhausted['remaining_turns'],0)
        self.assertEqual(s.state['status'],'completed')
        self.assertEqual(r['new_policy']['updated_after_race'],'B')
        self.assertEqual(s.state['model_turns'],1)

    def test_02_race_accounting_unchanged(self):
        race={'model_turns':40,'prediction_turns':40,'provider_attempts':[{'status':'complete'}],
              'model_usd':.75,'completed':True,'release_ledger':{'B':release('2024-01-06')}}
        before=copy.deepcopy(race)
        s=self.session();s.run(rows('2024-01-06',['A','B']),neutral())
        self.assertEqual(before,race)
        self.assertEqual(s.state['estimated_usd'],.001)
        self.assertEqual(len(s.state['provider_attempts']),1)
        self.assertEqual(s.state['model_usage'],{'prompt_tokens':100,'completion_tokens':50})

    def test_03_failure_preserves_results_and_blocks_next_day(self):
        self.open.side_effect=OSError('offline network failure')
        completed={};sessions=[];seen=[]
        def prediction(d,r,m): seen.append(r);completed[r]={'completed':True};return r
        def released(d,sealed):
            for r in sealed: completed[r]['release']=release(d)
            return rows(d,sealed)
        def reflect(d,sealed,outcomes,m):
            s=self.session(d);sessions.append(s);return s.run(outcomes,m)['new_policy']
        with self.assertRaises(DailyReflectionFailed):
            lifecycle([{'day':'2024-01-06','races':['A','B']},{'day':'2024-01-07','races':['C']}],
                      neutral(),prediction,released,reflect,lambda *a,**k:None)
        self.assertEqual(seen,['A','B'])
        self.assertTrue(all(v['completed'] and v['release']==release('2024-01-06') for v in completed.values()))
        self.assertEqual(sessions[0].state['status'],'failed')
        self.assertNotIn('reflection',sessions[0].state)
        self.assertEqual(len(sessions[0].state['provider_attempts']),2)

    def test_04_bounded_session_local_retry(self):
        calls=[]
        def fail_once(req,timeout):
            calls.append(1)
            if len(calls)==1: raise OSError('offline first attempt')
            return answer(req,timeout)
        self.open.side_effect=fail_once
        s=self.session();s.run(rows('2024-01-06',['B']),neutral())
        self.assertEqual(len(s.state['provider_attempts']),2)
        self.assertEqual(s.state['network_retries'],1)
        self.assertEqual(s.state['model_turns'],1)
        self.assertEqual(s.state['model_cost']['unknown_attempts'],1)

    def test_05_daily_prompt_and_complete_payload(self):
        s=self.session();data=rows('2024-01-06',['A','B','C']);s.run(data,neutral())
        request=s.state['llm_trajectory'][0]['request']
        payload=json.loads(request['messages'][1]['content'])
        self.assertEqual(payload['daily_races'],data)
        self.assertEqual(DAILY_REFLECTION_PROMPT.replace(NEW_REFLECTION_SENTENCE,OLD_REFLECTION_SENTENCE),PROMPT)
        self.assertNotIn(OLD_REFLECTION_SENTENCE,request['messages'][0]['content'])
        self.assertIn("previous race day's",request['messages'][0]['content'])
        self.assertEqual(verify_session(s.state,payload)['status'],'PASS')

    def run_two_days(self):
        snapshots=[];events=[];sessions=[];actual_requests=[]
        def prediction(day,r,m):
            snapshots.append((r,copy.deepcopy(m)))
            service=type('S',(),{})()
            service.state={'run_id':'fixture-'+r,'phase':'predict','index':0,'usage':{}}
            service.persist=lambda:None
            import time
            agent=AdaptiveAgent(SETTINGS,time.monotonic()+60)
            agent.call(service,{'mode':'prediction','research_policy':m},tools(['1','2','3']),'prediction-1')
            actual_requests.append(json.loads(service.state['llm_trajectory'][0]['request']['messages'][1]['content']))
            return r
        def ref(d,sealed,outcomes,m):
            s=self.session(d);sessions.append(s);return s.run(outcomes,m)['new_policy']
        lifecycle([{'day':'2024-01-06','races':['A','B','C']},{'day':'2024-01-07','races':['D','E']}],
                  neutral(),prediction,lambda d,r:rows(d,r),ref,lambda k,**v:events.append({'kind':k,**v}))
        return snapshots,events,sessions,actual_requests

    def test_06_same_day_freeze_and_release_order(self):
        snapshots,events,sessions,requests=self.run_two_days()
        self.assertEqual(snapshots[0][1],snapshots[1][1]);self.assertEqual(snapshots[1][1],snapshots[2][1])
        self.assertEqual(snapshots[3][1],snapshots[4][1])
        for day in ['2024-01-06','2024-01-07']:
            e=[x for x in events if x['day']==day]
            self.assertEqual([x['kind'] for x in e][-2:],['release_gate','memory_commit'])
            self.assertEqual(e[-2]['clock'],release(day))
        self.assertEqual(len(sessions),2)

    def test_07_next_day_actual_request_memory(self):
        snapshots,events,sessions,requests=self.run_two_days()
        expected=sessions[0].state['reflection']['new_policy']
        self.assertNotEqual(expected,neutral())
        self.assertEqual(requests[3]['research_policy'],expected)
        self.assertEqual(requests[4]['research_policy'],expected)

    def test_08_independent_experiments(self):
        a=self.session(name='X');b=self.session(name='Y')
        a.run(rows('2024-01-06',['A']),neutral())
        self.assertEqual(b.state['model_turns'],0);self.assertEqual(b.state['errors'],[])
        self.assertEqual(b.state['provider_attempts'],[])
        b.run(rows('2024-01-06',['A']),neutral())
        self.assertNotEqual(a.state['run_id'],b.state['run_id'])
        self.assertNotEqual(a.state['llm_trajectory'][0]['request_id'],b.state['llm_trajectory'][0]['request_id'])
        self.assertEqual(len(a.state['provider_attempts']),1)
        self.assertEqual(len(b.state['provider_attempts']),1)

    def test_09_324_events_ten_daily_sessions_no_prediction_calls(self):
        dates=['2024-01-06','2024-01-07','2024-01-08','2024-01-13','2024-01-14',
               '2024-01-20','2024-01-21','2024-01-27','2024-01-28','2024-02-03']
        schedule=[{'day':d,'races':[str(i)+'-'+str(j) for j in range(24 if i<3 else 36)]} for i,d in enumerate(dates)]
        seen=[];sessions=[];events=[]
        def pred(d,r,m): seen.append(r);return r
        def ref(d,sealed,outcomes,m):
            s=self.session(d);sessions.append(s);return s.run(outcomes,m)['new_policy']
        lifecycle(schedule,neutral(),pred,lambda d,r:rows(d,r),ref,lambda k,**v:events.append((k,v)))
        self.assertEqual(len(seen),324);self.assertEqual(len(sessions),10)
        self.assertEqual(self.open.call_count,10)  # all mocked daily reflection, no prediction call
        self.assertEqual(sum(k=='release_gate' for k,v in events),10)
        self.assertEqual(sum(k=='memory_commit' for k,v in events),10)

    def test_10_malformed_reflection_has_no_memory(self):
        def malformed(req,timeout):
            response=answer(req,timeout)
            response.value['choices'][0]['message']['tool_calls'][0]['function']['arguments']='{}'
            return response
        self.open.side_effect=malformed
        s=self.session()
        with self.assertRaises(DailyReflectionFailed):s.run(rows('2024-01-06',['A']),neutral())
        self.assertNotIn('reflection',s.state)
        self.assertEqual(self.open.call_count,1)

    def test_11_early_release_rejected_before_transport(self):
        s=self.session();data=rows('2024-01-06',['A']);data[0]['release_clock']-=1
        with self.assertRaises(DailyReflectionFailed):s.run(data,neutral())
        self.assertEqual(self.open.call_count,0)

    def test_12_session_cannot_rerun(self):
        s=self.session();s.run(rows('2024-01-06',['A']),neutral())
        with self.assertRaises(Exception):s.run(rows('2024-01-06',['A']),neutral())
        self.assertEqual(self.open.call_count,1)

    def execute_fixture(self, fail=False):
        from jockeybench.research import daily
        from jockeybench.research.runner import PROJECT
        from jockeybench.canonical import file_hash
        from types import SimpleNamespace
        from contextlib import ExitStack
        day='2024-01-06'
        states={}
        class FakeRun:
            def __init__(self,config,ident,agent):
                self.config=config;self.agent=agent
                self.storage=SimpleNamespace(path=Path(config.managed_root)/ident)
                self.state=states.setdefault(ident,{'run_id':ident,'schedule':[{'day':day,'races':['A']}],
                    'completed':False,'scores':{},'submissions':{},'release_ledger':{},'binding':{},
                    'usage':{},'model_outputs':[],'provider_attempts':[]})
                self.source=SimpleNamespace(raw_race=lambda r:r,
                    con=SimpleNamespace(execute=lambda *a:SimpleNamespace(fetchone=lambda:{'day':day})))
                self.executor=SimpleNamespace()
            def persist(self): pass
            def close(self): pass
            def preflight(self): pass
        class FakeService:
            def __init__(self,source,config,state,persist):self.state=state
            def call(self,name,args):
                assert name=='results'
                assert self.state['release_ledger'][args['race_id']]==release(day)
                return {'records':[],'_cost':{'total_micro':0}}
        def pred(run,agent,memory):
            s=run.state;rid=s['schedule'][0]['races'][0]
            s.update(memory_before=copy.deepcopy(memory),model_turns=40,
                     provider_attempts=[{'status':'complete','request_id':'race'}],
                     model_outputs=[{'request_id':'race','cost_usd':.75}],
                     submission_mode='voluntary',active_trace=[],query_count=39)
            agent.deadline=0  # Last race has no remaining time or turns/provider headroom.
            s['submissions'][rid]={'prediction':{'ranking':['1','2','3'],'probabilities':{'1':.5,'2':.3,'3':.2}}}
            return rid,day
        def settle(run,rid,d):
            run.state['completed']=True;run.state['clock']=release(d)
            run.state['release_ledger'][rid]=release(d)
            run.state['scores'][rid]={'score_revenue_yen':40}
        root=PROJECT/'active_research_outputs'
        with tempfile.TemporaryDirectory(prefix='A2-offline-execute-',dir=root) as tmp:
            out=Path(tmp)
            base=PROJECT/'active_research_outputs/2026-09-27-A2-Daily-Lifecycle-Cost-Multiplier'
            config=json.loads((base/'A2-config.json').read_text());config['managed_root']=str(out/'A2-runs')
            (out/'A2-config.json').write_text(json.dumps(config))
            (out/'A2-provider.json').write_bytes((base/'A2-provider.json').read_bytes())
            (out/'A2-reflection-budget.json').write_text(json.dumps(BUDGET))
            manifest={'schedule':[{'day':day,'races':['A','B']}],'maximum_real_races':2,'protected':{},
                      'files':{'A2-reflection-budget.json':file_hash(out/'A2-reflection-budget.json')}}
            (out/'A2-manifest.json').write_text(json.dumps(manifest))
            if fail:self.open.side_effect=OSError('offline provider failure')
            with ExitStack() as stack:
                for name,value in {'Run':FakeRun,'ActiveService':FakeService,'predict':pred,'settle':settle,
                    'credential':lambda s:None,'summary':lambda *a:{'cost_yen':2},
                    'replay':lambda *a:{'mock':True},'verify_semantics':lambda *a:{'mock':True},
                    'result_report':lambda r,*a:{'run_id':r.state['run_id'],'completed':r.state['completed']},
                    'scan_no_credential':lambda *a:{'credential_scan':'offline-no-credential'}}.items():
                    stack.enter_context(patch.object(daily,name,value))
                daily.execute(out)
            status=json.loads((out/'A2-execution-status.json').read_text())
            results=json.loads((out/'A2-results.json').read_text())
            session=json.loads((out/'A2-daily-sessions'/day/'A2-session.json').read_text())
            self.assertTrue(all(r['completed'] for r in results))
            for s in states.values():
                self.assertEqual(s['model_turns'],40)
                self.assertEqual(s['model_outputs'][0]['cost_usd'],.75)
                self.assertEqual(len(s['provider_attempts']),1)
                self.assertTrue(s['completed']);self.assertTrue(s['release_ledger'])
            if fail:
                self.assertEqual(session['status'],'failed')
                self.assertEqual(status['errors'][0]['failure_code'],'daily_reflection_failed')
                self.assertFalse((out/'A2-policy.json').exists())
                self.assertEqual(status['completed_days'],[])
            else:
                self.assertEqual(session['status'],'completed')
                self.assertEqual(status['completed_days'],[day])
                self.assertTrue((out/'A2-policy.json').exists())

    def test_13_real_coordinator_exhausted_races_independent_reflection(self):
        self.execute_fixture()

    def test_14_real_coordinator_failed_reflection_keeps_completed_races(self):
        self.execute_fixture(fail=True)

    def test_15_day_api_cost_counted_once_and_unknown_not_zero(self):
        from jockeybench.research.daily_report import session_accounting
        results=[{'net_B_after_model_estimate':10},{'net_B_after_model_estimate':20}]
        sessions=[{'estimated_usd':.02,'model_cost':{'unknown_attempts':0},'status':'completed'}]
        before=copy.deepcopy(results)
        self.assertEqual(session_accounting(results,sessions,150)['experiment_net_after_model_estimate'],27)
        self.assertEqual(results,before)
        sessions[0]['status']='failed'
        sessions[0]['model_cost']['unknown_attempts']=1
        self.assertIsNone(session_accounting(results,sessions,150)['experiment_net_after_model_estimate'])
        self.assertIsNone(session_accounting(results,[{'status':'running'}],150)['experiment_net_after_model_estimate'])


if __name__=='__main__':unittest.main()

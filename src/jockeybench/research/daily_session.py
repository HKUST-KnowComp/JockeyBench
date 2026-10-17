"""Independent A2 day-level accounting; original model transport is reused."""
import copy
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from jockeybench.errors import Rejected
from jockeybench.canonical import digest
from jockeybench.clock import release
from jockeybench.agents.provider import cost_exposure
from jockeybench.research.adaptive.model import AdaptiveAgent, PROMPT, model_cost
from jockeybench.research.adaptive.domains import reflection_tool, parse_reflection
from jockeybench.research.llm.runner import wall_limit, write_json
from jockeybench.research.reflection_normalization import canonicalize_reflection

OLD_REFLECTION_SENTENCE = "In reflection mode, only the previous race's legally released result, sealed prediction, costs, semantic trajectory and old policy are supplied."
NEW_REFLECTION_SENTENCE = "In reflection mode, the previous race day's legally released results, sealed predictions, costs and semantic trajectories for all selected races, together with the old policy, are supplied."
if PROMPT.count(OLD_REFLECTION_SENTENCE) != 1:
    raise RuntimeError('Expected reflection prompt sentence changed')
DAILY_REFLECTION_PROMPT = PROMPT.replace(OLD_REFLECTION_SENTENCE, NEW_REFLECTION_SENTENCE)


class DailyReflectionFailed(Exception):
    pass


def load_budget(path):
    value = json.loads(Path(path).read_text())
    if set(value) != {'max_model_turns', 'wall_seconds', 'max_estimated_cost_usd'}:
        raise Rejected('Unexpected daily budget fields')
    if type(value['max_model_turns']) is not int or value['max_model_turns'] != 1:
        raise Rejected('Exactly one daily reflection decision')
    for key in ('wall_seconds', 'max_estimated_cost_usd'):
        if type(value[key]) not in (int,float) or not math.isfinite(value[key]) or value[key] <= 0:
            raise Rejected('Daily budget must be positive and finite')
    return value


def now():
    return datetime.now(timezone.utc).isoformat()


class DailyReflectionSession:
    """Minimal service interface needed by the existing AdaptiveAgent/provider.

    No race state, source, sandbox, data tools or protocol ledger is attached.
    Results have already been metered and released by the original service.
    """
    def __init__(self, out, experiment_id, day, settings, budget):
        self.path = Path(out)/'A2-daily-sessions'/day
        self.path.mkdir(parents=True, exist_ok=False)
        self.settings = dict(settings, max_estimated_cost_usd=budget['max_estimated_cost_usd'])
        if self.settings.get('retry_attempts') not in (1,2):
            raise Rejected('At most one existing network retry')
        self.budget = copy.deepcopy(budget)
        self.state = {'run_id': experiment_id+':daily-reflection:'+day, 'day':day,
                      'phase':'reflect','index':0,'clock':release(day),
                      'status':'created','model_turns':0,'usage':{'model_calls':0},
                      'provider_attempts':[],'model_outputs':[],'llm_trajectory':[],
                      'errors':[],'pending':None,'started_at':None,'completed_at':None,
                      'budget':self.budget,'provider_settings':self.settings,
                      'prompt_digest':digest(DAILY_REFLECTION_PROMPT)}
        self.persist()

    def persist(self):
        self.state['session_digest'] = digest({k:v for k,v in self.state.items() if k!='session_digest'})
        temp=self.path/'A2-session.tmp.json'
        write_json(temp,self.state)
        temp.replace(self.path/'A2-session.json')

    def run(self, rows, memory):
        s=self.state
        if s['status']!='created':
            raise Rejected('A daily session may never be retried or resumed')
        s.update(status='running', started_at=now())
        try:
            required={'race_id','release_clock','final_prediction','actual_result','prediction_score',
                      'prediction_phase_cost','ledger','model_cost_so_far','domain_trajectory','allocation','stopping_mode'}
            if not rows or any(not required.issubset(row) or row['release_clock']!=s['clock'] for row in rows):
                raise Rejected('Daily reflection requires all released race records')
            if len({r['race_id'] for r in rows})!=len(rows):
                raise Rejected('Duplicate reflection race')
            anchor=rows[-1]['race_id']
            payload={'mode':'reflection','race_id':anchor,'day':s['day'],
                     'old_policy':copy.deepcopy(memory),'daily_races':copy.deepcopy(rows),
                     'result_release_clock':s['clock']}
            s['input_digest']=digest(payload)
            s['race_ids']=[row['race_id'] for row in rows]
            write_json(self.path/'A2-input.json',payload)
            agent=AdaptiveAgent(self.settings,time.monotonic()+self.budget['wall_seconds'])
            s['model_turns']=1
            s['usage']['model_calls']=1
            self.persist()
            with wall_limit(self.budget['wall_seconds']):
                response=agent.call(self,payload,[reflection_tool(anchor)],'daily-reflection',
                                    system=DAILY_REFLECTION_PROMPT)
            audit=canonicalize_reflection(response,anchor)
            s['reflection_validation']=audit
            self.persist()
            if audit['validation_result']!='PASS':
                raise Rejected(audit['validation_code'])
            update=copy.deepcopy(audit['canonical_arguments'])
            s['reflection']={'old_policy':copy.deepcopy(memory),**update,'clock':s['clock'],
                             'race_id':anchor,'day':s['day'],'race_ids':s['race_ids'],
                             'input_digest':digest(payload),'output_digest':digest(update)}
            s['status']='completed'
        except BaseException as exc:
            s['status']='failed'
            s['failure_code']='daily_reflection_failed'
            error={'type':type(exc).__name__}
            if s.get('reflection_validation',{}).get('validation_result')=='FAIL':
                error.update({k:s['reflection_validation'][k] for k in
                              ('validation_code','validation_path','validation_message')})
            s['errors'].append(error)
            for attempt in s['provider_attempts']:
                if attempt['status']=='pending': attempt['status']='interrupted'
        finally:
            s['model_usage']={'prompt_tokens':sum(x.get('prompt_tokens') or 0 for x in s['model_outputs']),
                              'completion_tokens':sum(x.get('completion_tokens') or 0 for x in s['model_outputs'])}
            s['model_cost']=model_cost(s)
            s['estimated_usd']=s['model_cost']['observed_or_estimated_usd']
            exposure,unknown=cost_exposure(s)
            s['reserved_or_estimated_exposure_usd']=exposure
            s['unbounded_unknown_cost']=unknown
            s['network_retries']=max(0,len(s['provider_attempts'])-len(s['llm_trajectory']))
            s['completed_at']=now()
            self.persist()
        if s['status']!='completed':
            raise DailyReflectionFailed('daily_reflection_failed')
        return copy.deepcopy(s['reflection'])


def verify_session(state, payload):
    if state['session_digest']!=digest({k:v for k,v in state.items() if k!='session_digest'}):
        raise Rejected('Daily session digest changed')
    if state['status']!='completed' or state['model_turns']!=1 or len(state['llm_trajectory'])!=1:
        raise Rejected('Daily reflection did not complete exactly one decision')
    request=state['llm_trajectory'][0]['request']
    if request['messages'][0]['content']!=DAILY_REFLECTION_PROMPT or json.loads(request['messages'][1]['content'])!=payload:
        raise Rejected('Daily reflection prompt/payload changed')
    if digest(payload)!=state['input_digest'] or state['clock']!=release(state['day']):
        raise Rejected('Daily reflection clock/input changed')
    raw=state['llm_trajectory'][0]['response']['message']
    if 'reflection_validation' in state:
        audit=canonicalize_reflection(raw,state['race_ids'][-1])
        if audit!=state['reflection_validation'] or audit['validation_result']!='PASS':
            raise Rejected('Daily canonicalization audit differs from raw response')
        update=audit['canonical_arguments']
    else:
        update=parse_reflection(raw,state['race_ids'][-1])  # Legacy audit stays strict.
    if any(update[k]!=state['reflection'][k] for k in ('reflection','new_policy')):
        raise Rejected('Daily memory differs from native model output')
    return {'status':'PASS','model_turns':1,'race_count':len(state['race_ids'])}

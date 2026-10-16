"""Experience-only observation adapter over the unchanged active prediction loop."""
import copy,json
from jrdb_lh.common import Rejected,release,cutoff
from active_research.adaptive.model import AdaptiveAgent,PROMPT as PREDICTION_PROMPT
from active_research.adaptive.runner import verify_semantics as legacy_semantics
from .frame import PROTOCOL,SCHEMA,DOMAINS
EXPLANATION=("\nHistorical Research Experience\n"
 "These statistics summarize your previous research behavior and legally released outcomes.\n"
 "Use them as historical feedback when deciding how to research the current race.\n"
 "They are descriptive statistics, not causal guarantees.\n")
PROMPT=PREDICTION_PROMPT+EXPLANATION
FIELD='Historical Research Experience'

def validate_frame(frame,clock):
 if set(frame)!={'protocol','schema','through_day','current_day','rolling_history','trend'}:
  raise Rejected('Invalid experience frame envelope')
 if frame['protocol']!=PROTOCOL or frame['schema']!=SCHEMA:raise Rejected('Wrong experience protocol')
 if frame['through_day'] is not None and release(frame['through_day'])>clock:raise Rejected('Future experience in prediction')
 if frame['through_day'] is None:
  if frame['current_day'] is not None or frame['rolling_history'] is not None or frame['trend']:
   raise Rejected('Non-neutral initial experience')
 else:
  for part in ['current_day','rolling_history']:
   if set(frame[part]['areas'])!=set(DOMAINS):raise Rejected('Forbidden experience domain')

class ExperienceAgent(AdaptiveAgent):
 def call(self,service,observation,tools,phase_key,forced=None,system=None):
  if observation.get('mode')!='prediction' or not phase_key.startswith('prediction-'):
   raise Rejected('Experience protocol permits only per-race prediction calls')
  expected={'get_'+d for d in DOMAINS}|{'submit'}
  if {t['function']['name'] for t in tools}!=expected:
   raise Rejected('Experience permits only the frozen four tools and submit')
  obs=copy.deepcopy(observation);frame=obs.pop('research_policy')
  validate_frame(frame,service.state['clock'])
  if frame!=service.state['memory_before']:raise Rejected('Experience state mismatch')
  obs[FIELD]=frame
  return super().call(service,obs,tools,phase_key,forced=forced,system=PROMPT)

def verify_semantics(state):
 # First verify the real experience context, then reuse original evidence projection checks.
 s=copy.deepcopy(state)
 for t in s.get('llm_trajectory',[]):
  body=t['request']
  if body['messages'][0]['content']!=PROMPT:raise Rejected('Experience prompt changed')
  obs=json.loads(body['messages'][1]['content'])
  if obs.get('mode')!='prediction' or 'research_policy' in obs or FIELD not in obs:
   raise Rejected('Non-prediction or legacy policy context')
  validate_frame(obs[FIELD],cutoff(s['schedule'][0]['day']))
  if obs[FIELD]!=s['memory_before']:raise Rejected('Frozen experience changed')
  if any(k in obs for k in ('actual_result','daily_races','outcome')):raise Rejected('Outcome in prediction context')
  obs['research_policy']=obs.pop(FIELD)
  body['messages'][0]['content']=PREDICTION_PROMPT
  body['messages'][1]['content']=json.dumps(obs)
 result=legacy_semantics(s)
 result['experience_context']='PASS'
 return result

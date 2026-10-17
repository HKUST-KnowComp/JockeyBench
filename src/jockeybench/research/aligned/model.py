"""Aligned native transport, using the trusted provider's reservation/retry ledger."""
import copy
import json
import math
import time
from pathlib import Path
from jockeybench.agents import provider
from jockeybench.errors import Rejected
from jockeybench.canonical import digest, packed
from jockeybench.research.experience.model import FIELD, PROMPT as LEGACY_PROMPT, validate_frame
from jockeybench.research.experience.frame import DOMAINS
from jockeybench.research.llm.agent import cost_record, redact
from .specification import PROFILE

OLD_CONSTRAINT = ('Engineering safety ceilings: 40 total model turns, '
                  '30 minutes per race and a provider spend threshold.')
NEW_CONSTRAINT = ('Engineering safety ceilings: 40 total model turns per race, '
                  'a 262144-byte serialized request limit, '
                  'and the existing per-race provider spend threshold. '
                  'There is no cumulative per-race wall-clock deadline.')
assert LEGACY_PROMPT.count(OLD_CONSTRAINT) == 1
PROMPT = LEGACY_PROMPT.replace(OLD_CONSTRAINT, NEW_CONSTRAINT)
FINALIZATION_INSTRUCTION = ('\nResearch is now closed.\n\n'
    'Using only the race information and evidence already available in this '
    'request, submit the final prediction for this race.\n\n'
    'No further research tools are available.\n'
    'Return exactly one valid native submit action.\n')

class ContextLimit(Rejected):
    pass

def guard_body(body):
    size = len(packed(body))
    if size > PROFILE['request_body_limit_bytes']:
        raise ContextLimit('context_limit')
    return size

def validate_settings(raw):
    if isinstance(raw, dict) and raw.get('research_profile') == 'jockeybench-research-v1':
        from jockeybench.research.profile import provider_settings
        return provider_settings(raw)
    allowed = {'model','base_url','api_key_env','timeout_seconds','temperature','seed',
               'reasoning_effort','output_token_field','input_usd_per_million',
               'output_usd_per_million','max_estimated_cost_usd','retry_attempts',
               'retry_base_seconds','retry_max_seconds','reservation_output_tokens'}
    if set(raw) - allowed or raw.get('api_key_env') != 'DEEPINFRA_API_KEY':
        raise Rejected('Non-secret DeepInfra configuration required')
    if raw.get('base_url') != 'https://api.deepinfra.com/v1/openai' or raw.get('model') not in {
        'XiaomiMiMo/MiMo-V2.5-Pro','deepseek-ai/DeepSeek-V4-Flash-0731'}:
        raise Rejected('Unexpected provider/model')
    fixed = {'timeout_seconds':3600,'retry_attempts':10,'retry_base_seconds':2,
             'retry_max_seconds':300,'max_estimated_cost_usd':.75,'output_token_field':'max_tokens'}
    if any(raw.get(k) != v for k,v in fixed.items()):
        raise Rejected('Aligned provider configuration mismatch')
    for key in ('input_usd_per_million','output_usd_per_million','reservation_output_tokens'):
        value = raw.get(key)
        if type(value) not in (int,float) or not math.isfinite(value) or value <= 0:
            raise Rejected('Verified positive prices and documented reservation output bound required')
    if type(raw['reservation_output_tokens']) is not int:
        raise Rejected('Reservation output bound must be an integer')
    provider.require_prices(raw)
    return dict(raw)

def load_settings(path):
    return validate_settings(json.loads(Path(path).read_text()))

class AlignedAgent:
    # Internal compatibility sentinel for the ORIGINAL predict loop. No finite
    # deadline exists; it is never serialized or shown to the agent.
    deadline = math.inf

    def __init__(self, settings, deadline=None):
        if deadline is not None:
            raise Rejected('Aligned does not accept a race deadline')
        self.settings = validate_settings(settings)
        self.identity = {'kind':'active-research-native-llm','provider':'DeepInfra',
                         'model':settings['model'],'version':1}

    def call(self, service, observation, tools, phase_key, forced=None, system=None):
        finalization=phase_key=='finalization-1'
        if observation.get('mode') != 'prediction' or not (phase_key.startswith('prediction-') or finalization):
            raise Rejected('Only per-race prediction calls are permitted')
        expected={'submit'} if finalization else {'get_'+d for d in DOMAINS}|{'submit'}
        if {t['function']['name'] for t in tools} != expected or (finalization and len(tools)!=1):
            raise Rejected('Only the original four domains and submit are permitted')
        obs = copy.deepcopy(observation)
        frame = obs.pop('research_policy')
        validate_frame(frame, service.state['clock'])
        if frame != service.state['memory_before']:
            raise Rejected('Experience state mismatch')
        obs[FIELD] = frame
        obs['safety_remaining_wall_seconds'] = None
        body = {'model':self.settings['model'],
                'messages':[{'role':'system','content':PROMPT+FINALIZATION_INSTRUCTION if finalization else PROMPT},
                            {'role':'user','content':json.dumps(obs,ensure_ascii=False)}],
                'tools':tools,'tool_choice':{'type':'function','function':{'name':forced}} if forced else 'required',
                'parallel_tool_calls':False,'max_tokens':None,
                'temperature':self.settings.get('temperature',0)}
        for key in ('seed','reasoning_effort'):
            if key in self.settings:
                body[key] = self.settings[key]
        if redact(body) != body:
            raise Rejected('Credential contamination blocked before request')
        size = guard_body(body)
        request_id = digest([service.state['run_id'],phase_key,body])
        row = {'phase':phase_key,'request_id':request_id,'request':body,
               'request_body_bytes':size}
        service.state.setdefault('llm_trajectory',[]).append(row)
        service.persist()
        started = time.monotonic()
        try:
            result, attempt = provider.request(service,self.settings,body,request_id,phase_key)
        finally:
            row['logical_request_elapsed_seconds'] = time.monotonic()-started
            service.persist()
        raw = result['choices'][0]['message']
        message = redact({k:raw[k] for k in ('role','content','tool_calls') if k in raw})
        record = cost_record(result,request_id)
        record.update(message=message,response_model=redact(result.get('model')),
                      finish_reason=result['choices'][0].get('finish_reason'),
                      elapsed_seconds=attempt['elapsed_seconds'])
        amount = (result.get('usage') or {}).get('estimated_cost')
        if type(amount) in (int,float) and math.isfinite(amount) and amount >= 0:
            record.update(cost_usd=amount,cost_basis='provider_reported_estimate')
        elif record['prompt_tokens'] is not None and record['completion_tokens'] is not None:
            amount = (record['prompt_tokens']*self.settings['input_usd_per_million']+
                      record['completion_tokens']*self.settings['output_usd_per_million'])/1e6
            record.update(cost_usd=amount,cost_basis='configured_uncached_rate_estimate')
        else:
            record.update(cost_usd=None,cost_basis='unavailable')
        service.state.setdefault('model_outputs',[]).append(record)
        service.state['pending'] = None
        row['response'] = record
        service.persist()
        return message

    def finalize(self,service,observation,submit_tool):
        if service.state.get('finalization_repair_requests',0)>=1:
            raise Rejected('At most one finalization logical request')
        service.state['finalization_repair_requests']=1
        service.persist()
        return self.call(service,observation,[submit_tool],'finalization-1',forced='submit')

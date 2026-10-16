"""Shared trusted A2 provider transport; only payload construction is new."""
import copy
import math
import time
from jrdb_lh import provider
from jrdb_lh.common import digest, Rejected, packed
from active_research.aligned.model import guard_body, validate_settings
from active_research.llm.agent import cost_record, redact

def request(service,settings,body,phase):
    if redact(body)!=body:raise Rejected('Credential contamination')
    size=len(packed(body))
    service.state.setdefault('request_sizes',[]).append({'phase':phase,'bytes':size,'sent':size<=262144})
    service.persist();guard_body(body)
    rid=digest([service.state['run_id'],phase,body])
    row={'phase':phase,'request_id':rid,'request':copy.deepcopy(body),'request_body_bytes':size}
    service.state.setdefault('llm_trajectory',[]).append(row);service.persist();start=time.monotonic()
    try:result,attempt=provider.request(service,settings,body,rid,phase)
    finally:row['logical_request_elapsed_seconds']=time.monotonic()-start;service.persist()
    raw=result['choices'][0]['message']
    message=redact({k:raw[k] for k in ('role','content','tool_calls') if k in raw})
    record=cost_record(result,rid)
    record.update(message=message,response_model=redact(result.get('model')),
        finish_reason=result['choices'][0].get('finish_reason'),elapsed_seconds=attempt['elapsed_seconds'],phase=phase)
    amount=(result.get('usage') or {}).get('estimated_cost')
    if type(amount) in (int,float) and math.isfinite(amount) and amount>=0:
        record.update(cost_usd=amount,cost_basis='provider_reported_estimate')
    elif record['prompt_tokens'] is not None and record['completion_tokens'] is not None:
        record.update(cost_usd=(record['prompt_tokens']*settings['input_usd_per_million']+record['completion_tokens']*settings['output_usd_per_million'])/1e6,cost_basis='configured_uncached_rate_estimate')
    else:record.update(cost_usd=None,cost_basis='unavailable')
    service.state.setdefault('model_outputs',[]).append(record);service.state['pending']=None
    row['response']=record;service.persist();return message

def body(settings,messages,tools):
    return {'model':settings['model'],'messages':messages,'tools':tools,'tool_choice':'required',
        'parallel_tool_calls':False,'max_tokens':None,
        **{k:settings[k] for k in ('temperature','seed','reasoning_effort') if k in settings}}

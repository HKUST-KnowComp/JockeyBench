"""Warmup-only complete-round FIFO; no edits to retained model-visible content."""
import copy,inspect,json
from pathlib import Path
from types import FunctionType
from jrdb_lh.common import digest,packed
from active_research.aligned.model import ContextLimit
from active_research.policy_amortized.policy_builder import write
from active_research.policy_amortized import transport
from active_research.policy_warmup import builder

GUARD=262144
VERSION='historical-warmup-complete-round-FIFO-v1'

def rounds(messages):
    result=[];number=0
    for start,m in enumerate(messages):
        if m.get('role')!='assistant':continue
        end=start+1
        while end<len(messages) and messages[end].get('role')=='tool':end+=1
        calls=m.get('tool_calls') or []
        expected=[c.get('id') for c in calls]
        actual=[x.get('tool_call_id') for x in messages[start+1:end]]
        complete=bool(expected) and all(isinstance(i,str) for i in expected+actual) and len(set(expected))==len(expected) and len(actual)==len(expected) and sorted(actual)==sorted(expected)
        result.append({'round_index':number,'start':start,'end':end,'complete':complete,'digest':digest(messages[start:end])});number+=1
    return result

def fit(body,state,phase):
    """Caller supplies the entire unpruned conversation, which is never mutated.

    Previously evicted rounds stay evicted. At least the latest assistant round,
    all partial rounds, all system messages and initial task remain protected.
    A request that cannot fit without touching those protected messages fails.
    """
    raw=copy.deepcopy(body);messages=raw['messages']
    if len(messages)<2 or messages[0].get('role')!='system' or messages[1].get('role')!='user':raise ValueError('Warmup system/initial task required')
    rr=rounds(messages);latest=rr[-1]['round_index'] if rr else None
    previous=state.setdefault('warmup_evicted_rounds',{})
    for r in rr:
        if str(r['round_index']) in previous and (not r['complete'] or r['digest']!=previous[str(r['round_index'])]):raise ValueError('Evicted raw round changed')
    removed=set()
    for r in rr:
        if str(r['round_index']) in previous:removed.update(range(r['start'],r['end']))
    def request():return {**copy.deepcopy(raw),'messages':[copy.deepcopy(m) for i,m in enumerate(messages) if i not in removed]}
    fitted=request();events=[]
    for r in rr:
        if len(packed(fitted))<GUARD:break
        if not r['complete'] or r['round_index']==latest or str(r['round_index']) in previous:continue
        indices=list(range(r['start'],r['end']))
        if any(messages[i].get('role') not in ('assistant','tool') for i in indices) or r['start']<2:raise ValueError('Protected message in removable round')
        before=len(packed(fitted));before_digest=digest(fitted);removed.update(indices);previous[str(r['round_index'])]=r['digest'];fitted=request()
        event={'kind':'warmup_complete_round_eviction','phase':phase,'round_index':r['round_index'],'message_indices':indices,
            'bytes_before':before,'bytes_after':len(packed(fitted)),'round_digest':r['digest'],'request_digest_before_eviction':before_digest,
            'request_digest_after_eviction':digest(fitted)}
        events.append(event);state.setdefault('warmup_context_evictions',[]).append(copy.deepcopy(event))
    record={'phase':phase,'raw_request':raw,'raw_request_digest':digest(raw),'raw_bytes':len(packed(raw)),
        'fitted_request_digest':digest(fitted),'fitted_bytes':len(packed(fitted)),'evicted_round_indices':sorted(map(int,previous)),
        'new_eviction_count':len(events),'guard_bytes':GUARD,'headroom_bytes':GUARD-len(packed(fitted)),
        'fits':len(packed(fitted))<GUARD}
    state.setdefault('warmup_context_raw_requests',[]).append(record)
    if not record['fits']:raise ContextLimit('Warmup protected/latest/partial context cannot fit without content modification')
    return fitted

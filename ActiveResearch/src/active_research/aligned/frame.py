"""Schema adapter over original Frame extraction; no evidence/statistics rewrite."""
import copy
import json
from jrdb_lh.common import digest, Rejected
from active_research.experience.frame import extract as original_extract, make_frame
from .scoring import checkpoint_components

def extract(state,day,race,clock):
    view=copy.deepcopy(state)
    metadata=state['aligned_delivery'][race]
    # Original extract expects a terminal native submit in active_trace.
    # Repaired deliveries are native; framework submissions have an explicitly
    # labelled, transient schema projection. No invented provider response or
    # model usage is written to the real checkpoint/audit.
    if metadata['submit_phase']!='research':
        event=copy.deepcopy(state['delivery_events'][-1])
        if not event['sealed'] or event['action']['prediction']!=state['submissions'][race]['prediction']:
            raise Rejected('Delivery event differs from sealed prediction')
        if metadata['generated_by']=='framework':
            native={'tool_calls':[{'function':{'name':'submit','arguments':json.dumps(event['action']['prediction'])}}]}
            view.setdefault('llm_trajectory',[]).append({'schema_projection_only':True,
                'generated_by':'framework','response':{'message':native}})
        elif state['llm_trajectory'][-1]['phase']!='finalization-1':
            raise Rejected('Repaired delivery has no finalization request')
        event['turn']=len(view['llm_trajectory'])
        view['active_trace'].append(event)
    row=original_extract(view,day,race,clock)
    evaluated=checkpoint_components(state)
    common=next(r for r in evaluated['races'] if r['race_id']==race)
    row.update(delivery=metadata['delivery'],generated_by=metadata['generated_by'],
               stopping_state=state.get('graceful_trigger',state.get('submission_mode')),
               scoring_profile=common['evaluator'],log_reward=common['log_reward'],
               B_contribution=common['common_B_contribution'],gross_common_reward=common['common_reward'],
               common_reward=evaluated['aligned_reward'],
               reward_basis='common-aligned-delivery-v1: log + eligible bonus - actual weighted research cost',
               common_net=evaluated['common_Net'],
               source_digest=digest({k:state[k] for k in ('active_trace','ledger','scores','submissions','release_ledger')}))
    if metadata['delivery']=='execution_fallback':
        row.update(validity='fallback',fallback=True,salvaged=False)
    return row

"""Modeling-style inspect/edit/validate/save/finish session, isolated from races."""
import copy
import json
from pathlib import Path
from jockeybench.errors import Rejected
from jockeybench.canonical import digest
from jockeybench.clock import release
from jockeybench.research.adaptive.domains import native,one_call
from jockeybench.research.aligned.model import validate_settings
from .policy_contract import CONTRACT,validate_source,source_hash,PolicyInvalid
from jockeybench.research.policy_amortized.specification import PROFILE,ARMS
from . import FRAME
from .transport import body,request
from jockeybench.research.policy_amortized.model_usage import PROTOCOL as USAGE_PROTOCOL,summarize,capture_session

def write(path,value):
    path=Path(path);tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2));tmp.replace(path)

class Registry:
    def __init__(self,folder,usage_scope='through_save_request'):
        self.folder=Path(folder);self.folder.mkdir(parents=True,exist_ok=True);self.usage_scope=usage_scope
    def save(self,source,parent=None,after_day=None,usage=None):
        validation=validate_source(source)
        usage=copy.deepcopy(usage)
        if usage is not None:usage['scope']=self.usage_scope
        version='v'+str(len(list(self.folder.glob('research_policy_v*.json')))).zfill(3)
        record={'policy_version':version,'policy_hash':source_hash(source),
            'parent_policy_hash':parent['policy_hash'] if parent else None,
            'created_after_day':after_day,'source':source,'validation_result':validation,
            'usage_accounting_protocol':USAGE_PROTOCOL,
            'model_usage_scope':self.usage_scope,
            'model_usage_at_save':copy.deepcopy(usage),
            'model_usage':copy.deepcopy(usage)}
        stem=self.folder/('research_policy_'+version)
        with stem.with_suffix('.py').open('x') as f:f.write(source)
        with stem.with_suffix('.json').open('x') as f:json.dump(record,f,indent=2)
        return record

def matched_pair(policy,folder):
    if not policy['validation_result']['valid'] or source_hash(policy['source'])!=policy['policy_hash']:raise PolicyInvalid('Invalid initial policy')
    root=Path(folder);root.mkdir(parents=True,exist_ok=False)
    for arm in ARMS:
        p=root/arm;p.mkdir()
        (p/'research_policy_v000.py').write_text(policy['source'])
        write(p/'initial-policy.json',policy)
    hashes={a:source_hash((root/a/'research_policy_v000.py').read_text()) for a in ARMS}
    if len(set(hashes.values()))!=1:raise PolicyInvalid('Initial policy mismatch')
    write(root/'matched-initial-audit.json',{'hashes':hashes,'identical':True})
    return hashes

def toolset():
    return [native('inspect_contract','Read the task, allowed SDK and interpreter contract.',{},[]),
        native('validate_research_policy','Run deterministic synthetic validation; no real race data.',{'source':{'type':'string'}},['source']),
        native('save_research_policy','Validate and save a candidate. Invalid candidate retains the previous valid program.',{'source':{'type':'string'}},['source']),
        native('finish','Finish construction or keep the current saved valid policy.',{},[])]

class SessionStore:
    def __init__(self,path,phase):
        self.path=Path(path)
        if self.path.exists():raise Rejected('Existing construction session; no resume')
        self.state={'run_id':self.path.stem,'phase':phase,'index':0,'usage':{},'pending':None,'model_outputs':[],
                    'provider_attempts':[],'llm_trajectory':[],'validation_events':[]}
        self.persist()
    def persist(self):write(self.path,self.state)

def update_input(current,frame,clock):
    if frame.get('protocol')!=FRAME or clock<release(frame['day']) or frame['release_clock']!=release(frame['day']):raise Rejected('Updater release boundary')
    if frame['policy_identity']['policy_hash']!=current['policy_hash']:raise Rejected('Feedback policy mismatch')
    if frame['frame_digest']!=digest({k:v for k,v in frame.items() if k!='frame_digest'}):raise Rejected('Frame digest')
    return {'current_policy':current['source'],'Research-Program Experience Frame':frame}

def construct(folder,settings,registry,current=None,frame=None,clock=None,caller=request):
    phase='policy_update' if current else 'policy_build';settings=validate_settings(settings)
    payload={'task':'Revise the acquisition program or keep it.' if current else 'Build an initial reusable acquisition program. Start by inspecting the contract, validate candidates, save a valid program, then finish.'}
    context_audit=None
    if current:
        # Validate the original release/hash boundary before projecting feedback.
        update_input(current,frame,clock)
        from .updater_context import context
        payload,context_audit=context(current,frame,clock,folder)
    elif frame is not None:raise Rejected('Initial builder has no outcomes')
    store=SessionStore(Path(folder)/(phase+('-'+frame['day'] if frame else '')+'.json'),phase)
    if current:
        store.state['updater_context_source_audit']=context_audit
        store.state['updater_context_requests']=[]
        store.state['updater_unpruned_requests']=[]
    messages=[{'role':'system','content':'Construct deterministic information-acquisition code, not predictions. '+CONTRACT+'\nUse the provided tools in a multi-turn inspect/validate/save/finish session. Synthetic fixtures contain no real outcomes. Invalid updates retain the last valid program. Observed feedback associations are not causal contributions.'},
              {'role':'user','content':json.dumps(payload)}]
    accepted=copy.deepcopy(current);inspected=False;validated=set();saved=False
    try:
        for turn in range(PROFILE['updater_max_turns' if current else 'builder_max_turns']):
            if current:
                from .updater_context import fit
                # The complete transcript remains available even when only whole
                # optional components/old interaction rounds leave the request.
                unpruned=body(settings,copy.deepcopy(messages),toolset())
                store.state['updater_unpruned_requests'].append(unpruned)
                store.state['updater_full_transcript']=copy.deepcopy(messages)
                request_body,receipt=fit(settings,messages,toolset())
                store.state['updater_context_requests'].append({'turn':turn+1,**receipt})
                store.persist()
            else:request_body=body(settings,messages,toolset())
            message=caller(store,settings,request_body,phase+'-'+str(turn+1))
            messages.append(message)
            response={}
            try:
                name,args=one_call(message)
                if name=='inspect_contract' and not args:
                    inspected=True
                    response=({'contract_location':'canonical system message','inspection_complete':True}
                        if current else {'contract':CONTRACT})
                elif name in ('validate_research_policy','save_research_policy') and set(args)=={'source'}:
                    if not inspected:raise PolicyInvalid('Inspect contract first')
                    result=validate_source(args['source']);h=result['policy_hash']
                    if current:store.state['updater_decision_parsed']=True
                    if name=='validate_research_policy':validated.add(h);response=result
                    else:
                        if h not in validated:raise PolicyInvalid('Validate this exact source before save')
                        accepted=registry.save(args['source'],accepted,frame['day'] if frame else None,
                            summarize(store.state,'through_save_request',store.path.name))
                        saved=True;response={'saved':True,'policy_hash':accepted['policy_hash'],'policy_version':accepted['policy_version']}
                elif name=='finish' and not args:
                    if not inspected or accepted is None:raise PolicyInvalid('No initial valid program')
                    if current:
                        # A legal keep decision still resolves and validates the
                        # accepted program. No new save is required or inferred.
                        store.state['updater_decision_validation']=validate_source(accepted['source'])
                        store.state['updater_decision_parsed']=True
                    store.state.update(finished=True,accepted_policy_hash=accepted['policy_hash'],saved=saved)
                    capture_session(store.state,'updater' if current else 'builder',store.path.name,True)
                    if current:
                        from .updater_context import lifecycle_checkpoint
                        store.state['updater_lifecycle_checkpoint']=lifecycle_checkpoint(store.state)
                        store.state['updater_full_transcript']=copy.deepcopy(messages)
                    store.persist();return accepted,store.state
                else:raise PolicyInvalid('Unavailable builder tool or arguments')
            except (PolicyInvalid,Rejected,KeyError,ValueError,TypeError) as exc:
                response={'error':type(exc).__name__,'detail':str(exc),'retained_policy_hash':accepted['policy_hash'] if accepted else None}
            store.state['validation_events'].append(copy.deepcopy(response))
            calls=message.get('tool_calls') or []
            if len(calls)==1:
                messages.append({'role':'tool','tool_call_id':calls[0]['id'],'content':json.dumps(response)})
            else:
                # Malformed provider response is not a new research opportunity.
                raise PolicyInvalid('Builder requires exactly one native call')
            store.persist()
        store.state['terminal_reason']='construction_turn_limit'
    except Exception as exc:
        store.state['terminal_reason']=type(exc).__name__+': '+str(exc)
    store.state.update(finished=True,accepted_policy_hash=accepted['policy_hash'] if accepted else None,saved=saved)
    capture_session(store.state,'updater' if current else 'builder',store.path.name,False)
    if current:
        from .updater_context import lifecycle_checkpoint
        store.state['updater_lifecycle_checkpoint']=lifecycle_checkpoint(store.state)
        store.state['updater_full_transcript']=copy.deepcopy(messages)
    store.persist()
    if accepted is None:raise PolicyInvalid('initial_program_failure')
    return accepted,store.state

def revise(arm,current,frame,clock,folder,settings,registry,caller=request):
    if arm=='frozen_research_program':return current,None
    if arm!='adaptive_research_program':raise Rejected('Unknown arm')
    return construct(folder,settings,registry,current,frame,clock,caller)

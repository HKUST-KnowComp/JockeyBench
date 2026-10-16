"""Daily-reflection format-only canonicalization. No provider calls or semantic repair."""
import copy
import json
from jrdb_lh.common import Rejected
from active_research.adaptive.domains import one_call, parse_reflection

FLAT = {'reflection','domain_priorities','strategy_notes','updated_after_race'}
NESTED = {'reflection','new_policy'}
ERRORS = {
 'Invalid policy shape': ('invalid_policy_schema','$.new_policy','Policy must contain exactly the required fields.'),
 'Invalid priority domains': ('invalid_policy_schema','$.new_policy.domain_priorities','Priority domain set must match the existing schema.'),
 'Priority must be finite in [0,1]': ('invalid_priority_range','$.new_policy.domain_priorities','Priorities must be finite numbers in [0,1].'),
 'Policy notes too long': ('invalid_policy_notes','$.new_policy.strategy_notes','Notes must be a string within the existing length limit.'),
 'Policy race binding mismatch': ('invalid_race_binding','$.new_policy.updated_after_race','Policy race binding must match the released day anchor.'),
 'Reflection too long': ('invalid_reflection','$.reflection','Reflection must be a string within the existing length limit.'),
}

def canonicalize_reflection(message, race):
    audit={'version':'A2-reflection-format-v1','raw_model_response':copy.deepcopy(message),
           'canonicalized_response':copy.deepcopy(message),'canonical_arguments':None,
           'normalization':[],'status':'invalid','validation_result':'FAIL',
           'validation_code':None,'validation_path':None,'validation_message':None}
    def fail(code,path,text):
        audit.update(validation_code=code,validation_path=path,validation_message=text)
        return audit
    def save(args):
        audit['canonical_arguments']=copy.deepcopy(args)
        if audit['normalization']:
            audit['canonicalized_response']['tool_calls'][0]['function']['arguments']=json.dumps(args,ensure_ascii=False,allow_nan=False)
    try:
        name,args=one_call(message)
    except (Rejected,AttributeError,TypeError):
        return fail('invalid_action_json_or_envelope','$','Exactly one native call with valid object arguments is required.')
    if name!='update_policy':
        return fail('wrong_tool_name','$.tool_calls[0].function.name','Expected the update_policy function.')
    args=copy.deepcopy(args)
    try: json.dumps(args,allow_nan=False)
    except ValueError:
        return fail('invalid_policy_schema','$','Non-finite JSON numbers are not allowed.')
    if set(args)==FLAT:
        args={'reflection':args['reflection'],'new_policy':{k:args[k] for k in ('domain_priorities','strategy_notes','updated_after_race')}}
        audit['normalization'].append('flat_policy_to_new_policy')
    elif set(args)!=NESTED:
        audit['canonical_arguments']=copy.deepcopy(args)
        return fail('wrong_top_level_fields','$','Expected exactly reflection and new_policy, or the exact four-field flat whitelist.')
    # Materialize even failed normalization attempts for replay; JSON from one_call is already finite.
    save(args)
    policy=args.get('new_policy')
    if isinstance(policy,dict) and isinstance(policy.get('domain_priorities'),str):
        def finite(value): raise ValueError('Non-standard JSON constant')
        def unique(pairs):
            value={}
            for k,v in pairs:
                if k in value: raise ValueError('Duplicate object key')
                value[k]=v
            return value
        try:
            priorities=json.loads(policy['domain_priorities'],parse_constant=finite,object_pairs_hook=unique)
        except (ValueError,TypeError,RecursionError):
            return fail('invalid_domain_priorities_json','$.new_policy.domain_priorities','Expected one valid JSON object string; no repair is allowed.')
        if not isinstance(priorities,dict):
            return fail('invalid_domain_priorities_type','$.new_policy.domain_priorities','Parsed priorities must be an object.')
        # Reject overflow (e.g. 1e999) via original validation before serializing.
        # Serialization must not coerce or repair non-finite numbers.
        try: json.dumps(priorities,allow_nan=False)
        except ValueError:
            return fail('invalid_priority_range','$.new_policy.domain_priorities','Priorities must be finite numbers in [0,1].')
        policy['domain_priorities']=priorities
        audit['normalization'].append('parse_domain_priorities_json_string')
        save(args)
    if isinstance(policy,dict) and 'domain_priorities' in policy and not isinstance(policy['domain_priorities'],dict):
        return fail('invalid_domain_priorities_type','$.new_policy.domain_priorities','Priorities must be an object.')
    try:
        parse_reflection(audit['canonicalized_response'],race)  # Unchanged full original validation.
    except Rejected as exc:
        code,path,text=ERRORS.get(str(exc),('invalid_policy_schema','$.new_policy','Existing reflection validation rejected the policy.'))
        return fail(code,path,text)
    audit.update(status='normalized' if audit['normalization'] else 'strict_valid',
                 validation_result='PASS',validation_code='ok',validation_path='$',validation_message='Existing reflection validation passed.')
    return audit

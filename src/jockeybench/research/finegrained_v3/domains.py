"""Native bounded history limit; other domains and semantic values unchanged."""
from jockeybench.errors import Rejected
from jockeybench.research.adaptive import domains as original
from .control import DEPTHS

def backend(action, race):
    if action['domain']=='recent_history':
        depth=action.get('depth',5)
        if type(depth) is not int or depth not in DEPTHS:raise Rejected('History depth must be 1, 3 or 5')
        return 'horse_history',{'race_id':race,'number':action['horse_number'],'limit':depth,'order':'recent'}
    return original.backend(action,race)

# Preserve every field/value and the frozen predictor prompt semantics. The
# provenance carries the exact limit. Existing "latest five" means the legal
# upper window, not a promise that five records are available/delivered.
project=original.project
legacy_fetch=type(original.fetch)(original.fetch.__code__,{**original.fetch.__globals__,'backend':backend},'fetch')

def fetch(run,action,race,logical_id=None):
    from .marginal_cost import MarginalAcquisition
    from jockeybench.canonical import digest
    import copy
    logical_id=logical_id or 'policy-'+str(1+sum(bool(e.get('evidence_id')) for e in run.state.get('active_trace',[])))
    row=MarginalAcquisition(run).call(action['domain'],horse_number=action.get('horse_number'),depth=action.get('depth'),logical_id=logical_id)
    action=row['action'];view=row['observation'];raw=row['backend_response']
    operation='logical-'+digest([run.state['run_id'],logical_id,action]);eid=digest([operation,action,view])
    name,args=backend(action,race)
    prov={'service_operation':operation,'evidence_id':eid,'logical_id':logical_id,'logical_digest':row['digest'],
        'backend_tool':name,'backend_arguments':args,'backend_response':copy.deepcopy(raw),
        'race_id':race,'clock':run.state['clock'],'source_manifest':row['source_manifest'],
        'response_digest':digest(raw),'semantic_view_digest':digest(view),'native_sources':row['native_sources'],
        'native_acquisition_operations':row['native_acquisition_operations'],'newly_acquired_cells':row['newly_acquired_cells'],
        'local_projection':row['local_projection'],'local_projection_operations':row['local_projection_operations']}
    run.state.setdefault('semantic_provenance',[]).append(prov)
    return copy.deepcopy(view),copy.deepcopy(row['receipt']),eid,operation

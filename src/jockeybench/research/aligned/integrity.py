"""Fail closed: graceful delivery is permitted only over a verified legal state."""
from jockeybench.errors import Rejected
from jockeybench.clock import cutoff
from jockeybench.canonical import digest
from jockeybench.audit.replay import replay as replay_economic
from jockeybench.ledger import reconcile
from jockeybench.research.experience.model import validate_frame

class IntegrityFailure(Rejected):
    pass

def verify_integrity(run,memory,require_card=True):
    s=run.state
    try:
        if len(s['schedule'])!=1 or len(s['schedule'][0]['races'])!=1:
            raise Rejected('Impossible race schedule')
        batch=s['schedule'][0];race=batch['races'][0]
        if s['clock']!=cutoff(batch['day']) or s['scores'] or s['release_ledger']:
            raise Rejected('Temporal or release state inconsistency')
        if s.get('strategy_job') not in (None,race):raise Rejected('Race/state ID mismatch')
        if s.get('memory_before',memory)!=memory:raise Rejected('Frame binding mismatch')
        validate_frame(memory,s['clock'])
        if hasattr(run,'storage') and hasattr(run.storage,'load'):
            saved=run.storage.load()  # Original loader validates the persisted digest.
            if saved and (saved['run_id']!=s['run_id'] or saved['binding']!=s['binding']):
                raise Rejected('Checkpoint identity/binding mismatch')
        elif s.get('checkpoint_digest') is not None and s['checkpoint_digest']!=digest({k:v for k,v in s.items() if k!='checkpoint_digest'}):
            raise Rejected('Checkpoint digest mismatch')
        card=s.get('active_initial',{}).get('card')
        if require_card or card is not None:
            if not card or card['race_id']!=race:raise Rejected('Race/card ID mismatch')
            numbers=[r['number'] for r in card['runners']]
            if not numbers or len(numbers)!=len(set(numbers)) or any(not isinstance(n,str) or not n for n in numbers):
                raise Rejected('Impossible entrant set')
            # Bind the visible projection to the actual charged card audit row.
            from jockeybench.research.runner import basic_card
            from jockeybench.protocol.service import Service as EconomicService
            cards=[r for r in s['audit'] if r['tool']=='card' and r.get('status')=='ok' and r['arguments'].get('race_id')==race]
            if not cards:raise Rejected('No audited initial card')
            service=EconomicService(run.source,run.config,s)
            raw=service.dispatch('card',cards[0]['arguments'],[])
            if basic_card(raw)!=card:raise Rejected('Initial card projection changed')
            if s['active_initial']['receipt']!=cards[0]['_cost']:
                raise Rejected('Initial card receipt changed')
        reconcile(s)
        replay_economic(run.config,s,source=run.source)
        from .audit import verify_semantics
        verify_semantics(s)
        from jockeybench.research.adaptive.domains import project
        events={e.get('evidence_id'):e for e in s.get('active_trace',[]) if e.get('evidence_id')}
        for p in s.get('semantic_provenance',[]):
            if p['race_id']!=race or p['clock']!=s['clock'] or p['source_manifest']!=s['binding']['source']:
                raise Rejected('Invalid evidence provenance binding')
            if p['evidence_id'] not in events:raise Rejected('Orphan evidence provenance')
            action=events[p['evidence_id']]['action']
            if p['evidence_id']!=digest([p['service_operation'],action,project(action,p['backend_response'])]):
                raise Rejected('Evidence identity mismatch')
        return True
    except IntegrityFailure:
        raise
    except Exception as exc:
        raise IntegrityFailure('hard_integrity_failure: '+type(exc).__name__+' '+str(exc)) from exc

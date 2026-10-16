"""Validate durable acquisition commits without demanding future milestones."""
from types import SimpleNamespace
from jrdb_lh.common import digest,cutoff,release
from active_research.aligned.integrity import IntegrityFailure
from active_research.policy_amortized.semantic_json.supervisor import invariant
from active_research.policy_amortized.semantic_json.view import audit_package,exact
from .domains import backend,project
from .policy_executor import EvidenceSDK,replay_acquisition

def evidence_lifecycle(s):
    execution=s.get('policy_execution',{});phase=s.get('phase')
    released=bool(s.get('release_ledger'));sealed=bool(s.get('submissions')) or phase in ('awaiting_daily_release','sealed')
    predicting=bool(s.get('llm_trajectory')) or any(k in s for k in ('prediction_context','finalization_context'))
    finalized='elapsed_seconds' in execution or predicting or sealed or released
    stage='released' if released else 'sealed' if sealed else 'predicting' if predicting else 'evidence_finalized' if finalized else 'collecting'
    checks={'stage':stage}
    checks['final_evidence_digest']=invariant('final_evidence_digest','evidence_package_digest' in s,finalized)
    checks['evidence_package']=invariant('evidence_package','evidence_package' in s,finalized)
    events=[e for e in s.get('active_trace',[]) if e.get('evidence_id')]
    ops={r['operation']:r for r in s.get('audit',[])};provs=s.get('semantic_provenance',[])
    by_op={p['service_operation']:p for p in provs}
    try:
        from .marginal_cost import audit_owned_cache
        if s.get('marginal_acquisition'):
            from .source_binding import validate_record
            validate_record(s)
        checks.update(audit_owned_cache(s,complete=execution.get('success',False)))
        if 'evidence_package' in s and exact(EvidenceSDK(SimpleNamespace(state=s)).package())!=exact(s['evidence_package']):raise ValueError('package pruned/modified')
        if 'evidence_package_digest' in s and digest(s['evidence_package'])!=s['evidence_package_digest']:raise ValueError('package digest')
        if execution.get('success'):
            if s.get('acquisition_complete') is not True:raise ValueError('missing completion')
            replay_acquisition(s)
        elif predicting or sealed:
            # Existing graceful repair semantics may handle ordinary interpreter
            # failure. Do not invent a successful ControlView/finalization receipt.
            if execution.get('success') is not False:raise ValueError('missing execution status')
        if execution.get('llm_calls',0)!=0:raise ValueError('acquisition LLM')
        for k in ('prediction_context','finalization_context'):
            if k in s:
                for key,value in audit_package(s['evidence_package'],s[k]['EvidencePackage']).items():
                    invariant(key,key in s,True,s.get(key)==value)
    except Exception as e:raise IntegrityFailure('V3 acquisition commit: '+str(e)) from e
    checks['provenance_so_far']='valid'
    checks['release_receipt']=invariant('release_receipt',released,
        released or phase in ('reflect','completed','released') or bool(s.get('scores') or s.get('aligned_scores')),
        s.get('release_ledger')=={s['schedule'][0]['races'][0]:release(s['schedule'][0]['day'])} if released else True)
    return checks

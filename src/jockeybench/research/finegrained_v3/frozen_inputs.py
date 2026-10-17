"""Frozen control identity checks. No model calls, policy generation or updates."""
import copy,json
from pathlib import Path
from jockeybench.canonical import digest, file_hash
from jockeybench.research.aligned.integrity import IntegrityFailure
from .policy_contract import source_hash

CONTROL='matched-frozen-P0-control-v1'

def verify_matched_inputs(out,m):
    def require(value,message):
        if not value:raise IntegrityFailure(message)
    require(m.get('control_protocol')==CONTROL,'Missing explicit frozen control protocol')
    b=json.loads((out/'matched-r1-bindings.json').read_text())
    p=json.loads((out/'frozen-p0-input.json').read_text())
    require(p['policy_version']=='v000' and p['parent_policy_hash'] is None,'Exact original P0 required')
    require(source_hash(p['source'])==p['policy_hash']==m.get('reused_initial_policy_hash')==b['P0_hash'],'P0 hash mismatch')
    require(file_hash(out/'frozen-p0-input.json')==b['initial_policy_file_hash'],'P0 artifact bytes changed')
    require((out/'policies/research_policy_v000.py').read_text()==p['source'],'P0 source changed')
    require(file_hash(out/'policies/research_policy_v000.json')==b['policy_record_file_hash'],'P0 record changed')
    require(len(list((out/'policies').glob('research_policy_v*.json')))==1,'Frozen policy evolved')
    require(not list(out.glob('policy_update*.json')) and not (out/'warmup-session.json').exists()
            and not (out/'warmup-runs').exists(),'Frozen control called builder/updater')
    require(m['protected']==b['control_source_hashes'],'Control source binding mismatch')
    require(digest(m['schedule'])==b['schedule_digest'],'Matched schedule changed')
    require(json.loads((out/'provider.json').read_text())==b['provider'],'Matched provider changed')
    cfg=json.loads((out/'config.json').read_text())
    require({k:v for k,v in cfg.items() if k!='managed_root'}==b['config_without_managed_root'],'Matched runtime config changed')
    require(Path(cfg['managed_root']).resolve()==out/'runs','Frozen control storage escaped')
    # Verify original pinned inputs, without opening original prediction outputs.
    origin=Path(b['R1'])
    for name,h in b['original_input_hashes'].items():
        require(file_hash(origin/name)==h,'Original R1 input changed: '+name)
    if (out/'initial-policy.json').exists():
        require(json.loads((out/'initial-policy.json').read_text())==p,'Frozen policy replacement')

def load_initial(out,m):
    verify_matched_inputs(out,m)
    if (out/'initial-policy.json').exists():raise IntegrityFailure('No frozen control restart')
    return copy.deepcopy(json.loads((out/'frozen-p0-input.json').read_text()))

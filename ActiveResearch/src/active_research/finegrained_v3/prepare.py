"""Prepare reviewable frozen inputs; never construct P0 or call a provider."""
import json
import math
import tarfile
from pathlib import Path
from jrdb_lh.common import digest,file_hash
from active_research.runner import PROJECT
from .policy_builder import write
from .lifecycle import freeze,verify_frozen

def same_phase_parameter(key,a,b):
    # Keep both v2 JSON files byte/value-identical to their own originals.
    # v2 serialized .06 as .060000000000000005 in the formal profile only.
    if key in ('input_usd_per_million','output_usd_per_million') and type(a) in (int,float) and type(b) in (int,float):
        return math.isclose(a,b,rel_tol=0,abs_tol=1e-15)
    return a==b

def prepare(baseline,warmup_profile,acceptance,out):
    baseline=Path(baseline).resolve();warmup_profile=Path(warmup_profile).resolve();out=Path(out).resolve()
    if not out.is_relative_to(PROJECT) or out.exists():raise ValueError('New independent v3 directory only')
    gate=json.loads(Path(acceptance).read_text())
    hashes={p.relative_to(PROJECT).as_posix():file_hash(p) for d in ('active_research','jrdb_lh') for p in (PROJECT/d).rglob('*.py')}
    if not gate.get('passed') or gate['source_hashes']!=hashes:raise ValueError('Matching offline regression required')
    old_manifest=json.loads((baseline/'manifest.json').read_text())
    settings=json.loads((baseline/'provider.json').read_text());profile=json.loads(warmup_profile.read_text())
    config=json.loads((baseline/'config.json').read_text())
    if settings['model']!='deepseek-ai/DeepSeek-V4-Flash-0731':raise ValueError('DeepSeek v2 baseline required')
    if settings['max_estimated_cost_usd']!=.75:raise ValueError('Race ceiling changed')
    shared=('base_url','model','api_key_env','temperature','seed','reasoning_effort',
        'input_usd_per_million','output_usd_per_million','output_token_field')
    if any(not same_phase_parameter(k,profile['provider'].get(k),settings.get(k)) for k in shared):raise ValueError('Warmup/provider mismatch')
    bindings={'schedule_digest':digest(old_manifest['schedule']),'provider':settings,
        'config_without_managed_root':{k:v for k,v in config.items() if k!='managed_root'},
        'warmup_profile':profile,'baseline_source_files':{str(p):file_hash(p) for p in
            (baseline/'manifest.json',baseline/'config.json',baseline/'provider.json',warmup_profile)}}
    out.mkdir();config['managed_root']=str(out/'runs')
    for name,value in [('config.json',config),('provider.json',settings),('warmup-profile.json',profile),
        ('baseline-bindings.json',bindings),('v3-offline-gate.json',gate),
        ('offline-gate.json',{'passed':True,'source_hashes':{k:v for k,v in hashes.items() if k.startswith('active_research/policy_amortized/')}})]:write(out/name,value)
    manifest=freeze(out,old_manifest['schedule']);verify_frozen(out,manifest,require_approval=False)
    with tarfile.open(out/'frozen-run-sources.tar.gz','w:gz') as t:
        for name in sorted(hashes):t.add(PROJECT/name,arcname=name,recursive=False)
    write(out/'prelaunch-status.json',{'prepared':True,'paid_calls':0,'warmup_started':False,
        'approval_required':True,'manifest_digest':digest(manifest),'source_snapshot_sha256':file_hash(out/'frozen-run-sources.tar.gz'),
        'exact_race_order_digest':bindings['schedule_digest'],'v2_P0_reused':False})
    return manifest

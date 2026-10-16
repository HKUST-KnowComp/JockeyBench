import json
from pathlib import Path
from jrdb_lh.common import digest,file_hash,Rejected
from jrdb_lh.config import Config
from active_research.runner import PROJECT,check_config
from active_research.aligned.model import load_settings
from active_research.experience.runner import validate_schedule
from .specification import PROFILE,ARMS
from .policy_builder import write

def freeze(out,arm,schedule):
    out=Path(out)
    if arm not in ARMS:raise Rejected('Unknown arm')
    if (out/'manifest.json').exists():raise Rejected('No overwrite')
    settings=load_settings(out/'provider.json');config=Config.load(out/'config.json');check_config(config)
    if not Path(config.managed_root).resolve().is_relative_to(out.resolve()):raise Rejected('New run storage only')
    if config.limits.model_output_tokens is not None:raise Rejected('max_tokens must be null')
    code=[p for d in ('active_research','jrdb_lh') for p in (PROJECT/d).rglob('*.py')]
    manifest={**PROFILE,'profile_hash':digest(PROFILE),'scientific_arm':arm,'schedule':schedule,
        'maximum_real_races':validate_schedule(schedule),'protected':{p.relative_to(PROJECT).as_posix():file_hash(p) for p in sorted(code)},
        'files':{n:file_hash(out/n) for n in ('config.json','provider.json','offline-gate.json')},'smoke_only':True}
    write(out/'manifest.json',manifest);return manifest

"""Offline template preparation. Null prices intentionally require verified input."""
import json
from pathlib import Path
from jrdb_lh.common import Rejected, file_hash
from .specification import PROFILE, ARMS, execution_hash
from active_research.experience.frame import PROTOCOL, SCHEMA

def templates(arm):
    if arm not in ARMS: raise Rejected('Unknown scientific arm')
    manifest={**PROFILE,'scientific_arm':arm,'execution_profile_hash':execution_hash(),
              'protocol':PROTOCOL,'frame_schema':SCHEMA,'schedule':[],
              'maximum_real_races':0,'protected':{},'files':{},'formal':False,
              'template_only':True}
    provider={'model':'deepseek-ai/DeepSeek-V4-Flash-0731',
              'base_url':'https://api.deepinfra.com/v1/openai','api_key_env':'DEEPINFRA_API_KEY',
              'timeout_seconds':3600,'retry_attempts':10,'retry_base_seconds':2,
              'retry_max_seconds':300,'output_token_field':'max_tokens',
              'max_estimated_cost_usd':.75,'temperature':0,'seed':0,
              'input_usd_per_million':None,'output_usd_per_million':None,
              'reservation_output_tokens':None}
    return manifest,provider

def freeze(out,arm,schedule):
    """Freeze already configured files and unchanged code; never execute a race."""
    from .model import load_settings
    from jrdb_lh.config import Config
    from active_research.runner import check_config
    from active_research.experience.runner import validate_schedule
    out=Path(out)
    manifest,_=templates(arm)
    load_settings(out/'A2-provider.json')
    config=Config.load(out/'A2-config.json')
    check_config(config)
    if not Path(config.managed_root).resolve().is_relative_to(out.resolve()):
        raise Rejected('Aligned storage must belong to the new output directory')
    manifest.update(schedule=schedule,maximum_real_races=validate_schedule(schedule),template_only=False)
    root=Path(__file__).resolve().parents[2]
    code=[p for base in (root/'active_research',root/'jrdb_lh') for p in base.rglob('*.py')]
    manifest['protected']={p.relative_to(root).as_posix():file_hash(p) for p in sorted(code)}
    manifest['files']={name:file_hash(out/name) for name in ('A2-config.json','A2-provider.json')}
    destination=out/'A2-manifest.json'
    if destination.exists():raise Rejected('Existing manifest; never overwrite')
    destination.write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    return manifest

def prepare(out,arm):
    out=Path(out)
    out.mkdir(parents=True,exist_ok=False)
    manifest,provider=templates(arm)
    for name,value in [('A2-manifest.template.json',manifest),('A2-provider.template.json',provider)]:
        (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2))
    (out/'README.txt').write_text('Offline templates only. Populate schedule, verified prices and documented reservation output bound, use the unchanged A2 config, then freeze. No API execution is authorized.\n')

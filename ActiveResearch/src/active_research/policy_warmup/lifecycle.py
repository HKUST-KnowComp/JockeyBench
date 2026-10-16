"""Bind only initial construction into the unchanged v1 daily execution code."""
import json
from pathlib import Path
from types import FunctionType
from jrdb_lh.common import digest,file_hash,Rejected
from active_research.policy_amortized import lifecycle as v1
from active_research.policy_amortized.specification import PROFILE as V1_PROFILE
from active_research.policy_amortized.policy_builder import write
from active_research.policy_amortized.prepare import freeze as freeze_v1
from .builder import build
from .archive import load

PROTOCOL='Policy-Amortized-Research-v2.1-Warmup'

def freeze(out,schedule):
    out=Path(out)
    # New-directory freeze only; this never opens a v1 historical manifest.
    m=freeze_v1(out,'adaptive_research_program',schedule)
    m['experiment_protocol']=PROTOCOL
    m['warmup_profile_hash']=file_hash(out/'warmup-profile.json')
    m['files']['warmup-profile.json']=m['warmup_profile_hash']
    write(out/'manifest.json',m)
    return m

def verify_frozen(out,m):
    if m.get('experiment_protocol')!=PROTOCOL:raise Rejected('Wrong v2 protocol')
    original={**m,'experiment_protocol':V1_PROFILE['experiment_protocol']}
    v1.verify_frozen(out,original)
    profile=json.loads((out/'warmup-profile.json').read_text());load(profile)
    gate=json.loads((out/'offline-gate.json').read_text())
    actual={k:v for k,v in m['protected'].items() if k.startswith('active_research/policy_warmup/')}
    if not gate.get('passed') or gate.get('warmup_source_hashes')!=actual:raise Rejected('Missing v2 offline gate')

def construct(out,settings,registry):
    profile=json.loads((Path(out)/'warmup-profile.json').read_text())
    accepted=build(out,profile)
    return accepted,json.loads((Path(out)/'warmup-session.json').read_text())

def finalize(out):
    out=Path(out);path=out/'smoke-report.json'
    if not path.exists():return
    report=json.loads(path.read_text());report['protocol']=PROTOCOL
    if (out/'warmup-accounting.json').exists():
        warm=json.loads((out/'warmup-accounting.json').read_text())
        report['warmup']=warm
        # Shared report already includes complete warmup builder usage exactly once.
        report['all_logical_requests_per_race']=report['total_logical_requests']/report['attempted'] if report['attempted'] else None
        formal=report['outcomes']['research_cost']['sum']*.5
        report['full_economic_accounting']={'weighted_warmup_score_units':warm['weighted_ledger_cost'],
            'weighted_formal_research_score_units':formal,'api_usd_by_phase':{
                'warmup':warm['api_cost_usd_estimate'],'update':report['api']['updater']['estimated_cost_usd'],
                'prediction':report['api']['prediction']['estimated_cost_usd'],'repair':report['api']['repair']['estimated_cost_usd']},
            'harmonized_total_cost_at_150_score_units_per_usd':warm['weighted_ledger_cost']+formal+150*report['total_api_estimate'],
            'native_scorer_unchanged':True,'note':'Synthetic score units are not money; exchange convention shown explicitly. Modeling net_A includes weighted warmup; its net_B additionally includes API*150. No historical scorer overwritten.'}
    report['comparison']={'status':'not computed','note':'No cold-start data loaded during construction or execution.'}
    report['warmup_support_cutoff']=1704456000
    if (out/'day2-candidate.json').exists():
        candidate=json.loads((out/'day2-candidate.json').read_text())
        version=candidate['policy_version']
        report['day2_candidate_source']=(out/'policies'/('research_policy_'+version+'.py')).read_text()
    write(out/'v2-smoke-report.json',report)
    (out/'Policy-Amortized-Research-v2-Warmup-Day1-Smoke-Report.md').write_text(
        '# Policy-Amortized Research v2 — Warmup Day1 smoke\n\n'
        'No Day2 races. Warmup support cutoff is 2024-01-05 21:00 JST. Full warmup receipts stay on Mini.\n\n```json\n'+json.dumps(report,indent=2)+'\n```\n')

def execute(out):
    out=Path(out).resolve()
    bound=FunctionType(v1.execute.__code__,{**v1.execute.__globals__,'verify_frozen':verify_frozen,'construct':construct})
    try:bound(out)
    finally:finalize(out)

"""Derived compact smoke report; reads prior results only after Day1 finishes."""
import gzip
import json
from pathlib import Path
from jrdb_lh.common import packed
from jrdb_lh.ledger import REVENUE_CATEGORIES
from .project import model_visible_semantic_projection
from .schema import exact
from .offline import stats


def noncompute_cost_micro(state):
    return sum(e['amount_micro'] for e in state['ledger']['entries']
               if e['category'] not in REVENUE_CATEGORIES and e['category'] != 'compute')


def finalize(out):
    out=Path(out);report=json.loads((out/'compact-smoke-report.json').read_text())
    status=report['status']
    states=[json.loads(gzip.decompress(p.read_bytes())) for p in sorted((out/'runs').glob('PAR-*/checkpoint.json.gz'))]
    report['compact_request_bytes']=report['prediction_request_bytes']
    if states and all('prediction_context' in s for s in states):
        report['compact_view_bytes']=stats([len(packed(s['prediction_context']['CompactEvidenceView'])) for s in states])
    if status['errors'] or status['completed_days']!=['2024-01-06'] or len(states)!=24:
        report['comparison']={'status':'not computed; incomplete smoke'}
    else:
        assert status['day2_races_executed']==0
        prior=Path(json.loads((out/'comparison-source.json').read_text())['directory'])
        old_report=json.loads((prior/'v2-smoke-report.json').read_text())
        old_states={s['schedule'][0]['races'][0]:s for s in [json.loads(gzip.decompress(p.read_bytes())) for p in (prior/'runs').glob('PAR-*/checkpoint.json.gz')]}
        checks=[]
        for s in states:
            race=s['schedule'][0]['races'][0];old=old_states[race]
            equal=exact(model_visible_semantic_projection(s['evidence_package']))==exact(model_visible_semantic_projection(old['evidence_package']))
            actions=lambda x:[e['action'] for e in x['active_trace'] if e.get('evidence_id')]
            checks.append({'race_id':race,'semantic_inputs_identical':equal,
                'query_actions_identical':exact(actions(s))==exact(actions(old)),
                'noncompute_ledger_cost_identical':noncompute_cost_micro(s)==noncompute_cost_micro(old),
                'full_evidence_digest':s['full_evidence_digest'],'compact_view_digest':s['compact_view_digest'],
                'semantic_projection_digest':s['semantic_projection_digest']})
        def metrics(r):
            return {k:r[k] for k in ['delivery','acquisition','domains','prediction_request_bytes','api','context_limit_frequency','Top1','Top3','outcomes']}
        report['comparison']={'status':'post hoc','same_24_races':True,'per_race_equivalence':checks,
            'original_warmed_v2':metrics(old_report),'compact':metrics(report),
            'note':'Single-day descriptive comparison; no quality superiority claim. Tool/data tariffs and projections are unchanged. Interpreter CPU metering can vary and is reported rather than normalized away.',
            'shared_original_warmup_accounting':old_report['warmup'],
            'initial_builder_reexecuted':False}
        report['prediction_output_tokens']=stats([o['completion_tokens'] for s in states for o in s['model_outputs']])
        report['prediction_api_per_race']=stats([sum(o.get('cost_usd') or 0 for o in s['model_outputs']) for s in states])
    (out/'compact-smoke-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    (out/'CompactEvidenceView-v1-Day1-Smoke-Report.md').write_text('# CompactEvidenceView-v1 Day1 smoke\n\nFullEvidencePackage remains the audit/accounting source of truth. No Day2 races.\n\n```json\n'+json.dumps(report,ensure_ascii=False,indent=2)+'\n```\n')

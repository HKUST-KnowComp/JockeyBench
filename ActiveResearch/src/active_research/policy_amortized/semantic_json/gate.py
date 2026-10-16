"""Outcome-blind engineering gate. No database, results API, or quality metrics."""
import gzip
import json
import math
import os
import statistics
import time
from collections import Counter
from pathlib import Path
from jrdb_lh.common import digest
from active_research.aligned.integrity import IntegrityFailure
from ..policy_builder import write
from .runtime import verify_semantics


class GateFailed(RuntimeError):
    pass


def load_checkpoint(path):
    s=json.loads(gzip.decompress(Path(path).read_bytes()))
    if s.get('checkpoint_digest')!=digest({k:v for k,v in s.items() if k!='checkpoint_digest'}):
        raise IntegrityFailure('Checkpoint digest/corruption')
    return s


def retries(attempts):
    # attempt_index is session-global, NOT a per-request retry index.
    return sum(max(0,n-1) for n in Counter(a['request_id'] for a in attempts).values())


def nonresult_metrics(s):
    outputs=s.get('model_outputs',[]);requests=s.get('llm_trajectory',[])
    sizes=[x['bytes'] for x in s.get('request_sizes',[])]
    lat=[r.get('logical_request_elapsed_seconds') for r in requests]
    complete=bool(outputs and sizes and requests) and all(
        type(o.get(k)) is int and o[k]>=0 for o in outputs for k in ('prompt_tokens','completion_tokens'))
    complete=complete and all(type(x) in (int,float) and math.isfinite(x) and x>=0 for x in lat)
    return {'race_id':s['schedule'][0]['races'][0],
        'sealed':bool(s.get('submissions')),
        'delivery':[d['delivery'] for d in s.get('aligned_delivery',{}).values()],
        'input_tokens':sum(o.get('prompt_tokens') or 0 for o in outputs),
        'output_tokens':sum(o.get('completion_tokens') or 0 for o in outputs),
        'latency_seconds':sum(x or 0 for x in lat),
        'request_bytes':max(sizes,default=0),
        'logical_requests':len(requests),'provider_attempts':len(s.get('provider_attempts',[])),
        'retries':retries(s.get('provider_attempts',[])),
        'api_estimate_usd':sum(o.get('cost_usd') or 0 for o in outputs),
        'unknown_cost_outputs':sum(o.get('cost_usd') is None for o in outputs),
        'context_limit':s.get('graceful_trigger')=='context_limit',
        'metrics_complete':complete}


def evaluate(rows,reference):
    ids=reference['race_ids'];old=reference['rows']
    valid=len(rows)==len(old)==5 and [r['race_id'] for r in rows]==ids==[r['race_id'] for r in old]
    measured={key:{'aggregate':sum(r[key] for r in rows),'median':statistics.median([r[key] for r in rows]) if rows else None,
                   'max':max((r[key] for r in rows),default=0)}
              for key in ('input_tokens','output_tokens','latency_seconds','request_bytes')}
    baseline={key:{'aggregate':sum(r[key] for r in old),'median':statistics.median(r[key] for r in old)}
              for key in ('input_tokens','output_tokens','latency_seconds','request_bytes')}
    criteria={'exact_five_races':valid,'five_sealed':len(rows)==5 and all(r['sealed'] for r in rows),
        'complete_nonresult_metrics':bool(rows) and all(r['metrics_complete'] for r in rows),
        'no_hard_delivery_failure':all(r['delivery'] and 'no_delivery' not in r['delivery'] for r in rows),
        'context':bool(rows) and measured['request_bytes']['max']<235000 and not any(r['context_limit'] for r in rows),
        'median_output':valid and measured['output_tokens']['median']<=1.75*baseline['output_tokens']['median'],
        'aggregate_output':valid and measured['output_tokens']['aggregate']<=1.75*baseline['output_tokens']['aggregate'],
        'median_latency':valid and measured['latency_seconds']['median']<=2*baseline['latency_seconds']['median']}
    return {'passed':all(criteria.values()),'criteria':criteria,'thresholds':{'output_factor':1.75,'latency_factor':2,'max_request_bytes_exclusive':235000},
        'race_ids':ids,'matched_original':reference,'measured':measured,'baseline':baseline,'per_race':rows,
        'outcomes_inspected':False,'accuracy_used_for_stopping':False,'release_count':0,'updater_calls':0}


def run_gate(out,manifest):
    from .lifecycle import verify_frozen
    out=Path(out);verify_frozen(out,manifest)
    paths=sorted((out/'runs').glob('PAR-*/checkpoint.json.gz'))
    if len(paths)!=5 or len(list((out/'runs').glob('PAR-*')))!=5:
        raise IntegrityFailure('Gate must run before race 6 creation')
    if list(out.glob('frame-*.json')) or list(out.glob('policy_update*.json')):
        raise IntegrityFailure('Early Frame/updater')
    rows=[]
    for path in paths:
        s=load_checkpoint(path)
        # Test presence only: no result values/quality fields are read or emitted.
        if s.get('release_ledger') or s.get('scores') or s.get('aligned_scores'):
            raise IntegrityFailure('Outcome leakage before five-race gate')
        if any(a.get('tool')=='results' for a in s.get('audit',[])):
            raise IntegrityFailure('Illegal results access before gate')
        verify_semantics(s)
        rows.append(nonresult_metrics(s))
    result=evaluate(rows,json.loads((out/'five-race-reference.json').read_text()))
    result.update(integrity='PASS',source_hashes_verified=True,config_hashes_verified=True)
    name='SemanticJSON-Light-v1-5Race-Gate-'+('Pass' if result['passed'] else 'Failure')+'-Report.md'
    (out/name).write_text('# SemanticJSON-Light-v1 five-race engineering gate\n\nNo outcomes inspected or released. Same trajectory; no restart.\n\n```json\n'+json.dumps(result,indent=2)+'\n```\n')
    write(out/'five-race-gate.json',result)
    return result


def barrier(out,manifest):
    out=Path(out)
    if (out/'five-race-gate.json').exists():raise IntegrityFailure('Gate replay/reentry')
    write(out/'five-race-gate-pending.json',{'manifest_digest':digest(manifest),'sealed':5,'before_race':6})
    owner=json.loads((out/'supervisor-start.json').read_text())['supervisor_pid']
    while not (out/'five-race-gate.json').exists():
        try:os.kill(owner,0)
        except ProcessLookupError as exc:raise IntegrityFailure('Supervisor unavailable at gate') from exc
        time.sleep(1)
    decision=json.loads((out/'five-race-gate.json').read_text())
    if not decision.get('passed'):raise GateFailed('Engineering gate failed; no race 6, release or updater')

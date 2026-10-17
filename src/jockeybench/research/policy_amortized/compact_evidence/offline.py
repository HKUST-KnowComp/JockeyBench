"""Frozen-checkpoint-only replay. No database, research tool or API request."""
import copy
import gzip
import hashlib
import json
import statistics
from pathlib import Path
from unittest.mock import patch
from jockeybench.canonical import digest, packed
from .project import compact_view, model_visible_semantic_projection
from .audit import audit_package, project_observation
from .schema import schema_fields, MODEL_VISIBLE_SEMANTIC_FIELDS, AUDIT_ONLY_FIELDS, exact


def stats(values):
    values=sorted(values)
    def percentile(q):
        position=(len(values)-1)*q; lo=int(position); hi=min(lo+1,len(values)-1)
        return values[lo]+(values[hi]-values[lo])*(position-lo)
    return {'mean':statistics.mean(values),'median':statistics.median(values),
            'p90':percentile(.9),'p95':percentile(.95),'max':max(values)}


def replay(frozen_directory, output_directory):
    from jockeybench.research.policy_amortized.audit import verify_semantics
    from jockeybench.research.policy_amortized.frame import extract, make_frame
    from .lifecycle import P0_HASH
    frozen=Path(frozen_directory);out=Path(output_directory);out.mkdir(exist_ok=False)
    paths=sorted(p for p in frozen.rglob('*') if p.is_file())
    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    policy=json.loads((frozen/'initial-policy.json').read_text())
    assert policy['policy_hash']==P0_HASH
    frame=json.loads((frozen/'frame-2024-01-06.json').read_text())
    rows=[];frame_rows=[];all_races=set();total_references=0
    def forbidden(*args,**kwargs):raise AssertionError('Frozen replay must not query tools/database/API')
    with patch('sqlite3.connect',side_effect=forbidden),patch('urllib.request.urlopen',side_effect=forbidden),patch('jockeybench.research.adaptive.domains.fetch',side_effect=forbidden):
        for path in sorted((frozen/'runs').glob('PAR-*/checkpoint.json.gz')):
            state=json.loads(gzip.decompress(path.read_bytes()));state_before=packed(state)
            assert state['checkpoint_digest']==digest({k:v for k,v in state.items() if k!='checkpoint_digest'})
            full=state['evidence_package'];assert digest(full)==state['evidence_package_digest']
            verify_semantics(state)
            compact=compact_view(full);proof=audit_package(full,compact)
            assert packed(compact)==packed(compact_view(copy.deepcopy(full)))
            from .expand import expand_compact_view
            assert exact(expand_compact_view(json.loads(json.dumps(compact))))==exact(model_visible_semantic_projection(full))
            old=next(r['request'] for r in state['llm_trajectory'] if r['phase']=='prediction-1')
            new=copy.deepcopy(old);new['messages'][1]['content']=json.dumps(project_observation(json.loads(old['messages'][1]['content'])),ensure_ascii=False)
            references=[s['race_id'] for x in full['observations'] if x['domain']=='recent_history' for s in x['observation']['starts']]
            all_races.update(references);total_references+=len(references)
            a=compact_view(full,shared_races=False,index_maps=False,retain_metadata=True)
            ab=compact_view(full,shared_races=True,index_maps=False,retain_metadata=True)
            abc=compact_view(full,shared_races=True,index_maps=False,retain_metadata=False)
            sizes=[len(packed(v)) for v in [full,a,ab,abc,compact]]
            row={'run_id':state['run_id'],'race_id':full['race_id'],**proof,
                 'semantic_equality':'PASS','deterministic':'PASS','reversible':'PASS','provenance_linkage':'PASS','no_semantic_field_loss':'PASS',
                 'full_bytes':sizes[0],'semantic_bytes':len(packed(model_visible_semantic_projection(full))), 'compact_bytes':sizes[-1],
                 'old_request_bytes':len(packed(old)),'compact_request_bytes':len(packed(new)),
                 'reduction_bytes':len(packed(old))-len(packed(new)),
                 'reduction_percent':100*(1-len(packed(new))/len(packed(old))),
                 'history_references':len(references),'unique_historical_races':len(set(references)),
                 'dedup_reuse_factor':len(references)/len(set(references)) if references else None,
                 'ablation_bytes':dict(zip(['full','A','AB','ABC','ABCD'],sizes)),
                 'component_saved_bytes':dict(zip(['schema_once','shared_races','metadata_separation','identity_and_missing_indices'],[sizes[i]-sizes[i+1] for i in range(4)]))}
            rows.append(row);frame_rows.append(extract(state,policy,frame['release_clock']))
            assert packed(state)==state_before
    assert len(rows)==24 and max(r['old_request_bytes'] for r in rows)==230001
    regenerated=make_frame(frame_rows,[],frame['day'],policy,frame['release_clock'],[r['race_id'] for r in frame_rows])
    assert exact(regenerated)==exact(frame)
    changed=[p for p,h in before.items() if hashlib.sha256(Path(p).read_bytes()).hexdigest()!=h]
    assert not changed
    result={'replayed_races':24,'semantic_pass':24,'regression_status':'pending; codec/replay alone do not authorize API',
            'rows':rows,'summary':{k:stats([r[k] for r in rows]) for k in ['full_bytes','semantic_bytes','compact_bytes','old_request_bytes','compact_request_bytes','reduction_bytes','reduction_percent']},
            'old_max_request_race':max(rows,key=lambda r:r['old_request_bytes']),
            'component_saved_bytes':{k:sum(r['component_saved_bytes'][k] for r in rows) for k in rows[0]['component_saved_bytes']},
            'history_references':total_references,'unique_historical_races_across_day':len(all_races),
            'within_request_unique_races_sum':sum(r['unique_historical_races'] for r in rows),
            'frame_exactly_unchanged':True,'research_cost_tool_calls_evidence_cells_unchanged':True,
            'protected_files':len(before),'protected_changes':changed,
            'max_compact_request_bytes':max(r['compact_request_bytes'] for r in rows),
            'compression_target_met':max(r['compact_request_bytes'] for r in rows)<190000,
            'schema_maps':compact['schema_maps'],'semantic_whitelist':MODEL_VISIBLE_SEMANTIC_FIELDS,'audit_only_fields':AUDIT_ONLY_FIELDS,
            'ablation_note':'Full -> A schema rows (metadata retained in sidecar) -> AB shared race rows -> ABC metadata removed -> ABCD horse/missing indices. C starts from full, not an already metadata-free baseline. Negative contributions are reported honestly.',
            'equality_definition':'Type-tagged exact recursive comparison; object key order irrelevant, lists/order and scalar types exact. All absent/null/missing reasons retained.'}
    (out/'replay.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    (out/'historical-hashes.json').write_text(json.dumps(before,indent=2))
    (out/'CompactEvidenceView-v1-Offline-Report.md').write_text('# CompactEvidenceView-v1 offline frozen replay\n\nFull regression and protected-source acceptance must be appended before launch. No database/tools/API used.\n\n```json\n'+json.dumps(result,ensure_ascii=False,indent=2)+'\n```\n')
    return result

"""Fresh matched-P0 smoke; only representation and usage-scope metadata change."""
import copy
import json
from pathlib import Path
from types import FunctionType
from jrdb_lh.common import digest, file_hash, Rejected
from active_research.policy_amortized import lifecycle as original
from active_research.policy_amortized.prepare import freeze as base_freeze
from active_research.policy_amortized.specification import PROFILE
from active_research.policy_amortized.policy_builder import Registry as BaseRegistry, write
from active_research.policy_amortized.policy_contract import source_hash
from active_research.policy_amortized.model_usage import summarize
from . import runtime
from .schema import VERSION

PROTOCOL = 'Policy-Amortized-Research-v2.1-Warmup-CompactEvidenceView-v1'
P0_HASH = 'd3f70dd7abe78cd75f801445bc167f59c82748b0c78ccd7d0e341b572a1dadc1'


class Registry(BaseRegistry):
    def __init__(self, folder):super().__init__(folder, usage_scope='through_save_research_policy')


def freeze(out, schedule):
    out = Path(out)
    m = base_freeze(out, 'adaptive_research_program', schedule)
    m.update(experiment_protocol=PROTOCOL, evidence_view_protocol=VERSION, reused_initial_policy_hash=P0_HASH)
    for name in ('frozen-warmed-p0.json', 'compact-offline-gate.json', 'comparison-source.json'):
        m['files'][name] = file_hash(out/name)
    write(out/'manifest.json', m)
    return m


def verify_frozen(out, manifest):
    if manifest.get('experiment_protocol') != PROTOCOL or manifest.get('evidence_view_protocol') != VERSION:
        raise Rejected('Wrong compact experiment protocol')
    original.verify_frozen(out, {**manifest, 'experiment_protocol': PROFILE['experiment_protocol']})
    gate = json.loads((out/'compact-offline-gate.json').read_text())
    if not gate.get('passed') or gate.get('replayed_races') != 24 or gate.get('semantic_pass') != 24:
        raise Rejected('Missing complete compact offline acceptance')
    if gate.get('max_compact_request_bytes', 262144) >= 190000:
        raise Rejected('Compression target not met; no real smoke')
    if gate.get('source_hashes') != manifest['protected']:
        raise Rejected('Compact acceptance/source mismatch')


def construct(out, settings, registry):
    frozen = json.loads((Path(out)/'frozen-warmed-p0.json').read_text())
    if frozen['policy_hash'] != P0_HASH or source_hash(frozen['source']) != P0_HASH:
        raise Rejected('Wrong frozen warmed P0')
    policy = registry.save(frozen['source'], usage=frozen.get('model_usage_at_save', frozen.get('model_usage')))
    # This smoke incurs no new builder call. Original warmup provenance is retained,
    # never counted again as this run's API spend.
    usage = summarize({}, 'complete_session', 'reused-frozen-warmed-P0')
    usage['termination'] = 'not_run_reused_frozen_P0'
    lineage = {'policy_hash': P0_HASH, 'original_artifact_digest': digest(frozen),
               'original_model_usage_at_save': copy.deepcopy(frozen.get('model_usage_at_save', frozen.get('model_usage'))),
               'builder_session_usage': usage, 'warmup_reexecuted': False}
    write(Path(out)/'initial-reuse.json', lineage)
    return policy, lineage


def execute(out):
    out = Path(out).resolve()
    bound = FunctionType(original.execute.__code__, {**original.execute.__globals__,
        'construct': construct, 'Registry': Registry, 'verify_frozen': verify_frozen,
        'Predictor': runtime.Predictor, 'predict': runtime.predict, 'verify_semantics': runtime.verify_semantics})
    try:bound(out)
    finally:
        if (out/'smoke-report.json').exists():
            report = json.loads((out/'smoke-report.json').read_text())
            report.update(protocol=PROTOCOL, evidence_view_protocol=VERSION,
                          reused_initial_policy_hash=P0_HASH, warmup_reexecuted=False)
            write(out/'compact-smoke-report.json', report)
            from .smoke_report import finalize
            finalize(out)

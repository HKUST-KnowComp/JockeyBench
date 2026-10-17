"""Pure, lossless semantic projection. Unknown semantic fields stay unchanged."""
import copy
import json
from jockeybench.canonical import digest
from ..compact_evidence.project import model_visible_semantic_projection
from ..compact_evidence.schema import MODEL_VISIBLE_SEMANTIC_FIELDS, AUDIT_ONLY_FIELDS, exact

VERSION = 'SemanticJSON-Light-v1'
MODEL_VISIBLE_SEMANTIC_ALLOWLIST = copy.deepcopy(MODEL_VISIBLE_SEMANTIC_FIELDS)
REMOVED_METADATA = copy.deepcopy(AUDIT_ONLY_FIELDS)


def serialize(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def semantic_view(full):
    # Reviewed source contract removes ONLY provenance, receipt and training billing_scope.
    # Nested dictionaries/lists are copied unchanged; no row/index/dedup representation.
    result = model_visible_semantic_projection(full)
    if exact(json.loads(serialize(result))) != exact(result):
        raise ValueError('Semantic JSON serialization changed a value/type')
    return result


def audit_package(full, view=None):
    expected = semantic_view(full)
    if view is not None and exact(view) != exact(expected):
        raise ValueError('Semantic JSON equality failed')
    return {'full_evidence_digest':digest(full), 'semantic_json_view_digest':digest(expected),
            'semantic_json_view_version':VERSION, 'semantic_projection_digest':digest(expected)}


def project_observation(obs):
    result = copy.deepcopy(obs)
    result['EvidencePackage'] = semantic_view(obs['EvidencePackage'])
    return result

"""Semantic equality and digests; standalone checks do not open data sources."""
import copy
from jrdb_lh.common import digest, packed
from .schema import VERSION, exact, CompactIntegrityError
from .project import compact_view, model_visible_semantic_projection
from .expand import expand_compact_view


def audit_package(full, compact=None):
    compact = compact_view(full) if compact is None else compact
    semantic = model_visible_semantic_projection(full)
    expanded = expand_compact_view(compact)
    if exact(expanded) != exact(semantic):
        raise CompactIntegrityError('Lossy compact semantic projection')
    if packed(compact) != packed(compact_view(full)):
        raise CompactIntegrityError('Noncanonical compact view')
    return {'full_evidence_digest': digest(full), 'compact_view_digest': digest(compact),
            'compact_view_version': VERSION, 'semantic_projection_digest': digest(expanded)}


def project_observation(observation):
    result = copy.deepcopy(observation)
    full = result.pop('EvidencePackage')
    result['CompactEvidenceView'] = compact_view(full)
    result['evidence_encoding'] = VERSION
    return result

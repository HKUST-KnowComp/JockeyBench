import copy
from jrdb_lh.common import digest, packed
from .schema import VERSION, exact, CompactIntegrityError
from .codec import readable_view, expand_readable_evidence, model_visible_semantic_projection


def audit_package(full, view=None):
    view = readable_view(full) if view is None else view
    expanded = expand_readable_evidence(view)
    if exact(expanded) != exact(model_visible_semantic_projection(full)):
        raise CompactIntegrityError('Lossy readable projection')
    if packed(view) != packed(readable_view(full)):
        raise CompactIntegrityError('Noncanonical readable projection')
    return {'full_evidence_digest': digest(full), 'readable_view_digest': digest(view),
            'readable_view_version': VERSION, 'semantic_projection_digest': digest(expanded)}


def project_observation(observation):
    result = copy.deepcopy(observation)
    result['ReadableCompactEvidence'] = readable_view(result.pop('EvidencePackage'))
    result['evidence_encoding'] = VERSION
    return result

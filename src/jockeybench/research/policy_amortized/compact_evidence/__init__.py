"""Lossless prediction-only view; FullEvidencePackage remains authoritative."""
from .schema import VERSION, CompactIntegrityError
from .project import compact_view, model_visible_semantic_projection
from .expand import expand_compact_view

__all__ = ['VERSION', 'CompactIntegrityError', 'compact_view',
           'model_visible_semantic_projection', 'expand_compact_view']

"""Schema positions are protocol-fixed, derived from the reviewed v5 catalog.

Structural envelopes mirror domains.project, whose source fingerprint is pinned.
An upstream addition requires explicit protocol review, never silent omission.
"""
import ast
import hashlib
import math
from pathlib import Path
from jockeybench.data import catalog as approved
from jockeybench.canonical import digest

VERSION = 'compact-evidence-v1'
CATALOG_DIGEST = 'c86927dd02f385c07954e58b023b4ae4830885391e540d43bec4d4b6df7a5022'
PROJECT_DIGEST = '197f97d688b746c3d8c9b399547180f1adcfc11423e4027dc0cf32507b9104c3'


class CompactIntegrityError(ValueError):
    pass


def schema_fields():
    catalog = {k: sorted(v) for k, v in approved.catalog().items()}
    if digest(catalog) != CATALOG_DIGEST:
        raise CompactIntegrityError('Approved projection schema drift')
    source = (Path(__file__).parents[2] / 'adaptive/domains.py').read_text()
    function = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'project')
    if hashlib.sha256(ast.get_source_segment(source, function).encode()).hexdigest() != PROJECT_DIGEST:
        raise CompactIntegrityError('Domain projection contract drift')
    return {
        'race_facts': tuple(catalog['race_facts']),
        'runner_facts': tuple(catalog['runner_facts']),
        'history_values': tuple(catalog['support_labels']),
        'training_values': tuple(catalog['training']),
        'condition_group': ('surface', 'distance', 'starts', 'wins', 'top3'),
        'shared_race': ('race_facts', 'day_index'),
        'history_start': ('race_id', 'day_index', 'race_facts', 'runner_facts', 'values', 'missing', 'label_scope'),
        'shared_start': ('race_id', 'runner_facts', 'values', 'missing', 'label_scope'),
        'track_context': ('facts', 'missing', 'feature_asof'),
        'recent_history': ('horse_number', 'starts', 'scope'),
        'training': ('horse_number', 'training', 'missing', 'feature_asof'),
        'condition_stats': ('horse_number', 'groups', 'sample_starts', 'labelled_starts', 'scope'),
    }


MODEL_VISIBLE_SEMANTIC_FIELDS = {
    'package': ('schema', 'race_id', 'entrants', 'observations'),
    'observation': ('domain', 'arguments', 'observation'),
    'arguments': ('horse_number',),
    'domain_observation': {d: schema_fields()[d] for d in ('track_context', 'recent_history', 'training', 'condition_stats')},
    'nested': {d: schema_fields()[d] for d in ('race_facts', 'runner_facts', 'history_values', 'training_values', 'condition_group', 'history_start')},
}
AUDIT_ONLY_FIELDS = {
    'observations[].provenance': 'EvidenceSDK.package internal evidence/operation linkage; retained in full package.',
    'observations[].receipt': 'EvidenceSDK.package copies the already-booked tool cost receipt.',
    'observations[training].observation.billing_scope': 'domains.project describes backend billing granularity, not workout facts.',
}


def checked_keys(value, allowed, location):
    if type(value) is not dict or set(value) - set(allowed):
        raise CompactIntegrityError('Unknown semantic fields or invalid object at ' + location)


def exact(value):
    """Type-tagged tree: Python's True==1 and 1==1.0 are not semantic equality."""
    if type(value) is dict:
        return ('dict', tuple((k, exact(v)) for k, v in sorted(value.items())))
    if type(value) is list:
        return ('list', tuple(exact(v) for v in value))
    if type(value) not in (str, int, float, bool, type(None)):
        raise CompactIntegrityError('Non-JSON semantic value')
    if type(value) is float:
        if not math.isfinite(value):raise CompactIntegrityError('Nonfinite semantic value')
        return ('float', value.hex())
    return (type(value).__name__, value)

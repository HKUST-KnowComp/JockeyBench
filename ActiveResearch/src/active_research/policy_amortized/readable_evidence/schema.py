"""Readable column headers from the same approved semantic contract as v1."""
from ..compact_evidence.schema import (schema_fields, exact, checked_keys,
    CompactIntegrityError, MODEL_VISIBLE_SEMANTIC_FIELDS, AUDIT_ONLY_FIELDS)

VERSION = 'readable-compact-evidence-v2'
NOTE = ('Evidence is grouped by horse. Row values follow named column headers; training stays with each horse. '
        'missing retains original field names and reasons; absent_fields and null_objects distinguish absent from null. '
        'No summarization or feature selection is applied.')


def columns(training_style='key_value'):
    f = schema_fields()
    result = {
        'HISTORY_COLUMNS': ['race_id', 'day_index', 'label_scope'] +
            [parent + '.' + name for parent, schema in [('race_facts', 'race_facts'),
             ('runner_facts', 'runner_facts'), ('values', 'history_values')] for name in f[schema]],
        'CONDITION_COLUMNS': list(f['condition_group']),
    }
    if training_style == 'columns': result['TRAINING_COLUMNS'] = list(f['training_values'])
    return result


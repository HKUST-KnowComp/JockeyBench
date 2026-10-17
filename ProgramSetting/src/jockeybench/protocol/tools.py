"""Tool names, arguments and JSON-schema specifications offered to agents.

*Data tools* return archive evidence and are metered and priced. *Control
tools* (``describe``, ``status``, ``ledger``, ``batch``, ``submissions``,
``save_strategy``, ``compute``) are free apart from the CPU and nested data
calls of ``compute``. The order of ``TOOL_ARGS`` is the order of the tool list
sent to the model.
"""

SEARCH = ('cursor', 'limit', 'filters', 'order', 'scope')
TOOL_ARGS = {
    'describe': set(),
    'status': set(),
    'batch': set(),
    'history': {*SEARCH, 'include_form', 'max_spend'},
    'card': {'race_id', 'max_spend'},
    'horse_history': {'race_id', 'number', *SEARCH, 'max_spend'},
    'training': {'race_id', 'max_spend'},
    'statistics': {'group_by', *SEARCH, 'max_spend'},
    'submissions': {'race_id', 'cursor', 'limit'},
    'coverage': {'race_id', *SEARCH, 'max_spend'},
    'submit': {'race_id', 'prediction'},  # never offered: the framework seals predictions
    'save_strategy': {'files', 'mode'},
    'compute': {'code'},
    'form': {'race_id', 'k', 'max_spend'},
    'results': {'race_id', 'cursor', 'limit', 'filters', 'order', 'max_spend'},
    'ledger': set(),
    'connections': {'race_id', 'max_spend'},
}
DATA_TOOLS = {'card', 'form', 'history', 'horse_history', 'training', 'statistics', 'coverage', 'results', 'connections'}
READ_TOOLS = DATA_TOOLS | {'describe', 'status', 'batch', 'submissions', 'ledger'}
REQUIRED = {
    'card': ['race_id'],
    'training': ['race_id'],
    'horse_history': ['race_id', 'number'],
    'compute': ['code'],
    'save_strategy': ['files'],
}
GROUPS = ['surface', 'distance', 'venue', 'draw', 'sex']

DESCRIPTIONS = {
    'describe': 'Read the economic-v1 schema, field meanings, tariffs and release contract.',
    'status': 'Current physical safety limits and ledger; no monetary budget cap.',
    'batch': 'Task horizon, races, entrant numbers and sealed status. No new objective features.',
    'history': 'Paged historical cards with separately released labels; include_form freezes features at each sample cutoff.',
    'card': 'Read the objective previous-evening card for an available race. Stable fields contain null for unknown values; no absolute dates.',
    'horse_history': 'Earlier starts for one target horse with only temporally available objective labels.',
    'training': 'Read permitted pre-race workout fields and explicit missing reasons.',
    'statistics': 'Counts from eligible objective labels in this page; scope defaults to support (released initial-support races before day index 0), so pass scope=all to include released window labels. All contributing evidence is charged.',
    'submissions': 'Recover only your own sealed rankings, probabilities and strategy version, by race_id or paged list. Join with feedback to learn from your own actions. No correct answers or private scores.',
    'coverage': 'Measure input-field presence/missing counts on one race_id OR a filtered historical page. Does not impute or expose hidden results.',
    'save_strategy': 'Save code, notes and parameters; see Program interface and validation in the task specification.',
    'compute': 'Run isolated Python; see Program interface and validation in the task specification.',
    'form': 'All runners recent labelled starts (k<=5), frozen at the requested race cutoff.',
    'results': 'Timed objective finish and abnormal status, then review fields; never markets or expert signals.',
    'ledger': 'Current incurred costs and permitted settled gross score.',
    'connections': 'Prior 365-day jockey and trainer starts/wins/top3 from labels visible at the target cutoff.',
}
PARAMETERS = {
    'race_id': 'Pseudonymous race identifier returned by the tools.',
    'number': 'Two-digit entrant number on the target card.',
    'scope': 'all includes visible cards; support restricts to released initial-support races before day index 0.',
    'order': 'recent sorts by day and race identifier descending; oldest reverses that order.',
    'filters': 'Conjunction of optional day and race-fact filters.',
    'day_min': 'Inclusive minimum relative day index; 0 is the configured window start.',
    'day_max': 'Inclusive maximum relative day index; negative indices precede the window.',
    'distance_min': 'Inclusive minimum race distance in metres.',
    'distance_max': 'Inclusive maximum race distance in metres.',
    'surface': 'Exact opaque categorical surface code.',
    'venue': 'Exact opaque categorical venue code.',
    'condition': 'Exact reviewed condition value (A1/A2/A3/OP or 5/10/16).',
    'include_form': 'Number of attached prior labelled starts per runner, 0..5; frozen at each historical card cutoff.',
    'k': 'Maximum prior labelled starts per runner, 1..5, frozen at the target race cutoff.',
    'group_by': 'Group this page by surface, distance, venue, draw or sex; counts use only visible labels.',
    'cursor': 'Opaque continuation token for the same query, trajectory, phase and clock; null starts a page.',
    'limit': 'Number of races per page, bounded by max_page_size; default_page_size applies when omitted.',
    'max_spend': 'Maximum score-yen for this data response including its call fee; rejection retains only the call fee.',
    'files': 'Map of flat .py/.json/.txt/.md filenames to text, at most 64 files per call (all JSON arguments of one call are limited to response_bytes); required unless mode=uniform, which takes no files. Small code, notes and fitted parameters only.',
    'mode': 'code (default) validates saved files; uniform selects the deterministic framework fallback and takes no files.',
    'code': 'Python executed in a fresh isolated process with sdk injected; assign a small JSON value to result.',
}


def _properties(page_size):
    filters = {k: {'type': 'integer'} for k in ('day_min', 'day_max', 'distance_min', 'distance_max', 'surface', 'venue')}
    filters['condition'] = {'type': ['integer', 'string']}
    for name, child in filters.items():
        child['description'] = PARAMETERS[name]
    props = {
        'race_id': {'type': 'string'},
        'number': {'type': 'string'},
        'cursor': {'type': ['string', 'null']},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': page_size},
        'group_by': {'type': 'string', 'enum': GROUPS},
        'code': {'type': 'string'},
        'order': {'type': 'string', 'enum': ['recent', 'oldest']},
        'scope': {'type': 'string', 'enum': ['all', 'support']},
        'filters': {'type': 'object', 'properties': filters, 'additionalProperties': False},
        'files': {'type': 'object', 'additionalProperties': {'type': 'string'}},
        'max_spend': {'type': 'number', 'minimum': 0},
        'k': {'type': 'integer', 'minimum': 1, 'maximum': 5},
        'include_form': {'type': 'integer', 'minimum': 0, 'maximum': 5},
        'mode': {'type': 'string', 'enum': ['code', 'uniform']},
    }
    for key, prop in props.items():
        prop['description'] = PARAMETERS[key]
    return props


def required(name):
    if name == 'save_strategy':
        return []  # mode=uniform needs no files
    return REQUIRED.get(name, ['race_id'] if name in {'form', 'connections'} else [])


def tool_specs(page_size=8):
    """OpenAI-style function specifications, in the order sent to the model."""
    props = _properties(page_size)
    return [
        {
            'type': 'function',
            'function': {
                'name': name,
                'description': DESCRIPTIONS[name],
                'parameters': {
                    'type': 'object',
                    'properties': {k: props[k] for k in sorted(keys)},
                    'required': required(name),
                    'additionalProperties': False,
                },
            },
        }
        for name, keys in TOOL_ARGS.items()
        if name != 'submit'
    ]

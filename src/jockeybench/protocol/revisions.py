"""Agent-facing text changes between protocol versions.

The paper's main cohort ran protocol 0.6.0. Protocol 0.6.1 corrects agent-visible
statements that contradicted the rules the runtime enforces; the rules, scoring and
every other byte of the contract are unchanged. Each entry is ``(0.6.0 text,
0.6.1 text)`` as the model reads it under the default (paper) configuration, so
reverting the 0.6.1 contract with these pairs reproduces the 0.6.0 contract exactly
(``tests/test_specification.py``).
"""

CORRECTIONS_0_6_1 = [
    # Probabilities were called optional, mandatory and "when supplied" in one paragraph, and optional
    # in a tool description and an error; scoring.validate rejects any prediction without them.
    (
        'Return a JSON object containing only ranking and optional probabilities; ',
        'Return a JSON object containing only ranking and probabilities, both required; ',
    ),
    ('Probabilities are mandatory. When supplied, probabilities map every entrant', 'Probabilities map every entrant'),
    (
        'Recover only your own sealed rankings, optional probabilities and strategy version',
        'Recover only your own sealed rankings, probabilities and strategy version',
    ),
    ('Prediction requires ranking and optional probabilities', 'Prediction requires exactly ranking and probabilities'),
    # The hit bonus was called independent of the probabilities, but a race where the current program returns valid,
    # all-equal probabilities is sealed as silent_uniform and earns no bonus (lifecycle.predict_race, scoring.hit_revenue).
    (
        'The hit bonus is independent of submitted probabilities;',
        'The hit bonus amount does not depend on the probability values, but a race where the current validated program '
        'returns valid probabilities that are all exactly equal is sealed as silent_uniform and earns zero hit bonus;',
    ),
    (
        'The program stays valid if kept, including after repeated uninformative output.',
        'The program stays valid if kept, including after repeated uninformative output, but each race where it returns '
        'valid probabilities that are all exactly equal is sealed as silent_uniform with zero hit bonus.',
    ),
    # The direct-response ceiling is min(response_bytes, context_bytes // 3), not response_bytes, and it is never
    # lowered by the context; a rejected compute still pays CPU and nested fees (service.call, CompatibleAgent._serve).
    (
        'Direct responses exceeding response_bytes=1048576 (including a reserved 2048-byte envelope) are rejected uncharged, '
        'with no data or call fee. A smaller remaining model context can lower this ceiling.',
        'Direct tool responses exceeding min(response_bytes, context_bytes // 3)=87381 bytes (including a reserved 2048-byte '
        'envelope) are rejected without a data or call fee (a data request that first fails its max_spend check keeps that '
        'call fee); a rejected compute still pays its CPU and nested SDK fees, and '
        'its file changes are rolled back. SDK calls inside compute and framework-run jobs may return up to '
        'response_bytes=1048576.',
    ),
    (
        'Undeliverable response: use a smaller limit/k or compute with SDK pages. No data or call fee charged.',
        'Undeliverable response: use a smaller limit/k, page with the SDK inside compute, or return a smaller compute result. '
        'No data or call fee charged; a compute keeps its CPU and nested SDK fees.',
    ),
    # Without the old 'context can lower this ceiling', a context that cannot make room for it ends the session
    # (compatible.fit_context runs before each request and, with the ceiling reserved, before each tool call).
    (
        '(oversize initial context ends the session; source inclusion has an explicit truncation manifest).',
        '(if dropping rounds cannot fit the next request, or leave room for a response at the direct ceiling before a tool '
        'call, the session ends; source inclusion has an explicit truncation manifest).',
    ),
    # save_strategy needs files unless mode=uniform, which refuses them (tools.REQUIRED, service.dispatch).
    (
        'Map of flat .py/.json/.txt/.md filenames to text; small code, notes and fitted parameters only.',
        'Map of flat .py/.json/.txt/.md filenames to text, at most 64 files per call (all JSON arguments of one call are '
        'limited to response_bytes); required unless mode=uniform, which takes no files. Small code, notes and fitted '
        'parameters only.',
    ),
    (
        'code validates saved files; uniform selects the deterministic framework fallback.',
        'code (default) validates saved files; uniform selects the deterministic framework fallback and takes no files.',
    ),
    # Training arrays on disk were said to be rejected; the workspace checks are file type, flatness, .py syntax and
    # predict, and finite JSON without sample caches (sandbox/executor.files_digest, validate_json_artifact).
    (
        'historical samples, race packages and training arrays on disk are rejected and rolled back.',
        'do not store historical samples, race packages or training arrays. The workspace may hold only flat '
        '.py/.json/.txt/.md/.bin/.pkl files; every .py file must parse and strategy.py must define predict(sdk, context), '
        'and a .json file is rejected if it is not finite JSON or contains, at any depth, an examples, runners, card, '
        'label_missing or records key whose value is a list or object. A rejected compute or save is rolled back as a whole.',
    ),
    (
        'Strategy artifacts must use allowed file types and configured sizes',
        'Strategy workspace files must be .py/.json/.txt/.md/.bin/.pkl files within configured sizes',
    ),
    # A saved program that validation rejects is discarded with its session's files, NOTES.md included, and the
    # repair session starts from the last accepted workspace (lifecycle.validate_candidate).
    (
        '(saving NOTES.md suffices); otherwise restore the session-start workspace.',
        '(saving NOTES.md suffices) and validation does not reject the program left in the workspace when the session ends; '
        'otherwise the session-start workspace is restored, NOTES.md included.',
    ),
    (
        '-> candidate rejected, previous valid version retained, one repair session',
        '-> candidate rejected and discarded with the files saved alongside it, previous valid version retained, one repair session',
    ),
    (
        'Repair the candidate program with remaining session resources, or choose uniform.',
        'Repair the candidate program with remaining session resources, or choose uniform. If format_repair has an error '
        'field, that candidate was rejected and discarded and the last accepted workspace was restored; '
        'current_strategy_files show the .py/.json/.txt/.md files of that workspace.',
    ),
    # statistics defaults to scope=support, unlike the other page tools (service._page_tool).
    (
        'Counts from eligible objective labels in this page. All contributing evidence is charged.',
        'Counts from eligible objective labels in this page; scope defaults to support (released initial-support races '
        'before day index 0), so pass scope=all to include released window labels. All contributing evidence is charged.',
    ),
]

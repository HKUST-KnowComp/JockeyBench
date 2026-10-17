"""The single authoritative agent task specification (the system prompt and ``describe``)."""

import json
import math
from dataclasses import asdict

from .. import PROTOCOL_VERSION
from ..data.catalog import catalog
from ..ledger import summary
from ..sandbox.environment import probe
from .delivery import SDK_CONVENTION, session_memory
from .metering import REPEAT_RULE
from .tools import DATA_TOOLS, tool_specs

# Worked example quoted in the scoring section: n=14, winner probability 0.10.
EXAMPLE_LOG_REVENUE = 100 * math.log(14 * (0.98 * 0.10 + 0.02 / 14))
HIDDEN_LIMITS = {'cache_mib', 'rss_bytes', 'artifact_bytes', 'open_files', 'numerical_threads', 'storage_bytes', 'job_seconds'}
# Agents of the paper runs were shown three limits inherited from an earlier,
# hit-only protocol. They have no effect in economic-v1 but are kept so that the
# contract differs from v0.6.0 only by the corrections in ``revisions.py``.
INHERITED_LIMITS = {'feedback_memory_items': 32, 'feedback_memory_bytes': 16384, 'format_repairs': 1}


def public_limits(config):
    values = {k: v for k, v in asdict(config.limits).items() if k not in HIDDEN_LIMITS}
    values['max_page_size'] = values.pop('page_size')
    return {**values, **INHERITED_LIMITS}


def allowances(config):
    lim = config.limits
    rows = []
    for phase, prefix in [('initial exploration', 'warmup'), ('daily reflection', 'batch')]:
        cap = getattr(lim, prefix + '_model_calls')
        reserve = min(cap, lim.repair_model_calls)
        rows.append(
            {
                'phase': phase,
                'allowances': {
                    k: getattr(lim, k) for k in (prefix + '_calls', prefix + '_evidence_cells', prefix + '_bytes', prefix + '_model_calls')
                },
                'main_model_calls': cap - reserve,
                'reserved_repair_calls': reserve,
                'sharing': 'Direct tools and nested SDK calls share this phase; repair shares remaining data allowances.',
                'at_cap': 'Data calls reject recoverably; model-call or turn cap ends this session.',
            }
        )
    rows.append(
        {
            'phase': 'repair',
            'allowances': {'repair_model_calls': lim.repair_model_calls},
            'sharing': 'One repair session per phase; reserved calls plus all unused main calls, within the original phase cap.',
            'at_cap': 'Keep the last valid program or uniform; no second repair session.',
        }
    )
    rows.append(
        {
            'phase': 'per-race prediction and validation job',
            'allowances': {k: getattr(lim, k) for k in ('strategy_calls', 'strategy_evidence_cells', 'strategy_cpu_seconds')},
            'sharing': 'Separate from sessions; each execution has fresh caps (including each validation repeat). Outer compute and all SDK calls count.',
            'at_cap': 'Job fails; candidate rejection or prediction fallback applies.',
        }
    )
    return rows


def environment(service):
    c = service.config
    return service.state.get('binding', {}).get('environment') or probe(c.python, c.limits.numerical_threads)


def allowance_table(config):
    lines = ['Phase | Allowances | Shared or separate | At cap']
    for row in allowances(config):
        limits = ', '.join(f'{k}={json.dumps(v)}' for k, v in row['allowances'].items())
        if 'main_model_calls' in row:
            limits += f"; main={row['main_model_calls']}, repair reserve={row['reserved_repair_calls']}"
        lines.append(' | '.join((row['phase'], limits, row['sharing'], row['at_cap'])))
    return '\n'.join(lines)


def sections(service):
    c, lim, t = service.config, service.config.limits, service.config.tariff
    fields = catalog()
    a = 2 + len(fields['race_facts'])
    b = 5 + len(fields['runner_facts']) + len(fields['training'])
    boundary = (
        'No odds, popularity, payouts for current or past races, JRDB indices, marks, predictions, aptitude or class ratings, '
        'track-bias figures or free text are available. Unknown values are null with missing reasons.'
    )
    interface = (
        'Define callable predict(sdk, context) in strategy.py. Return a JSON object containing only ranking and probabilities, both required; '
        'ranking is a list of every entrant number string exactly once, with no omissions, extras or duplicates. '
        'Probabilities map every entrant to a finite number in [0,1]; their sum must be within absolute tolerance 0.02 of 1 '
        '(positive mass required). Accepted rounding is normalized; sums within 1e-12 are retained. predict must not write files. '
        + SDK_CONVENTION
        + '\n'
        'Validation checklist: Python syntax and two-argument predict declaration on save; callable predict at execution; JSON output schema; '
        'full ranking; probability domain and normalization; no workspace changes; per-race calls, evidence and CPU caps; job wall time, '
        'RSS, storage, artifact, open-file and output/IPC ceilings where configured. Imports and uncaught exceptions can fail a job.\n'
        'Failure matrix: save syntax/artifact rejection -> tool error, correct and save again; candidate execution/interface/resource failure '
        '-> candidate rejected and discarded with the files saved alongside it, previous valid version retained, one repair session; '
        'prediction execution/interface/resource failure '
        '-> try the previous valid version once, then uniform. Storage quota or an unresolved interruption stops the run for inspection. '
        'Missing strategy or mode=uniform -> deterministic fallback ranking with uniform probabilities.\n'
        'Each of up to three initial-support validation races executes twice. Differing ranking or probabilities sets '
        'nondeterministic_output=true in last_validation and prompts the same one repair session; it does not invalidate the program. '
        'Validation uses no predictive-accuracy threshold.\n'
        'Uninformative-output rule: all returned probabilities are exactly equal'
        '. If this holds on every validation race, silent_uniform requests the one repair session, regardless of evidence read. '
        'The program stays valid if kept, including after repeated uninformative output, but each race where it returns valid '
        'probabilities that are all exactly equal is sealed as silent_uniform with zero hit bonus.'
    )
    scoring = (
        'Maximize net_score = sum_races [S*ln(n_r*pbar_r) + I(delivery=predictor)*hit_bonus_yen*(h_r-1/n_r)] '
        '- sum(evidence fees + call fees + CPU fees) over the entire trajectory. '
        'After removing scratched runners from the sealed ranking, renormalize their remaining probability mass; if it is zero use uniform. '
        'n_r is the number that actually started. Smooth each remaining probability: p_smoothed=(1-lambda)*p+lambda/n_r. '
        'pbar_r is the mean smoothed probability over ALL official winners (dead heat: every official winner counts). '
        f'Tariff {t.version}: hit_bonus_yen={t.hit_bonus_yen:g}. Ranking now has a scoring role: h_r=1 if the '
        'top-ranked runner that actually started is any official winner, else 0. The hit bonus requires a validated program '
        'and sealed delivery=predictor; I is 1 only for that delivery, otherwise 0. explicit_uniform, no_strategy, '
        'silent_uniform and execution_fallback receive exactly zero hit bonus, including fallback to an older valid version. '
        'At n=14 and hit_bonus_yen=100, a hit pays +92.9 and a miss pays -7.1, in addition to the log part. '
        'The hit bonus amount does not depend on the probability values, but a race where the current validated program returns '
        'valid probabilities that are all exactly equal is sealed as silent_uniform and earns zero hit bonus; with one official winner, '
        'a uniformly random top choice '
        'has expected bonus zero. revenue_log_yen and revenue_hit_yen are booked separately and sum to revenue_yen. '
        'Invalid/cancelled results book zero gross score. '
        'Each day’s revenue is booked when that day closes; costs are booked when incurred. The final value is the ledger after '
        'the last day and its reflection close. '
        f'S={t.score_scale:g}; lambda={t.smoothing:g}. At S=100, lambda=0.02, n=14 and winner probability 0.10, '
        'p_smoothed=0.0994285714 and log revenue'
        f'={EXAMPLE_LOG_REVENUE:.1f}; uniform gives 0. '
        'For beliefs q with a feasible interior optimum, the optimal report is p=(q-lambda/n)/(1-lambda), within O(lambda) '
        'of belief; otherwise the constrained optimum has boundary components. Fees do not depend on the reported probabilities.'
    )
    price = (a + b * 14) * t.field_yen * t.multiplier + t.call_yen * t.multiplier
    units = (
        'One evidence cell is one scalar fact slot delivered, null included; identifiers/day indices count, duplicate metadata '
        'and missing-reason strings do not. Statistics/coverage meter underlying scalar evidence inspected. '
        f'field_yen={t.field_yen:g} score-yen/cell; call_yen={t.call_yen:g} per data-tool call; '
        f'cpu_yen={t.cpu_yen:g} per CPU-second (user+system) of the isolated process, including failed code. '
        f'All three rates are multiplied by {t.multiplier:g}. Data tools: {", ".join(sorted(DATA_TOOLS))}. Control tools are free of fees; '
        'compute adds CPU and nested data-tool fees. Tool steps and response bytes still count for controls. '
        f'From the live field catalogue, a card with all three entity identifiers per runner delivers {a} + {b}*n cells: n=14 gives {a + b * 14} cells, approximately '
        f'{price:.2f} score-yen including one call (excluding CPU); omitted entity identifiers reduce the count. Score-yen is a scoring unit, not real currency. '
        'No monetary cap or requirement to spend; uniform without any incurred costs has net_score zero. ' + REPEAT_RULE
    )
    time = (
        'For every race of day D, predict runs at the previous evening cutoff, 21:00 JST. Visible inputs are that card, '
        'workouts and permitted labels released up to that cutoff. Day D results release at 20:30 JST; later objective review '
        'fields release at their review publication time. Reflection runs after the day closes and before day D+1 cards exist '
        '(21:00 JST). Attached form is frozen at its target race cutoff even when queried later. '
        "context['seed'] is constant per trajectory and is the only sanctioned randomness source; seed any random generator from it. "
        'Initial support labels require review release; online finish/abnormal labels become accessible after day close.'
    )
    feedback = (
        'Feedback schema: items[] contains this day’s races only; race_id is its identifier. '
        'top1_hits/top1_n are this day’s champion hits/scored races; top3_hits/top3_n are this day’s correct top-three '
        'choices/eligible selected choices. These are daily counts, not cumulative. '
        'Each item has status (ok, cancelled, no_valid_runners or missing_results) and revenue_yen '
        '(that race’s gross score or null), revenue_log_yen (log component or null when unscored), '
        'and revenue_hit_yen (hit component, zero when ineligible or unscored; always zero for score-yen-v1). '
        'hits contains the daily counts; ledger is cumulative. '
        'Ledger fields: currency, tariff and multiplier identify units; cost_yen is incurred cost; settled_revenue_yen '
        'is booked gross; net_score is booked gross minus cost; remaining_days counts unpredicted days; monetary_cap=null.'
        ' Ledger hit_bonus_yen is the configured bonus; revenue_log_yen/revenue_hit_yen are the cumulative '
        'booked components.'
    )
    machine = {k: getattr(lim, k) for k in ('rss_bytes', 'job_seconds', 'storage_bytes', 'artifact_bytes', 'open_files')}
    environment_text = (
        json.dumps(environment(service), sort_keys=True)
        + '\nMachine limits: '
        + json.dumps(machine, sort_keys=True)
        + '. rss_bytes is process resident memory; job_seconds is wall time per execution; storage_bytes is total managed trajectory '
        'disk usage including checkpoints; artifact_bytes bounds a file; open_files bounds open descriptors. '
        'null means no framework ceiling. CPU is still billed for every second used.'
    )
    response_rule = (
        f'Direct tool responses exceeding min(response_bytes, context_bytes // 3)={min(lim.response_bytes, lim.context_bytes // 3)} bytes '
        '(including a reserved 2048-byte envelope) are rejected without a data or call fee (a data request that first fails its '
        'max_spend check keeps that call fee); a rejected compute still pays its CPU '
        'and nested SDK fees, and its file changes are rolled back. SDK calls inside compute and framework-run jobs may return up to '
        f'response_bytes={lim.response_bytes}. max_page_size='
        + str(lim.page_size)
        + '; default_page_size='
        + str(lim.default_page_size)
        + '.'
    )
    return {
        'Task': [
            'Create and improve strategy.py defining predict(sdk, context). The framework executes and seals every race prediction. '
            'Model sessions occur during initial exploration, daily reflection and at most one repair per phase. ' + boundary,
            feedback,
        ],
        'Time contract': [time],
        'Program interface and validation': [interface],
        'Scoring': [scoring],
        'Costs and units': [units],
        'Allowances': [
            allowance_table(c),
            response_rule,
            'batch_seconds limits cumulative session compute wall-time within a phase, including repair; '
            'prediction and validation wall-time is separate. null means no ceiling.',
            'Additional session limits: ' + json.dumps(public_limits(c), sort_keys=True),
        ],
        'Sessions and persistence': [
            'Every compute starts fresh with injected sdk. Assign result to a small JSON value. Variables do not persist. '
            'Persist only small code, concise NOTES.md and fitted parameters; do not store historical samples, race packages or '
            'training arrays. The workspace may hold only flat .py/.json/.txt/.md/.bin/.pkl files; every .py file must parse and '
            'strategy.py must define predict(sdk, context), and a .json file is rejected if it is not finite JSON or contains, at any '
            'depth, an examples, runners, card, label_missing or records key whose value is a list or object. A rejected compute or '
            'save is rolled back as a whole. '
            'predict cannot modify files. Files written during a session persist only if save_strategy is called at least once '
            '(saving NOTES.md suffices) and validation does not reject the program left in the workspace when the session ends; '
            'otherwise the session-start workspace is '
            'restored, NOTES.md included. '
            'Current source, notes and last_validation are shown in the next session. Watch _budget; save when model calls <=3 '
            'or a data allowance <=20%. Oldest complete tool rounds are dropped first; the task message, strategy source and notes '
            'are never dropped (if dropping rounds cannot fit the next request, or leave room for a response at the direct '
            'ceiling before a tool call, the session ends; source inclusion has an explicit truncation manifest).'
        ],
        'Execution environment': [environment_text],
        'Glossary': [
            'Initial support: races before window day 0 whose labels were released by the initial clock. '
            'Trajectory: one agent’s exploration and scheduled prediction days. Entrant: declared runner identified by number. '
            'Cutoff: latest permitted input time. Sealed: immutable saved prediction. '
            'Validation: interface/resource checks on support cases, without model selection by outcomes. '
            'Reflection: optional program update after a day closes. '
            'net_score: cumulative gross score minus incurred fees.'
        ],
    }


def system_message(service):
    return '\n\n'.join(
        title + '\n' + '\n'.join(f'{i}. {rule}' for i, rule in enumerate(rules, 1)) for title, rules in sections(service).items()
    )


def describe(service):
    c = service.config
    source = service.state.get('binding', {}).get('source', service.source.identity)
    keep = {'dataset_fingerprint', 'import_version', 'anonymization_version', 'receipt_digest', 'sha256', 'bytes', 'sidecar_sha256'}
    return {
        'harness_version': PROTOCOL_VERSION,
        'protocol': c.protocol,
        'projection': c.projection,
        'dataset_fingerprint': {k: v for k, v in source.items() if k in keep},
        'environment': environment(service),
        'sections': sections(service),
        'allowance_table': allowances(c),
        'fields': catalog(),
        'tools': {s['function']['name']: s['function']['parameters'] for s in tool_specs(c.limits.page_size)},
        'max_page_size': c.limits.page_size,
        'default_page_size': c.limits.default_page_size,
    }


def program_context(service, task):
    """System message and task payload of a new model session."""
    state, c = service.state, service.config
    task = dict(task)
    task.update(
        total_days=len(state['schedule']),
        total_races=sum(len(b['races']) for b in state['schedule']),
        remaining_prediction_days=len(state['schedule']) - state['index'] - int(state['phase'] == 'reflect'),
        current_strategy=state.get('validated_strategy'),
        recent_strategy_errors=state.get('strategy_failures', [])[-3:],
        **session_memory(service),
        ledger=summary(state, c),
        tariff=c.tariff.public(),
    )
    return system_message(service), task

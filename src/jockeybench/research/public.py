"""Portable Active/Frozen lifecycle using the shared JockeyBench engine.

This is a new versioned deployment profile. Historical fixed-run preparation and
archive loaders remain provenance tools, not dependencies of this entry point.
"""

import json
import re
from collections import Counter
from dataclasses import replace
from pathlib import Path

from jockeybench.canonical import code_hash, digest
from jockeybench.clock import cutoff, release
from jockeybench.data.source import Source
from jockeybench.errors import Rejected
from jockeybench.ledger import summary
from jockeybench.sandbox.environment import probe
from jockeybench.storage import read_checkpoint, valid_run_id

from .aligned.scoring import checkpoint_components
from .experience.runner import validate_schedule
from .finegrained_v3.frame import extract, make_frame
from .finegrained_v3.native_replay import replay_economic
from .finegrained_v3.policy_builder import Registry, revise, write
from .finegrained_v3.policy_contract import source_hash, validate_source
from .finegrained_v3.runtime import Predictor, predict, verify_semantics
from .finegrained_v3.source_binding import VerifiedRun
from .policy_amortized.lifecycle import coordinate
from .policy_amortized.model_usage import summarize
from .policy_amortized.service import PolicyService
from .profile import ARMS, VERSION, provider_settings, validate_config
from .runner import settle


def run_path(config, run_id):
    if not valid_run_id(run_id):
        raise Rejected('Invalid run name')
    return Path(config.managed_root) / run_id


def _load(path):
    return json.loads(Path(path).read_text())


def read_manifest(out):
    manifest = _load(Path(out) / 'manifest.json')
    if manifest.get('digest') != digest({k: v for k, v in manifest.items() if k != 'digest'}):
        raise Rejected('Research manifest digest mismatch')
    if manifest.get('profile') != VERSION or manifest.get('code') != code_hash():
        raise Rejected('Research source/profile changed; retain the exact installed version')
    return manifest


def _verify_inputs(out, manifest, initial=None):
    if read_manifest(out) != manifest:
        raise Rejected('Research manifest changed during execution')
    if initial is not None and _load(out / 'initial-policy.json') != initial:
        raise Rejected('Research initial program changed during execution')


def _matched_initial(config, initial_from, manifest, out, registry):
    original = run_path(config, initial_from)
    previous = read_manifest(original)
    if previous['arm'] != 'adaptive':
        raise Rejected('Frozen requires initial input from an Adaptive trajectory')
    for key in ('config', 'provider', 'source', 'code', 'environment', 'schedule'):
        if previous[key] != manifest[key]:
            raise Rejected('Matched initial input differs in ' + key)
    status = _load(original / 'status.json')
    if status.get('status') != 'completed':
        raise Rejected('Matched Adaptive trajectory must be complete')
    policy = _load(original / 'initial-policy.json')
    validation = validate_source(policy['source'])
    if source_hash(policy['source']) != policy['policy_hash'] or validation != policy['validation_result']:
        raise Rejected('Matched initial policy failed validation')
    if status.get('initial_policy_digest') != digest(policy):
        raise Rejected('Matched initial policy provenance changed')
    version = policy.get('policy_version')
    if not isinstance(version, str) or not re.fullmatch(r'v[0-9]+', version):
        raise Rejected('Matched initial policy has an invalid version')
    # Warmup may save several candidates. Preserve the accepted P0 record,
    # including its version and session usage, instead of renumbering it.
    stem = registry.folder / ('research_policy_' + version)
    with stem.with_suffix('.py').open('x') as handle:
        handle.write(policy['source'])
    with stem.with_suffix('.json').open('x') as handle:
        json.dump(policy, handle, ensure_ascii=False, indent=2)
    adopted = policy
    accounting = _load(original / 'warmup-accounting.json')
    if digest(accounting) != status.get('warmup_accounting_digest'):
        raise Rejected('Matched warmup accounting changed')
    write(out / 'warmup-accounting.json', accounting)
    write(
        out / 'matched-initial.json',
        {
            'source_run_id': initial_from,
            'source_manifest_digest': previous['digest'],
            'initial_policy_digest': digest(policy),
            'policy_hash': policy['policy_hash'],
            'warmup_reexecuted': False,
        },
    )
    return adopted


def _attach(run):
    service = PolicyService(run.source, run.config, run.state, run.persist)
    service.executor = run.executor
    run.service = service
    run.executor.service = service
    return run


def _api_totals(sessions):
    fields = (
        'logical_requests',
        'provider_attempts',
        'input_tokens',
        'output_tokens',
        'estimated_cost_usd',
        'unknown_cost_outputs',
        'failed_attempts',
    )
    result = {key: sum(session[key] for session in sessions) for key in fields}
    result['cost_coverage_complete'] = not (result['unknown_cost_outputs'] or result['failed_attempts'])
    return result


def execute(config, run_id, settings, *, arm='adaptive', initial_from=None, source_factory=None, warmup_caller=None):
    """Run a fresh trajectory. CLI guards the experiment host before calling here.

    The injected source/caller are for synthetic fixture tests; production uses
    Source and the metered provider transport. Existing outputs never resume.
    """
    validate_config(config)
    settings = provider_settings(settings)
    if arm not in ARMS or (arm == 'frozen') != bool(initial_from):
        raise Rejected('Use adaptive without --initial-from, or frozen with its matched Adaptive run')
    out = run_path(config, run_id)
    if out.exists():
        raise Rejected('Use a fresh research run name; no restart, resume or overwrite')
    if initial_from:
        run_path(config, initial_from)
    factory = source_factory or Source
    source = factory(config.source, config.limits.cache_mib)
    try:
        schedule = source.schedule(config.start, config.end, config.smoke_days, config.races_per_day, config.schedule_mode)
        validate_schedule(schedule)
        source_manifest = source.manifest()
    finally:
        source.close()
    manifest = {
        'profile': VERSION,
        'arm': arm,
        'initial_from': initial_from,
        'config': config.public(),
        'source': source_manifest,
        'code': code_hash(),
        'provider': settings,
        'schedule': schedule,
        'environment': probe(config.python, config.limits.numerical_threads),
    }
    manifest['digest'] = digest(manifest)
    out.mkdir(parents=True, exist_ok=False)
    write(out / 'manifest.json', manifest)
    write(out / 'status.json', {'status': 'running', 'profile': VERSION, 'completed_days': []})
    run_config = replace(config, managed_root=str(out / 'runs'))
    registry = Registry(out / 'policies')
    events, reports, completed, sessions, race_usage = [], [], [], [], []
    initial = None

    def verify():
        _verify_inputs(out, manifest, initial)

    def emit(kind, **data):
        events.append({'sequence': len(events), 'kind': kind, **data})
        write(out / 'events.json', events)
        if kind == 'day_complete':
            completed.append(data['day'])

    def open_run(ident, policy):
        source = factory(config.source, config.limits.cache_mib)
        try:
            run = _attach(VerifiedRun(run_config, ident, Predictor(settings, policy), source=source))
        except BaseException:
            source.close()
            raise
        if run.state['binding']['source'] != manifest['source']:
            run.close()
            raise Rejected('Research source changed during trajectory')
        if run.state['binding']['environment'] != manifest['environment']:
            run.close()
            raise Rejected('Research environment changed during trajectory')
        return run

    def one(day, race, policy):
        verify()
        ident = 'PAR-' + str(sum(e['kind'] == 'sealed' for e in events) + 1).zfill(6)
        if (out / 'runs' / ident).exists():
            raise Rejected('Existing race storage; no restart')
        run = open_run(ident, policy)
        try:
            state = run.state
            bound = run.source.con.execute('SELECT day FROM races WHERE id=?', (run.source.raw_race(race),)).fetchone()
            if not bound or bound['day'] != day:
                raise Rejected('Research race/day binding mismatch')
            state.update(
                schedule=[{'day': day, 'races': [race]}],
                clock=cutoff(day),
                initial_clock=cutoff(day),
                trajectory_bound=True,
                research_manifest_digest=manifest['digest'],
            )
            run.persist()
            run.preflight()
            if predict(run, run.agent) != (race, day) or state['scores'] or state['release_ledger']:
                raise Rejected('Research seal/release boundary mismatch')
            state['prediction_phase_cost'] = summary(state, run_config)
            state.pop('strategy_job', None)
            state.pop('strategy_job_usage', None)
            state['phase'] = 'awaiting_daily_release'
            run.persist()
            return ident
        finally:
            run.close()

    def released(day, sealed, policy):
        verify()
        expected = next(b['races'] for b in schedule if b['day'] == day)
        # Validate the entire day before revealing any outcome to feedback.
        checkpoints = [read_checkpoint(out / 'runs' / ident / 'checkpoint.json.gz') for ident in sealed]
        if len(checkpoints) != len(expected):
            raise Rejected('Incomplete day release')
        for state, race in zip(checkpoints, expected, strict=True):
            if (
                state['schedule'] != [{'day': day, 'races': [race]}]
                or set(state['submissions']) != {race}
                or state['release_ledger']
                or state['scores']
            ):
                raise Rejected('All predictions must be sealed before daily release')
        rows = []
        for ident, race in zip(sealed, expected, strict=True):
            run = open_run(ident, policy)
            try:
                state = run.state
                settle(run, race, day)
                state['scores'][race]['format_fallback'] = state['submissions'][race]['format_fallback']
                state['phase'] = 'reflect'
                result = run.service.call('results', {'race_id': race})
                state['experience_result_cost'] = result['_cost']
                state['aligned_scores'] = checkpoint_components(state)
                state['phase'] = 'completed'
                run.persist()
                core = replay_economic(run_config, state, source=run.source)
                semantic = verify_semantics(state)
                row = extract(state, policy, release(day))
                race_usage.append(summarize(state, 'complete_session', ident))
                rows.append(row)
                reports.append({'run_id': ident, 'row': row, 'core_audit': core, 'semantic_audit': semantic})
                write(out / 'race-reports.json', reports)
                emit('result_released', day=day, race_id=race, clock=release(day))
            finally:
                run.close()
        return rows

    def feedback(rows, history, day, policy, clock, races):
        frame = make_frame(rows, history, day, policy, clock, races)
        write(out / ('frame-' + day + '.json'), frame)
        return frame

    def update(policy, frame, clock):
        verify()
        revised, session = revise(ARMS[arm], policy, frame, clock, out, settings, registry)
        sessions.append(session)
        from .finegrained_v3.updater_context import lifecycle_checkpoint

        receipt = lifecycle_checkpoint(session)
        write(out / ('updater-checkpoint-' + frame['day'] + '.json'), receipt)
        if not receipt['passed']:
            raise Rejected('Updater did not finish its required lifecycle; trajectory stopped')
        return revised

    try:
        if arm == 'frozen':
            initial = _matched_initial(config, initial_from, manifest, out, registry)
        else:
            from .portable_warmup import build

            initial = build(out, config, settings, source_factory=factory, caller=warmup_caller)
        write(out / 'initial-policy.json', initial)
        final_policy, history = coordinate(schedule, initial, ARMS[arm], one, released, feedback, update, emit)
        verify()
        warmup = _load(out / 'warmup-accounting.json')
        warmup_usage = warmup['builder_session_usage']
        updater_usage = [summarize(s, 'complete_session') for s in sessions]
        attributed = [warmup_usage, *race_usage, *updater_usage]
        incurred = [*race_usage, *updater_usage] + ([warmup_usage] if arm == 'adaptive' else [])
        api_usage = {
            'currency': 'USD',
            'cost_basis': 'Sum of available provider estimates or configured token rates; unknown costs are counted separately.',
            'shared_warmup': _api_totals([warmup_usage]),
            'predictors': _api_totals(race_usage),
            'updaters': _api_totals(updater_usage),
            'attributed_total': _api_totals(attributed),
            'incurred_this_run': _api_totals(incurred),
            'warmup_reexecuted': arm == 'adaptive',
        }
        result = {
            'profile': VERSION,
            'setting': 'active_research',
            'arm': arm,
            'completed': True,
            'run_id': run_id,
            'manifest_digest': manifest['digest'],
            'races': len(history),
            'days': len(completed),
            'initial_policy_hash': initial['policy_hash'],
            'final_policy_hash': final_policy['policy_hash'],
            'delivery_totals': dict(Counter(row['delivery'] for row in history)),
            'score': {
                'evaluator': 'common-aligned-delivery-v1',
                'race_net_total': sum(row['aligned_net'] for row in history),
                'warmup_weighted_cost': warmup['weighted_ledger_cost'],
                'net_after_warmup': sum(row['aligned_net'] for row in history) - warmup['weighted_ledger_cost'],
            },
            'warmup': {'reexecuted': arm == 'adaptive', 'matched_from': initial_from, 'accounting': warmup},
            'updater_sessions': len(sessions),
            'currency': 'benchmark_score_units_not_money',
            'api_cost_in_score': False,
            'api_usage': api_usage,
        }
        write(out / 'report.json', result)
        write(
            out / 'status.json',
            {
                'status': 'completed',
                'profile': VERSION,
                'completed_days': completed,
                'initial_policy_digest': digest(initial),
                'warmup_accounting_digest': digest(warmup),
            },
        )
        return result
    except BaseException as exc:
        write(
            out / 'status.json',
            {
                'status': 'stopped',
                'profile': VERSION,
                'completed_days': completed,
                'error_type': type(exc).__name__,
                'reason': str(exc),
                'restart_allowed': False,
            },
        )
        raise


def audit(config, run_id, *, source_factory=None):
    """Verify a complete ordered trajectory, then replay its receipts and scores."""
    out = run_path(config, run_id)
    manifest = read_manifest(out)
    validate_config(config)
    if config.public() != manifest['config']:
        raise Rejected('Audit configuration differs from research manifest')
    if manifest.get('arm') not in ARMS or (manifest['arm'] == 'frozen') != bool(manifest.get('initial_from')):
        raise Rejected('Research arm/initial input binding mismatch')
    validate_schedule(manifest['schedule'])
    status = _load(out / 'status.json')
    days = [batch['day'] for batch in manifest['schedule']]
    if status.get('status') != 'completed' or status.get('profile') != VERSION or status.get('completed_days') != days:
        raise Rejected('Research trajectory is incomplete')

    def initial_input(directory, receipt):
        policy = _load(directory / 'initial-policy.json')
        version = policy.get('policy_version', '')
        if not isinstance(version, str) or not version.startswith('v') or not version[1:].isdigit():
            raise Rejected('Research initial policy version is invalid')
        if (
            source_hash(policy['source']) != policy['policy_hash']
            or validate_source(policy['source']) != policy['validation_result']
            or digest(policy) != receipt.get('initial_policy_digest')
        ):
            raise Rejected('Research initial policy provenance changed')
        stem = directory / 'policies' / ('research_policy_' + version)
        if _load(stem.with_suffix('.json')) != policy or stem.with_suffix('.py').read_text() != policy['source']:
            raise Rejected('Research initial policy registry changed')
        accounting = _load(directory / 'warmup-accounting.json')
        if digest(accounting) != receipt.get('warmup_accounting_digest'):
            raise Rejected('Research warmup accounting changed')
        return policy, accounting

    initial, accounting = initial_input(out, status)
    if manifest['arm'] == 'frozen':
        original = run_path(config, manifest['initial_from'])
        previous = read_manifest(original)
        previous_status = _load(original / 'status.json')
        if (
            previous.get('arm') != 'adaptive'
            or previous_status.get('status') != 'completed'
            or previous_status.get('profile') != VERSION
            or previous_status.get('completed_days') != days
        ):
            raise Rejected('Matched Adaptive trajectory is incomplete')
        for key in ('config', 'provider', 'source', 'code', 'environment', 'schedule'):
            if previous[key] != manifest[key]:
                raise Rejected('Matched initial input differs in ' + key)
        original_policy, original_accounting = initial_input(original, previous_status)
        matched = _load(out / 'matched-initial.json')
        expected_match = {
            'source_run_id': manifest['initial_from'],
            'source_manifest_digest': previous['digest'],
            'initial_policy_digest': digest(initial),
            'policy_hash': initial['policy_hash'],
            'warmup_reexecuted': False,
        }
        if initial != original_policy or accounting != original_accounting or matched != expected_match:
            raise Rejected('Frozen initial policy or matched provenance changed')
        if (
            len(list((out / 'policies').glob('research_policy_v*.json'))) != 1
            or list(out.glob('policy_update-*.json'))
            or (out / 'warmup-runs').exists()
        ):
            raise Rejected('Frozen trajectory contains a warmup or policy update')

    expected = [(batch['day'], race) for batch in manifest['schedule'] for race in batch['races']]
    run_ids = ['PAR-' + str(i + 1).zfill(6) for i in range(len(expected))]
    actual = sorted((out / 'runs').glob('PAR-*'))
    if [path.name for path in actual] != run_ids or any(not path.is_dir() or path.is_symlink() for path in actual):
        raise Rejected('Research race directories differ from the complete ordered schedule')
    run_config = replace(config, managed_root=str(out / 'runs'))
    factory = source_factory or Source
    result = []
    for ident, (day, race) in zip(run_ids, expected, strict=True):
        path = out / 'runs' / ident / 'checkpoint.json.gz'
        if not path.is_file() or path.is_symlink():
            raise Rejected('Research race checkpoint is missing or unsafe')
        state = read_checkpoint(path)
        if state.get('research_manifest_digest') != manifest['digest']:
            raise Rejected('Research checkpoint manifest binding mismatch')
        if (
            state.get('run_id') != ident
            or state.get('schedule') != [{'day': day, 'races': [race]}]
            or not state.get('trajectory_bound')
            or state.get('index') != 0
        ):
            raise Rejected('Research checkpoint does not match its ordered race identity')
        if (
            state.get('phase') != 'completed'
            or state.get('completed') is not True
            or set(state.get('submissions', {})) != {race}
            or set(state.get('scores', {})) != {race}
            or set(state.get('feedback', {})) != {race}
            or state.get('release_ledger') != {race: release(day)}
            or state.get('clock') != release(day)
            or state['submissions'][race].get('clock') != cutoff(day)
        ):
            raise Rejected('Research race is not completely sealed, released and scored')
        for key in ('source', 'environment', 'code', 'config'):
            if state['binding'].get(key) != manifest[key]:
                raise Rejected('Research checkpoint ' + key + ' differs from trajectory manifest')
        if state.get('policy_provider_settings') != manifest['provider']:
            raise Rejected('Research prediction provider differs from trajectory manifest')
        if manifest['arm'] == 'frozen' or day == days[0]:
            identity = {key: initial[key] for key in ('policy_hash', 'policy_version', 'parent_policy_hash', 'created_after_day')}
            if state.get('policy_identity') != identity or state.get('policy_source') != initial['source']:
                raise Rejected('Research race does not use its bound initial policy')
        source = factory(config.source, config.limits.cache_mib)
        try:
            result.append(
                {'run_id': state['run_id'], 'core': replay_economic(run_config, state, source=source), 'semantic': verify_semantics(state)}
            )
        finally:
            source.close()
    return {'profile': VERSION, 'verified_races': len(result), 'results': result}

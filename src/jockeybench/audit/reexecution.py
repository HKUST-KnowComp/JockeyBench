"""Re-execution audit: run every sealed strategy version again on its races and compare predictions.

Each race is replayed at its sealed clock with the read history that preceded
it, in a fresh temporary store, with fees quoted but not booked.
"""

import copy
import shutil
import tempfile
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace

from ..canonical import code_hash
from ..data.source import Source
from ..errors import Rejected
from ..lifecycle import context, job, usage
from ..protocol.service import Service
from ..sandbox.environment import probe
from ..sandbox.executor import Executor
from ..scoring import uniform
from ..storage import Storage

TOLERANCE = 1e-9


def same_prediction(left, right, tolerance=TOLERANCE):
    if left.get('ranking') != right.get('ranking'):
        return False
    a, b = left['probabilities'], right['probabilities']  # validate() makes them mandatory
    return a.keys() == b.keys() and all(abs(a[k] - b[k]) <= tolerance for k in a)


def _add_reads(seen, rows):
    for row in rows:
        if row.get('status') == 'ok' and 'read_key' in row:
            values = seen.setdefault(row['read_key'], [])
            if row['data_digest'] not in values:
                values.append(row['data_digest'])


def reexecute(config, checkpoint, source=None):
    binding = checkpoint['binding']
    if binding['code'] != code_hash() or binding['config'] != config.public():
        raise Rejected('Re-execution requires the sealed code and configuration')
    if binding.get('environment') != probe(config.python, config.limits.numerical_threads):
        raise Rejected('Re-execution environment differs from checkpoint')
    owned = source is None
    source = source or Source(config.source, config.limits.cache_mib)
    try:
        if source.manifest() != binding['source']:
            raise Rejected('Re-execution source mismatch')
        versions = Path(config.managed_root) / checkpoint['run_id'] / 'versions'
        results = {}
        with tempfile.TemporaryDirectory(prefix='reexecute-', dir=config.managed_root) as temporary, ExitStack() as cleanup:
            storage = Storage(temporary, 'audit', config.limits.storage_bytes)
            cleanup.callback(storage.close)
            state = copy.deepcopy(checkpoint)
            state.update(
                audit=[],
                usage=usage(),
                compute_jobs=[],
                ledger={'entries': [], 'ids': {}, 'cost_micro': 0, 'revenue_micro': 0},
                economic_operations={},
                cursors={},
                submissions={},
                charging_disabled=True,
                phase='predict',
            )
            service = Service(source, config, state)
            executor = Executor(config, storage, service)
            service.executor = executor
            executor.preflight([])  # This is an experiment-host action, independent of the original run.
            run = SimpleNamespace(state=state, config=config, source=source, service=service, executor=executor, persist=lambda: None)
            race_days = {r: i for i, batch in enumerate(state['schedule']) for r in batch['races']}
            seen, position = {}, 0
            ordered = [race for batch in state['schedule'] for race in batch['races'] if race in checkpoint['submissions']]
            if set(ordered) != set(checkpoint['submissions']):
                raise Rejected('Sealed race is outside the bound schedule')
            for race in ordered:
                sealed = checkpoint['submissions'][race]
                version = sealed.get('strategy')
                group = results.setdefault(version or 'uniform', {'matches': 0, 'mismatches': 0, 'races': []})
                state.update(clock=sealed['clock'], index=race_days[race], usage=usage(), phase='predict', validated_strategy=version)
                attempt = next(
                    (
                        a
                        for a in checkpoint.get('prediction_jobs', {}).get(race, {}).get('attempts', [])
                        if a['strategy'] == version and a['status'] == 'ok'
                    ),
                    {},
                )
                # Repeat-read identities as they stood when this prediction ran.
                end = attempt.get('audit_start', position)
                if end < position:
                    seen, position = {}, 0
                _add_reads(seen, checkpoint['audit'][position:end])
                position = end
                state['read_digests'] = copy.deepcopy(seen)
                state['usage'] = copy.deepcopy(attempt.get('session_usage', usage()))
                state['feedback'] = {r: v for r, v in checkpoint['feedback'].items() if checkpoint['release_ledger'][r] <= state['clock']}
                error = None
                try:
                    if version:
                        src, dst = versions / version, executor.versions / version
                        if src.is_symlink() or not src.is_dir():
                            raise Rejected('Missing sealed strategy version')
                        if not dst.exists():
                            shutil.copytree(src, dst)
                        executor.restore(version)
                        actual = job(run, race)
                    else:
                        actual = uniform(race, context(run, race)['runner_numbers'], config.seed)
                    matches = same_prediction(actual, sealed['prediction'])
                except Rejected as exc:
                    matches, error = False, str(exc)
                group['matches' if matches else 'mismatches'] += 1
                group['races'].append({'race_id': race, 'matches': matches, **({'error': error} if error else {})})
                state['submissions'][race] = copy.deepcopy(sealed)
            return {
                'run_id': checkpoint['run_id'],
                'by_strategy': results,
                'matches': sum(v['matches'] for v in results.values()),
                'mismatches': sum(v['mismatches'] for v in results.values()),
                'tolerance': TOLERANCE,
                'charging_disabled': True,
                'source_unchanged': source.manifest() == binding['source'],
            }
    finally:
        if owned:
            source.close()

"""A trajectory: one agent, one configuration, one checkpoint.

The checkpoint is bound to the source manifest, the public configuration,
the runtime code hash, the agent identity and the sandbox environment; a
resume with any of these changed is refused.
"""

import copy
import shutil
from pathlib import Path

from .canonical import code_hash
from .clock import cutoff
from .data.source import Source
from .errors import Rejected
from .ledger import ledger
from .lifecycle import INITIAL_KEYS, run_trajectory, usage
from .protocol.metering import PROGRAM_BUDGET_VERSION
from .protocol.service import Service
from .sandbox.environment import probe
from .sandbox.executor import Executor
from .storage import Storage, read_checkpoint, tree_bytes, valid_run_id

# State carried from a shared warmup into each arm that starts from it. The provider history is copied
# separately, with its own defaults.
PROVIDER_HISTORY_KEYS = ('model_outputs', 'provider_attempts', 'usage_history', 'audit')
SHARED_INITIAL_KEYS = tuple(k for k in INITIAL_KEYS if k not in (*PROVIDER_HISTORY_KEYS, 'provider_failures'))


class Run:
    def __init__(self, config, run_id, agent, initial_from=None, source=None):
        self.config, self.agent = config, agent
        self.source = source or Source(config.source, config.limits.cache_mib)
        self.storage = Storage(config.managed_root, run_id, config.limits.storage_bytes)
        binding = {
            'source': self.source.manifest(),
            'config': config.public(),
            'code': code_hash(),
            'agent': agent.identity,
            'budget_version': PROGRAM_BUDGET_VERSION,
            'projection': config.projection,
            'feedback': 'results-v1',
            'environment': probe(config.python, config.limits.numerical_threads),
        }
        state = self.storage.load()
        if state:
            if state['binding'] != binding:
                raise Rejected('Source, code, agent or configuration differs from checkpoint')
        else:
            schedule = self.source.schedule(config.start, config.end, config.smoke_days, config.races_per_day, config.schedule_mode)
            state = {
                'run_id': run_id,
                'binding': binding,
                'schedule': schedule,
                'index': 0,
                'phase': 'warmup',
                'clock': cutoff(schedule[0]['day']),
                'initial_clock': cutoff(schedule[0]['day']),
                'submissions': {},
                'feedback': {},
                'scores': {},
                'release_ledger': {},
                'strategy_digest': None,
                'initial_strategy': None,
                'validated_strategy': None,
                'valid_strategies': [],
                'usage': usage(),
                'usage_history': [],
                'audit': [],
                'cursors': {},
                'model_outputs': [],
                'pending': None,
                'economic_sequence': 0,
                'completed': False,
            }
            ledger(state)
        self.state = state
        self.service = Service(self.source, config, state, self.persist)
        self.executor = Executor(config, self.storage, self.service)
        self.service.executor = self.executor
        if state['strategy_digest']:
            self.executor.restore(state['strategy_digest'])
        else:
            self.executor.snapshot()
        if initial_from and state['initial_strategy'] is None:
            self.import_initial(initial_from)
        self.persist()

    def persist(self):
        self.storage.save(self.state)

    def import_initial(self, run_id):
        """Start this arm from another run's completed warmup (same source, agent, code, budgets and environment)."""
        if not valid_run_id(run_id):
            raise Rejected('Invalid initial run name')
        base = self.storage.root / run_id
        if base.is_symlink():
            raise Rejected('Unsafe initial strategy source')
        try:
            old = read_checkpoint(base / 'checkpoint.json.gz')
        except Rejected as exc:
            raise Rejected('Initial checkpoint corrupted') from exc
        binding = self.state['binding']
        comparable = lambda c: {k: v for k, v in c.items() if k != 'arm'}
        if any(old['binding'][k] != binding[k] for k in ('source', 'agent', 'code')) or comparable(old['binding']['config']) != comparable(
            binding['config']
        ):
            raise Rejected('Shared initialization requires matching source, agent, code and budgets')
        if old['binding'].get('environment') != binding.get('environment'):
            raise Rejected('Shared initialization requires the same execution environment')
        ident = old['initial_strategy']
        if not ident:
            raise Rejected('Initial exploration is not complete')
        initial = old.get('initial_economic', {})
        self.state.update(
            initial_strategy=ident,
            common_initial_from=run_id,
            phase='predict',
            usage=usage(),
            initial_audit_run_id=old.get('initial_audit_run_id', old['run_id']),
            initial_audit_arm=old.get('initial_audit_arm', old['binding']['config']['arm']),
        )
        for key in SHARED_INITIAL_KEYS:
            if key in initial:
                self.state[key] = copy.deepcopy(initial[key])
        self.state['initial_economic'] = copy.deepcopy(initial)
        for key in PROVIDER_HISTORY_KEYS:
            self.state[key] = copy.deepcopy(initial.get(key, []))
        self.state['provider_failures'] = initial.get('provider_failures', 0)
        # Notes-only snapshots and earlier validated fallback programs can
        # differ from initial_strategy; copy every referenced version.
        referenced = set(self.state['valid_strategies']) | {
            ident,
            self.state.get('accepted_workspace'),
            self.state.get('validated_strategy'),
        }
        for version in sorted(referenced - {None}):
            src, dst = base / 'versions' / version, self.executor.versions / version
            if not dst.exists():
                self.storage.check(tree_bytes(src))
                shutil.copytree(src, dst)
        self.executor.restore(ident)

    def preflight(self):
        """Prove that sandboxed code can read neither the archive, its key, a canary nor the checkpoint."""
        canary = self.storage.path / 'private-canary.txt'
        if not canary.exists():
            self.storage.check(64)
            canary.write_text('sandbox boundary canary; contains no credentials\n')
        key = Path(self.config.source).parent / 'namespace.key'
        denied = [self.config.source, canary, self.storage.path / 'checkpoint.json.gz']
        if key.exists():
            denied.append(key)
        self.state['isolation'] = self.executor.preflight(denied)
        self.persist()
        return self.state['isolation']

    def run(self, stop_after=None, warmup_only=False):
        return run_trajectory(self, stop_after, warmup_only)

    def report(self):
        from .reporting import report

        return report(self)

    def close(self):
        self.source.close()
        self.storage.close()

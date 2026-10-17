"""Public historical-support builder over the installed JockeyBench engine.

This is ``jockeybench-research-warmup-v1``, not the archived paper warmup.
The acquisition policy language and builder are shared with finegrained_v3,
but source access, tools, isolation and provider accounting use this package.
Only the saved acquisition source transfers into the evaluated trajectory.
"""

import copy
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from jockeybench import ledger
from jockeybench.agents.compatible import CompatibleAgent
from jockeybench.canonical import code_hash
from jockeybench.clock import cutoff
from jockeybench.data.catalog import catalog
from jockeybench.data.source import Source
from jockeybench.errors import Rejected
from jockeybench.protocol.service import Service
from jockeybench.protocol.tools import tool_specs
from jockeybench.runner import Run

from .finegrained_v3 import transport, warmup
from .finegrained_v3.policy_builder import toolset
from .finegrained_v3.policy_contract import CONTRACT
from .finegrained_v3.source_binding import ensure

VERSION = 'jockeybench-research-warmup-v1'


@dataclass(frozen=True)
class Boundary:
    """Support predates the configured origin and is reviewed at the first cutoff."""

    origin: str
    horizon: str

    def __post_init__(self):
        origin, horizon = date.fromisoformat(self.origin), date.fromisoformat(self.horizon)
        if not date(2024, 1, 1) <= origin <= horizon < date(2026, 1, 1):
            raise Rejected('Invalid public warmup support boundary')

    @property
    def clock(self):
        return cutoff(self.horizon)

    def public(self):
        return {'origin': self.origin, 'horizon': self.horizon, 'clock': self.clock, 'support_scope': 'reviewed_pre_origin'}


def historical_views(connection, boundary):
    """Constrain every trusted broad scan; the immutable main archive stays intact.

    Values are ISO dates and integers validated by Boundary. SQLite does not
    accept query parameters in view definitions. Only TEMP objects are created.
    """
    day, clock = date.fromisoformat(boundary.origin).isoformat(), boundary.clock
    connection.execute('PRAGMA query_only=OFF')
    try:
        connection.execute('PRAGMA temp_store=MEMORY')
        connection.executescript(f"""
            CREATE TEMP VIEW races AS
                SELECT * FROM main.races
                WHERE day<'{day}' AND pre_at<={clock} AND review_at<={clock};
            CREATE TEMP VIEW entries AS
                SELECT e.* FROM main.entries e JOIN races r ON r.id=e.race_id;
            CREATE TEMP VIEW expanded_race_ids AS
                SELECT e.* FROM main.expanded_race_ids e JOIN races r ON r.id=e.race_id;
        """)
    finally:
        connection.execute('PRAGMA query_only=ON')


class HistoricalSource(Source):
    """A fresh Source connection whose entire visible archive is legal support."""

    @classmethod
    def restrict(cls, source, boundary):
        # Ownership of this fresh connection transfers to the returned source.
        # Retain instance overrides used by synthetic in-memory fixtures.
        scoped = cls.__new__(cls)
        scoped.__dict__.update(source.__dict__)
        scoped.__dict__.pop('_connections_index', None)
        scoped.boundary = boundary
        historical_views(scoped.con, boundary)
        return scoped

    def schedule(self, *args, **kwargs):
        # Anchor the frozen clock without exposing formal cards or schedule IDs.
        return [{'day': self.boundary.horizon, 'races': []}]

    def _boundary(self, clock, origin=None):
        if clock != self.boundary.clock or (origin is not None and origin != self.boundary.origin):
            raise Rejected('Historical support boundary mismatch')

    def race(self, public, clock):
        self._boundary(clock)
        return super().race(public, clock)

    def card(self, public, clock, origin_day=None):
        self._boundary(clock, origin_day)
        return super().card(public, clock, origin_day)

    def history_page(self, clock, origin, after, limit, anchor=None, filters=None, order='recent', scope='all', include_form=0):
        self._boundary(clock, origin)
        filters = dict(filters or {})
        maximum = filters.get('day_max', -1)
        if type(maximum) is not int or maximum >= 0:
            raise Rejected('Historical support requires a negative day_max')
        filters['day_max'] = maximum
        return super().history_page(clock, origin, after, limit, anchor, filters, order, 'support', include_form)

    def objective_result(self, public, clock, origin):
        self._boundary(clock, origin)
        return super().objective_result(public, clock, origin)

    def form(self, public, clock, origin, k):
        self._boundary(clock, origin)
        return super().form(public, clock, origin, k)


def tools(page_size):
    return [t for t in tool_specs(page_size) if t['function']['name'] in warmup.READ | {'compute'}] + toolset()


class SupportService(Service):
    def call(self, name, arguments=None, from_sdk=False, operation_id=None):
        ensure(self)
        boundary = self.source.boundary
        if self.state['clock'] != boundary.clock or self.state['phase'] != 'warmup':
            raise Rejected('Frozen support session')
        if name not in warmup.READ | {'compute'} or (from_sdk and name == 'compute'):
            raise Rejected('Unavailable support capability')
        return super().call(name, arguments, from_sdk, operation_id)

    def dispatch(self, name, args, meter=None):
        if name == 'describe':
            return {
                'warmup_version': VERSION,
                'tools': tools(self.config.limits.page_size),
                'runtime_contract': CONTRACT,
                'tariff': self.config.tariff.public(),
                'boundary': self.source.boundary.public(),
                'support': 'Reviewed historical races before configured origin; no formal cards or outcomes.',
                'compute': 'sdk.call(tool, **arguments); fresh isolated Python analysis; result must be JSON serializable.',
                'limits': self.config.public()['limits'],
                'schema': catalog(),
            }
        return super().dispatch(name, args, meter)


def build(out, config, settings, *, source_factory=None, caller=None):
    """Build an initial acquisition policy with the public shared runtime.

    ``source_factory(path, cache_mib)`` may inject a fresh synthetic Source in
    tests; its connection is owned and closed here. ``caller`` has the existing
    research transport signature ``(service, settings, request_body, phase)``.
    Omitting it uses the trusted provider transport and its cost accounting.
    """
    out = Path(out)
    config = replace(config, managed_root=str(out / 'warmup-runs'))
    settings = copy.deepcopy(settings)
    if settings.get('research_profile', 'jockeybench-research-v1') != 'jockeybench-research-v1':
        raise Rejected('Use the explicit public research profile for portable warmup')
    settings['research_profile'] = 'jockeybench-research-v1'
    source = (source_factory or Source)(config.source, config.limits.cache_mib)
    try:
        manifest = source.manifest()
        schedule = source.schedule(config.start, config.end, config.smoke_days, config.races_per_day, config.schedule_mode)
        boundary = Boundary(config.start, schedule[0]['day'])
        source = HistoricalSource.restrict(source, boundary)
        # Public transport configuration can contain research-only profile tags;
        # the engine agent binding still uses the common provider settings.
        agent_settings = {k: v for k, v in settings.items() if k != 'research_profile'}
        agent = CompatibleAgent(agent_settings)
    except Exception:
        source.close()
        raise

    def accounting(state):
        result = warmup.accounting(state, reconcile=ledger.reconcile)
        races = sum(len(batch['races']) for batch in schedule)
        result.pop('warmup_weighted_cost_per_324')
        result.pop('warmup_api_usd_per_324')
        result.update(
            normalization_races=races,
            warmup_weighted_cost_per_race=result['weighted_ledger_cost'] / races,
            warmup_api_usd_per_race=result['api_cost_usd_estimate'] / races,
        )
        return result

    backend = SimpleNamespace(
        config=config,
        settings=settings,
        agent=agent,
        source=source,
        run_type=Run,
        service_class=SupportService,
        tools=tools(config.limits.page_size),
        rejected=Rejected,
        reconcile=ledger.reconcile,
        accounting=accounting,
        request=transport.request,
        clock=boundary.clock,
        origin=boundary.origin,
        horizon=boundary.horizon,
        lineage={'warmup_version': VERSION, 'runtime_code_hash': code_hash(), 'support_boundary': boundary.public()},
    )
    try:
        return warmup.build(out, {'source_manifest': manifest}, caller, backend=backend)
    finally:
        # Also close if engine initialization or source binding fails before
        # the shared builder enters its session cleanup. SQLite close is idempotent.
        source.close()

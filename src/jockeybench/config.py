"""Run configuration. Defaults are the paper's main protocol.

A trajectory binds ``Config.public()``; changing any value requires a fresh run.
"""

import json
import math
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from datetime import date
from pathlib import Path

ARMS = ('A', 'S')  # A revises its program after every race day; S keeps the initial program.
SCHEDULE_MODES = ('smoke', 'full')  # first races_per_day races of each day, or every eligible race
_NULLABLE = {
    'cache_mib',
    'rss_bytes',
    'job_seconds',
    'batch_seconds',
    'storage_bytes',
    'strategy_cpu_seconds',
    'artifact_bytes',
    'open_files',
    'model_output_tokens',
}
_FRACTIONAL = {'job_seconds', 'batch_seconds', 'strategy_cpu_seconds'}


@dataclass(frozen=True)
class Limits:
    # Machine safety ceilings; None means no framework ceiling.
    cache_mib: int | None = None
    rss_bytes: int | None = 10 * 1024**3
    job_seconds: float | None = 1800
    batch_seconds: float | None = None
    numerical_threads: int = 4
    artifact_bytes: int | None = None
    open_files: int | None = None
    storage_bytes: int | None = 5 * 1024**3
    # Per-phase session allowances (warmup = initial exploration, batch = one daily reflection).
    warmup_calls: int = 1000
    batch_calls: int = 1000
    warmup_bytes: int = 64 * 1024**2
    batch_bytes: int = 32 * 1024**2
    warmup_evidence_cells: int = 400000
    batch_evidence_cells: int = 100000
    warmup_model_calls: int = 40
    batch_model_calls: int = 20
    repair_model_calls: int = 5
    model_turns: int = 64
    model_output_tokens: int | None = None
    context_bytes: int = 256 * 1024
    response_bytes: int = 1024**2
    page_size: int = 8
    default_page_size: int = 2
    # Per-race allowances of a framework-executed prediction or validation job.
    strategy_calls: int = 40
    strategy_evidence_cells: int = 5000
    strategy_cpu_seconds: float | None = 120

    def __post_init__(self):
        for key, value in asdict(self).items():
            if value is None and key in _NULLABLE:
                continue
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError('Limits must be positive finite numbers; machine resources and model_output_tokens may be null')
            if key not in _FRACTIONAL and type(value) is not int:
                raise ValueError('Count and size limits must be integers')
        if self.default_page_size > self.page_size:
            raise ValueError('Default page cannot exceed maximum page')


@dataclass(frozen=True)
class Tariff:
    """Score-yen prices and the scoring rule (``score-yen-v2``)."""

    version: str = 'score-yen-v2'
    multiplier: float = 0.5
    field_yen: float = 0.01  # per evidence cell
    call_yen: float = 0.5  # per data-tool call
    cpu_yen: float = 0.2  # per sandbox CPU-second
    score_scale: float = 100
    smoothing: float = 0.02
    usd_to_yen: float = 150
    hit_bonus_yen: float = 100

    def __post_init__(self):
        if self.version != 'score-yen-v2':
            raise ValueError('Only the score-yen-v2 tariff is supported')
        for key, value in asdict(self).items():
            if key == 'version':
                continue
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (value == 0 and key != 'hit_bonus_yen'):
                raise ValueError('Tariff values must be finite and positive; hit_bonus_yen may be zero')
        if self.smoothing >= 1:
            raise ValueError('Smoothing must be between zero and one')

    def public(self):
        return asdict(self)


@dataclass(frozen=True)
class Config:
    source: str = str(Path(os.environ.get('JRDB_SOURCE', 'data/private/expanded.sqlite')).expanduser().absolute())
    python: str = sys.executable  # interpreter for sandboxed programs
    managed_root: str = str(Path(os.environ.get('JRDB_RUNS', 'runs')).expanduser().absolute())
    start: str = '2024-01-01'
    end: str = '2024-06-30'
    arm: str = 'A'
    seed: int = 0
    smoke_days: int = 10  # number of race days
    races_per_day: int = 5
    schedule_mode: str = 'full'
    limits: Limits = field(default_factory=Limits)
    protocol: str = 'economic-v1'
    tariff: Tariff = field(default_factory=Tariff)
    projection: str = 'objective-v5'

    def __post_init__(self):
        if self.protocol != 'economic-v1' or self.projection != 'objective-v5':
            raise ValueError('This release implements protocol economic-v1 with projection objective-v5')
        start, end = date.fromisoformat(self.start), date.fromisoformat(self.end)
        # 2026 races are a sealed holdout and can never be scheduled.
        if not date(2024, 1, 1) <= start <= end < date(2026, 1, 1):
            raise ValueError('Only 2024 development / consumed 2025 diagnostic windows are enabled')
        if self.arm not in ARMS:
            raise ValueError('Unknown arm')
        if self.schedule_mode not in SCHEDULE_MODES:
            raise ValueError('schedule_mode must be smoke or full')
        if self.smoke_days < 1 or self.races_per_day < 1:
            raise ValueError('Explicit positive schedule limits required')

    def public(self):
        """The bound, path-free part of the configuration."""
        values = asdict(self)
        for key in ('source', 'python', 'managed_root'):
            values.pop(key)
        return {**values, 'tariff': self.tariff.public()}

    @classmethod
    def load(cls, path=None):
        raw = json.loads(Path(path).read_text()) if path else {}
        if not isinstance(raw, dict) or not all(isinstance(raw.get(k, {}), dict) for k in ('limits', 'tariff')):
            raise ValueError('A configuration is a JSON object; limits and tariff are objects')
        try:
            for key in ('source', 'managed_root', 'python'):
                if key in raw:
                    raw[key] = str(Path(os.path.expandvars(raw[key])).expanduser().absolute())
            raw['limits'] = _build(Limits, raw.get('limits', {}))
            raw['tariff'] = _build(Tariff, raw.get('tariff', {}))
            return _build(cls, raw)
        except TypeError as exc:  # a value of the wrong type, e.g. "smoke_days": "3"
            raise ValueError(f'Malformed configuration: {exc}') from exc


def _build(kind, values):
    unknown = set(values) - {f.name for f in fields(kind)}
    if unknown:
        raise ValueError(f'Unknown {kind.__name__} keys: {", ".join(sorted(unknown))}')
    return kind(**values)

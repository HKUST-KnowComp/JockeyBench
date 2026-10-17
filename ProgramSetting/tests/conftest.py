"""Shared fixtures. Every test uses a tiny fabricated archive held in memory; no file or real data is read."""

import json
import os
import platform
import sqlite3
from dataclasses import replace

import pytest

from jockeybench.clock import cutoff, release
from jockeybench.config import Config, Limits
from jockeybench.data.source import Source
from jockeybench.lifecycle import usage
from jockeybench.protocol.service import Service
from jockeybench.runner import Run
from jockeybench.sandbox.executor import Executor
from jockeybench.storage import Storage

# raw0-raw1: initial support (before the 2024 window); raw2-raw5: window races; raw6: 2025; raw7: 2026 holdout.
DAYS = ['2023-12-01', '2023-12-02', '2024-01-06', '2024-01-06', '2024-01-07', '2024-01-20', '2025-01-01', '2026-01-01']
NUMBERS = ['01', '02', '03', '04', '05']

native = pytest.mark.skipif(
    os.environ.get('JRDB_NATIVE') != '1' or platform.system() not in {'Darwin', 'Linux'},
    reason='real sandbox jobs: set JRDB_NATIVE=1 on macOS or Linux',
)


def make_source():
    """Five runners per race; runner n finishes n-th. Hidden fields are planted to prove they never leak."""
    s = Source.__new__(Source)
    s.path, s.key, s.identity = None, b'k' * 32, {'fixture': True}
    s.con = sqlite3.connect(':memory:')
    s.con.row_factory = sqlite3.Row
    s.con.executescript("""
        CREATE TABLE races(id TEXT PRIMARY KEY,day TEXT,pre_at INTEGER,off_at INTEGER,review_at INTEGER,context TEXT);
        CREATE TABLE entries(race_id TEXT,number TEXT,horse_id TEXT,jockey_id TEXT,trainer_id TEXT,pre TEXT,history TEXT,settlement TEXT,review_at INTEGER);
        CREATE TABLE expanded_race_ids(race_id TEXT,public_id TEXT);""")
    for i, day in enumerate(DAYS):
        rid = f'raw{i}'
        s.con.execute(
            'INSERT INTO races VALUES(?,?,?,?,?,?)',
            (
                rid,
                day,
                cutoff(day),
                release(day) - 3600 + i,
                release(day) + 5 * 86400,
                json.dumps({'distance': 1600, 'surface': 1, 'venue': 1, 'odds': 99, 'expert': 'SECRET'}),
            ),
        )
        s.con.execute('INSERT INTO expanded_race_ids VALUES(?,?)', (rid, s.token('race', rid)))
        for n in range(1, 6):
            pre = {
                'KYI': {
                    'draw': n,
                    'sex': 1,
                    'carried_weight_tenths_kg': 550,
                    'reference_win_odds': 77,
                    'idm': 123,
                    'horse_note': 'SECRET',
                    'blinkers': {'nested_odds': 66},
                },
                'age_days': 1100,
                'CHA': {'early_time': 123, 'middle_time': 115, 'late_time': 114, 'effort': 5},
                'training_comment': 'SECRET',
                'cancelled': False,
            }
            history = {'finish': n, 'last3f_tenths_sec': 356, 'observed_idm': 66, 'historical_final_odds': 22}
            settlement = {'finish': str(n), 'abnormal': '0', 'present': True, 'win_payout_yen': 200 if n == 1 else 0, 'final_odds': '2.0'}
            s.con.execute(
                'INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?)',
                (
                    rid,
                    f'{n:02}',
                    f'horse{n}',
                    'jockey1',
                    'trainer1',
                    json.dumps(pre),
                    json.dumps(history),
                    json.dumps(settlement),
                    release(day) + 5 * 86400,
                ),
            )
    s.con.commit()
    s.manifest = lambda: {'sha256': 'in-memory-fixture', 'receipt_digest': 'fixture'}
    s.verify = lambda: None  # an in-memory database cannot change underneath a run
    return s


@pytest.fixture
def source():
    s = make_source()
    yield s
    s.close()


@pytest.fixture
def config(tmp_path):
    canary = tmp_path / 'synthetic-source-canary'
    canary.write_text('Synthetic sandbox isolation canary')
    return Config(source=str(canary), managed_root=str(tmp_path / 'managed'), limits=replace(Limits(), page_size=2))


@pytest.fixture
def service(source, config):
    """A service at the first window cutoff (2024-01-06), before any prediction."""
    clock = cutoff('2024-01-06')
    state = {
        'run_id': 'unit',
        'clock': clock,
        'initial_clock': clock,
        'phase': 'predict',
        'index': 0,
        'usage': usage(),
        'feedback': {},
        'submissions': {},
        'cursors': {},
        'audit': [],
        'model_outputs': [],
        'pending': None,
        'schedule': [{'day': '2024-01-06', 'races': [source.token('race', 'raw2'), source.token('race', 'raw3')]}],
        'strategy_digest': None,
        'validated_strategy': None,
        'valid_strategies': [],
    }
    return Service(source, config, state)


@pytest.fixture
def executor(config, service, tmp_path):
    """A real sandbox executor bound to ``service``. Only native tests start processes."""
    storage = Storage(config.managed_root, 'unit', config.limits.storage_bytes)
    e = Executor(config, storage, service)
    service.executor = e
    e.snapshot()
    yield e
    storage.close()


@pytest.fixture
def private_file(tmp_path):
    path = tmp_path / 'private-canary.txt'
    path.write_text('canary')
    return path


@pytest.fixture
def make_run(config, monkeypatch):
    """Build a Run on a fresh fixture source; by default the native isolation preflight is skipped."""

    def factory(run_id, agent, *, initial_from=None, native=False, **changes):
        if not native:
            monkeypatch.setattr(Run, 'preflight', lambda self: self.state.update(isolation={'unit_only': True}))
        source = make_source()
        monkeypatch.setattr(source, 'close', lambda: None)
        return Run(replace(config, **changes), run_id, agent, initial_from, source=source)

    return factory

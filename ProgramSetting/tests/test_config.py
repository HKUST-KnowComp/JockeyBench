import json
from dataclasses import replace
from pathlib import Path

import pytest

from jockeybench.config import Config, Limits, Tariff

CONFIGS = Path(__file__).parents[1] / 'configs'


def test_defaults_are_the_paper_protocol():
    paper = Config.load(CONFIGS / 'paper.json')
    assert Config().public() == paper.public()
    assert paper.protocol == 'economic-v1' and paper.projection == 'objective-v5' and paper.arm == 'A'
    assert paper.tariff == Tariff(version='score-yen-v2', multiplier=0.5, hit_bonus_yen=100)


def test_smoke_config_differs_only_in_schedule():
    paper, smoke = (Config.load(CONFIGS / name).public() for name in ('paper.json', 'smoke.json'))
    assert {k for k in paper if paper[k] != smoke[k]} == {'smoke_days', 'races_per_day', 'schedule_mode'}


@pytest.mark.parametrize(
    'changes',
    [
        {'start': '2023-12-31'},
        {'end': '2026-01-01'},
        {'start': '2024-03-01', 'end': '2024-02-01'},
        {'arm': 'B'},
        {'protocol': 'hit-only-v1'},
        {'projection': 'objective-v4'},
        {'schedule_mode': 'random'},
        {'schedule_mode': 'spread'},
        {'smoke_days': 0},
    ],
)
def test_invalid_configurations_are_refused(changes):
    with pytest.raises(ValueError):
        replace(Config(), **changes)


@pytest.mark.parametrize(
    'limits', [{'batch_calls': None}, {'batch_calls': 0}, {'page_size': 2.5}, {'default_page_size': 9}, {'job_seconds': float('inf')}]
)
def test_invalid_limits_are_refused(limits):
    with pytest.raises(ValueError):
        Limits(**limits)


def test_machine_ceilings_and_output_tokens_may_be_unbounded():
    limits = Limits(model_output_tokens=None, rss_bytes=None, job_seconds=None, strategy_cpu_seconds=None)
    assert Config(limits=limits).public()['limits']['model_output_tokens'] is None


@pytest.mark.parametrize(
    'tariff',
    [
        {'version': 'score-yen-v1'},
        {'smoothing': 1},
        {'multiplier': 0},
        {'multiplier': '0.5'},
        {'hit_bonus_yen': -1},
        {'hit_bonus_yen': None},
        {'field_yen': float('nan')},
    ],
)
def test_invalid_tariffs_are_refused(tariff):
    with pytest.raises(ValueError):
        Tariff(**tariff)


def test_public_config_is_path_free_and_load_expands_paths(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_ROOT', str(tmp_path))
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'source': '$DATA_ROOT/db.sqlite', 'managed_root': '$DATA_ROOT/runs', 'tariff': {'multiplier': 1}}))
    config = Config.load(path)
    assert config.source == str(tmp_path / 'db.sqlite') and config.managed_root == str(tmp_path / 'runs')
    assert config.tariff.multiplier == 1
    assert not {'source', 'python', 'managed_root'} & set(config.public())


@pytest.mark.parametrize(
    'raw,message',
    [
        ({'smoke_day': 3}, 'Unknown Config keys: smoke_day'),
        ({'limits': {'page_sizes': 4}}, 'Unknown Limits keys: page_sizes'),
        ({'tariff': {'bonus': 1}}, 'Unknown Tariff keys: bonus'),
        ({'smoke_days': '3'}, 'Malformed'),
        ({'start': 20240101}, 'Malformed'),
        ({'limits': None}, 'JSON object'),
        ([], 'JSON object'),
    ],
)
def test_malformed_configurations_are_refused_with_a_reason(raw, message, tmp_path):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match=message):
        Config.load(path)


def test_zero_hit_bonus_is_allowed():
    assert Tariff(hit_bonus_yen=0).hit_bonus_yen == 0

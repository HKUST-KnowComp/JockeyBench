"""Projection and time-gating of the archive: what an agent can see, and when."""

import json

import pytest

from jockeybench.clock import cutoff, release
from jockeybench.data.catalog import catalog
from jockeybench.data.projection import number, projection
from jockeybench.errors import Rejected

HIDDEN = [
    'SECRET',
    'nested_odds',
    'reference_win_odds',
    'observed_idm',
    'historical_final_odds',
    'win_payout_yen',
    'final_odds',
    'idm',
    'raw2',
    'horse1',
    'jockey1"',
    'trainer1"',
]


@pytest.mark.parametrize(
    'raw,expected', [('12', 12), (' 1.5 ', 1.5), ('＋ 2', 2), ('- 2', -2), (True, None), ('1e13', None), ('abc', None), (None, None)]
)
def test_number_parsing(raw, expected):
    assert number(raw) == expected


def test_projection_keeps_every_field_with_a_missing_reason():
    values, missing = projection(
        {'distance': '0', 'surface': ' ', 'grade': 'x', 'condition': 'A1'}, ('distance', 'surface', 'grade', 'condition', 'venue')
    )
    assert values == {'distance': None, 'surface': None, 'grade': None, 'condition': 'A1', 'venue': None}
    assert missing == {'distance': 'unusable', 'surface': 'blank', 'grade': 'invalid', 'venue': 'absent'}


@pytest.mark.parametrize(
    'tool,args', [('card', {}), ('training', {}), ('horse_history', {'number': '01'}), ('form', {'k': 2}), ('connections', {})]
)
def test_race_tools_never_expose_hidden_fields(service, tool, args):
    text = json.dumps(service.call(tool, {'race_id': service.source.token('race', 'raw2'), **args}))
    assert not [hidden for hidden in HIDDEN if hidden in text]


@pytest.mark.parametrize(
    'tool,args',
    [
        ('describe', {}),
        ('history', {'limit': 2, 'include_form': 2}),
        ('statistics', {}),
        ('coverage', {}),
        ('results', {'limit': 2}),
        ('status', {}),
        ('ledger', {}),
        ('batch', {}),
    ],
)
def test_page_and_control_tools_never_expose_hidden_fields(service, tool, args):
    service.state['clock'] = release('2024-01-06')
    text = json.dumps(service.call(tool, args))
    assert not [hidden for hidden in HIDDEN if hidden in text]


def test_future_races_and_the_2026_holdout_are_refused(service):
    for raw in ['raw4', 'raw6', 'raw7']:
        with pytest.raises(Rejected, match='not available'):
            service.call('card', {'race_id': service.source.token('race', raw)})
    service.state['clock'] = release('2026-01-02')
    with pytest.raises(Rejected):
        service.call('card', {'race_id': service.source.token('race', 'raw7')})
    with pytest.raises(Rejected, match='Unknown race'):
        service.call('card', {'race_id': 'race_' + '0' * 24})


def test_historical_card_is_frozen_at_its_own_cutoff(service):
    rid = service.source.token('race', 'raw0')
    early = service.call('card', {'race_id': rid})
    service.state['clock'] = cutoff('2025-02-01')
    later = service.call('card', {'race_id': rid})
    data = lambda response: {k: v for k, v in response.items() if not k.startswith('_')}
    assert data(early) == data(later)
    assert early['feature_asof'] == 'previous_day_21_jst' and early['day_index'] == -31
    assert 'day' not in early and 'finish' not in json.dumps(early)


def test_results_release_finish_at_day_close_and_review_later(service):
    race = service.source.token('race', 'raw2')
    with pytest.raises(Rejected, match='not been released'):
        service.call('results', {'race_id': race})
    service.state['clock'] = release('2024-01-06')
    result = service.call('results', {'race_id': race})
    first = result['records'][0]
    assert result['stage'] == 'finish_only' and first['values']['finish'] == 1
    assert first['values']['last3f_tenths_sec'] is None and first['missing']['last3f_tenths_sec'] == 'not_released'
    service.state['clock'] += 5 * 86400
    result = service.call('results', {'race_id': race})
    assert result['stage'] == 'review' and result['records'][0]['values']['last3f_tenths_sec'] == 356


def test_form_is_frozen_at_the_target_race_cutoff(service):
    race = service.source.token('race', 'raw4')
    service.state['clock'] = cutoff('2024-01-07')
    first = service.call('form', {'race_id': race, 'k': 2})
    assert first['runners'][0]['starts'][0]['values']['last3f_tenths_sec'] is None
    service.state['clock'] = cutoff('2024-01-20')
    assert service.call('form', {'race_id': race, 'k': 2})['runners'] == first['runners']
    sample = service.call('history', {'include_form': 2, 'filters': {'day_min': 6, 'day_max': 6}, 'limit': 1})['examples'][0]
    assert sample['form']['runners'] == first['runners']
    assert sample['labels']['01']['last3f_tenths_sec'] == 356


def test_horse_history_contains_only_the_target_horse(service):
    service.source.con.execute("UPDATE entries SET number='09' WHERE race_id='raw1' AND horse_id='horse4'")
    result = service.call('horse_history', {'race_id': service.source.token('race', 'raw2'), 'number': '04', 'limit': 2})
    latest, older = result['examples']
    assert latest['matched_number'] == '09' and set(latest['labels']) == {'09'}
    assert [r['number'] for r in latest['card']['runners']] == ['09']
    assert older['matched_number'] == '04' and len(older['card']['runners']) == 1


def test_connections_count_only_labels_visible_at_the_cutoff(service):
    race = service.source.token('race', 'raw4')
    service.state['clock'] = cutoff('2024-01-07')
    values = service.call('connections', {'race_id': race})['runners'][0]['values']
    # Support races raw0/raw1 are reviewed; window races raw2/raw3 released at 2024-01-06 20:30.
    assert values == {'jockey_starts': 20, 'jockey_wins': 4, 'jockey_top3': 12, 'trainer_starts': 20, 'trainer_wins': 4, 'trainer_top3': 12}


def test_catalog_documents_every_projected_field(service):
    card = service.call('card', {'race_id': service.source.token('race', 'raw2')})
    fields = catalog()
    assert set(card['facts']) == set(fields['race_facts'])
    assert set(card['runners'][0]['facts']) == set(fields['runner_facts'])
    assert set(card['runners'][0]['training']) == set(fields['training'])

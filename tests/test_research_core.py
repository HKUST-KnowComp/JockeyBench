"""Research integration at the shared broker boundary, using only fabricated memory data."""

import copy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from jockeybench.canonical import code_hash
from jockeybench.clock import release
from jockeybench.config import Tariff
from jockeybench.errors import Rejected
from jockeybench.ledger import reconcile
from jockeybench.protocol.metering import PROGRAM_BUDGET_VERSION
from jockeybench.research.finegrained_v3.native_replay import replay_economic
from jockeybench.research.llm.agent import credential
from jockeybench.research.policy_amortized.compact_evidence.schema import schema_fields
from jockeybench.research.runner import ActiveService, seal, settle


def prediction():
    return {'ranking': ['01', '02', '03', '04', '05'], 'probabilities': {'01': 0.6, '02': 0.1, '03': 0.1, '04': 0.1, '05': 0.1}}


def test_trusted_seal_is_audited_and_immutable(service):
    race = service.state['schedule'][0]['races'][0]
    value = prediction()
    receipt = service.seal_prediction(race, value, operation_id='engine-prediction')
    assert receipt['sealed'] == race
    assert receipt['_cost']['total_micro'] == 0
    assert service.state['audit'][-1]['tool'] == 'submit'
    assert service.state['audit'][-1]['status'] == 'ok'
    saved = copy.deepcopy(service.state['submissions'][race])
    value['ranking'].reverse()
    value['probabilities']['01'] = 0
    assert service.state['submissions'][race] == saved
    assert service.seal_prediction(race, prediction(), operation_id='engine-prediction') == receipt
    changed = prediction()
    changed['ranking'].reverse()
    with pytest.raises(Rejected, match='already sealed'):
        service.seal_prediction(race, changed)
    assert service.state['submissions'][race] == saved


@pytest.mark.parametrize('from_sdk', [False, True])
def test_agent_cannot_enter_trusted_submission_or_replay(service, from_sdk):
    race = service.state['schedule'][0]['races'][0]
    service.state['engine_submission'] = True  # Obsolete checkpoint data grants no authority.
    with pytest.raises(Rejected):
        service.call('submit', {'race_id': race, 'prediction': prediction()}, from_sdk=from_sdk)
    assert not service.state['submissions']
    service.seal_prediction(race, prediction(), operation_id='trusted')
    with pytest.raises(Rejected, match='not authorized'):
        service.call('submit', {'race_id': race, 'prediction': prediction()}, from_sdk=from_sdk, operation_id='trusted')


@pytest.mark.parametrize('failure', ['phase', 'race', 'clock', 'prediction'])
def test_seal_checks_current_decision_boundary(service, failure):
    race = service.state['schedule'][0]['races'][0]
    value = prediction()
    if failure == 'phase':
        service.state['phase'] = 'reflect'
    elif failure == 'race':
        race = service.source.token('race', 'raw4')
    elif failure == 'clock':
        service.state['clock'] = release('2024-01-06')
    else:
        value['probabilities'].pop('05')
    with pytest.raises(Rejected):
        service.seal_prediction(race, value)
    assert not service.state['submissions']


def test_setting_worker_permissions_do_not_change_trusted_acquisition(service):
    race = service.state['schedule'][0]['races'][0]
    active = ActiveService(service.source, service.config, service.state)
    # A worker's guessed call cannot read source data through the research broker.
    with pytest.raises(Rejected, match='unauthorized'):
        active.call('card', {'race_id': race}, from_sdk=True)
    assert not service.state.get('read_digests')
    receipt = active.call('card', {'race_id': race}, operation_id='paid-card')
    assert receipt['race_id'] == race
    assert receipt['_cost']['charged_cells'] > 0
    with pytest.raises(Rejected, match='not authorized'):
        active.call('card', {'race_id': race}, from_sdk=True, operation_id='paid-card')
    # The program setting keeps its existing worker access.
    assert service.call('card', {'race_id': race}, from_sdk=True)['race_id'] == race


def test_research_seal_settle_and_replay_share_core_ledger(service):
    cfg = replace(service.config, tariff=replace(Tariff(), hit_bonus_yen=0))
    state = service.state
    state.update(
        scores={},
        release_ledger={},
        binding={
            'code': code_hash(),
            'config': cfg.public(),
            'source': service.source.manifest(),
            'budget_version': PROGRAM_BUDGET_VERSION,
        },
    )
    active = ActiveService(service.source, cfg, state)
    run = SimpleNamespace(state=state, service=active, source=service.source, config=cfg, persist=lambda: None)
    race = state['schedule'][0]['races'][0]
    active.call('card', {'race_id': race})
    seal(run, race, {'type': 'submit', 'prediction': prediction()}, prediction()['ranking'])
    assert not state['scores'] and not state['release_ledger']
    settle(run, race, '2024-01-06')
    audited = replay_economic(cfg, state, source=service.source)
    assert audited['verified_score_revenues'] == 1
    assert audited['verified_responses'] == 2
    assert audited['ledger'] == reconcile(state)
    assert audited['audit_cost_micro'] == audited['ledger']['cost_micro']
    assert state['scores'][race]['revenue_hit_yen'] == 0


def test_shared_catalog_retains_frozen_research_field_contract():
    fields = schema_fields()  # Includes the pinned catalog and domain-projection digest checks.
    assert 'finish' in fields['history_values']
    assert 'distance' in fields['race_facts']


def test_research_credentials_use_only_selected_environment(monkeypatch):
    key = 'JOCKEYBENCH_TEST_RESEARCH_KEY'
    monkeypatch.delenv(key, raising=False)
    with pytest.raises(Rejected, match='credential is unavailable'):
        credential({'api_key_env': key})
    monkeypatch.setenv(key, 'synthetic-test-key')
    assert credential({'api_key_env': key}) is None

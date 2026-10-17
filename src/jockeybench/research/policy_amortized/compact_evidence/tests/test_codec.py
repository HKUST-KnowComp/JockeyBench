import copy
import json
import pytest
from jockeybench.canonical import packed
from jockeybench.research.policy_amortized.compact_evidence import (
    compact_view, expand_compact_view, model_visible_semantic_projection, CompactIntegrityError)
from jockeybench.research.policy_amortized.compact_evidence.schema import exact, schema_fields


@pytest.fixture
def full():
    fields = schema_fields()
    def observation(domain, number, value):
        return {'domain': domain, 'arguments': {'horse_number': number} if number is not None else {},
                'observation': value, 'provenance': {'evidence_id': 'internal', 'service_operation': 'op'},
                'receipt': {'total_micro': 100, 'charged_cells': 23}}
    start = {'race_id': 'race_shared', 'day_index': -10,
             'race_facts': {'surface': 'turf', 'distance': 1600, 'grade': 'G3', 'rainfall': None},
             'runner_facts': {'draw': 2, 'earned_prize': 1.0, 'sex': 'opaque'},
             'values': {'finish': 1, 'abnormal': False, 'last3f_tenths_sec': None},
             'missing': {'last3f_tenths_sec': 'blank', 'margin_tenths_sec': 'absent'},
             'label_scope': 'released historical outcomes'}
    result = {'schema': 'evidence-package-v1', 'race_id': 'race_current', 'entrants': ['02', '01'],
              'observations': [observation('track_context', None, {'facts': {'surface': 'dirt', 'distance': 1200},
                  'missing': {'rainfall': 'absent'}, 'feature_asof': 'previous_day_21_jst'})]}
    for h in result['entrants']:
        result['observations'].append(observation('recent_history', h,
            {'horse_number': h, 'starts': [copy.deepcopy(start), {**copy.deepcopy(start), 'race_id': 'race_older', 'day_index': -20}], 'scope': 'latest five'}))
        result['observations'].append(observation('training', h,
            {'horse_number': h, 'training': {'effort': 'opaque', 'furlongs': 5, 'early_time': None, 'late_time': 1.25},
             'missing': {'early_time': 'blank'}, 'feature_asof': 'previous_day_21_jst', 'billing_scope': 'whole-race'}))
        result['observations'].append(observation('condition_stats', h,
            {'horse_number': h, 'sample_starts': 2, 'labelled_starts': 1, 'groups': [
                {'surface': 'turf', 'distance': 1600, 'starts': 1, 'wins': 1, 'top3': 1},
                {'surface': 'dirt', 'distance': None, 'starts': 0, 'wins': 0, 'top3': 0}], 'scope': 'not lifetime'}))
    return result


def test_01_02_03_exact_deterministic_json_roundtrip(full):
    before=copy.deepcopy(full); compact=compact_view(full)
    assert packed(compact)==packed(compact_view(full))
    assert exact(json.loads(json.dumps(compact)))==exact(compact)
    assert exact(expand_compact_view(compact))==exact(model_visible_semantic_projection(full))
    assert exact(full)==exact(before)


def test_04_05_06_null_absent_missing_reasons(full):
    expanded=expand_compact_view(compact_view(full))
    start=expanded['observations'][1]['observation']['starts'][0]
    assert start['values']['last3f_tenths_sec'] is None
    assert 'margin_tenths_sec' not in start['values']
    assert start['missing']=={'last3f_tenths_sec':'blank','margin_tenths_sec':'absent'}
    assert type(start['values']['abnormal']) is bool
    assert type(start['values']['finish']) is int
    assert type(start['runner_facts']['earned_prize']) is float


@pytest.mark.parametrize('value', [None, {}, [], ['finish', 'abnormal'], {'finish': None, 'abnormal': {'reason': 'opaque'}}])
def test_missing_shapes_and_reasons_preserved(full,value):
    full['observations'][1]['observation']['starts'][0]['missing']=value
    assert exact(expand_compact_view(compact_view(full)))==exact(model_visible_semantic_projection(full))


def test_07_08_09_10_identity_maps_and_shared_expansion(full):
    c=compact_view(full)
    assert c['horse_map']=={'02':0,'01':1}
    assert c['race_map']=={'race_older':0,'race_shared':1}
    assert len(c['historical_races'])==2
    e=expand_compact_view(c)
    assert e['observations'][1]['observation']['starts']==e['observations'][4]['observation']['starts']
    assert e['entrants']==['02','01']


@pytest.mark.parametrize('change', ['fact','numeric_type','day_index','absent'])
def test_11_shared_conflict_rejected(full,change):
    s=full['observations'][4]['observation']['starts'][0]
    if change=='fact':s['race_facts']['distance']=1800
    if change=='numeric_type':s['race_facts']['distance']=1600.0
    if change=='day_index':s['day_index']=-9
    if change=='absent':del s['race_facts']['rainfall']
    with pytest.raises(CompactIntegrityError,match='Conflicting'):compact_view(full)


def test_12_13_14_15_order_and_all_domain_fields(full):
    full['observations'].append(copy.deepcopy(full['observations'][2]))
    c=compact_view(full);e=expand_compact_view(c)
    assert exact(e)==exact(model_visible_semantic_projection(full))
    assert [s['race_id'] for s in e['observations'][1]['observation']['starts']]==['race_shared','race_older']
    assert [g['surface'] for g in e['observations'][3]['observation']['groups']]==['turf','dirt']


def test_16_metadata_removed_only_from_view(full):
    c=compact_view(full)
    for text in ('receipt','provenance','evidence_id','service_operation','billing_scope'):
        assert text not in json.dumps(c)
    assert full['observations'][0]['receipt']['charged_cells']==23
    assert 'billing_scope' in full['observations'][2]['observation']


@pytest.mark.parametrize('target', ['package','envelope','domain','fact','missing','group'])
def test_17_27_unknown_semantic_fields_fail_loudly(full,target):
    obj={'package':full,'envelope':full['observations'][0],
         'domain':full['observations'][0]['observation'],
         'fact':full['observations'][0]['observation']['facts'],
         'missing':full['observations'][0]['observation']['missing'],
         'group':full['observations'][3]['observation']['groups'][0]}[target]
    obj['new_approved_fact']='must not disappear'
    with pytest.raises(CompactIntegrityError):compact_view(full)


def test_27_catalog_drift_fails(monkeypatch,full):
    from jockeybench.research.policy_amortized.compact_evidence import schema
    original=schema.approved.catalog
    def changed(*args):
        value=original(*args);value['race_facts']['new_field']={};return value
    monkeypatch.setattr(schema.approved,'catalog',changed)
    with pytest.raises(CompactIntegrityError,match='schema drift'):compact_view(full)


def test_all_ablation_variants_reversible(full):
    for shared in [False,True]:
        for indexed in [False,True]:
            for metadata in [False,True]:
                c=compact_view(full,shared_races=shared,index_maps=indexed,retain_metadata=metadata)
                assert exact(expand_compact_view(c))==exact(model_visible_semantic_projection(full))


def test_fixed_schema_and_dict_order_independence(full):
    def reverse(x):
        if type(x) is dict:return {k:reverse(x[k]) for k in reversed(list(x))}
        if type(x) is list:return [reverse(v) for v in x]
        return x
    assert packed(compact_view(full))==packed(compact_view(reverse(full)))


def test_digest_linkage_and_accounting_separation(full):
    from jockeybench.research.policy_amortized.compact_evidence.audit import audit_package
    first=audit_package(full)
    second_full=copy.deepcopy(full);second_full['observations'][0]['receipt']['total_micro']+=1
    second=audit_package(second_full)
    assert first['full_evidence_digest']!=second['full_evidence_digest']
    assert first['compact_view_digest']==second['compact_view_digest']
    assert first['semantic_projection_digest']==second['semantic_projection_digest']


def test_changed_semantic_value_is_rejected(full):
    from jockeybench.research.policy_amortized.compact_evidence.audit import audit_package
    c=compact_view(full);c['historical_races'][0][0][0][0][c['schema_maps']['race_facts']['distance']]=999
    with pytest.raises(CompactIntegrityError):audit_package(full,c)


def test_boolean_schema_index_is_not_an_integer_index(full):
    c=compact_view(full)
    name=next(k for k,v in c['schema_maps']['race_facts'].items() if v==1)
    c['schema_maps']['race_facts'][name]=True
    with pytest.raises(CompactIntegrityError):expand_compact_view(c)

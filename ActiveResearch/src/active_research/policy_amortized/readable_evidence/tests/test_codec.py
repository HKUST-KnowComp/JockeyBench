import copy
import json
import pytest
from jrdb_lh.common import packed
from active_research.policy_amortized.compact_evidence.tests.test_codec import full
from active_research.policy_amortized.readable_evidence.codec import readable_view, expand_readable_evidence, model_visible_semantic_projection
from active_research.policy_amortized.readable_evidence.schema import exact, CompactIntegrityError
from active_research.policy_amortized.readable_evidence.audit import audit_package


def test_all_domains_roundtrip_and_immutable(full):
    before=packed(full);view=readable_view(full)
    assert packed(view)==packed(readable_view(copy.deepcopy(full)))
    assert exact(expand_readable_evidence(json.loads(json.dumps(view))))==exact(model_visible_semantic_projection(full))
    assert packed(full)==before
    assert audit_package(full)['full_evidence_digest']


@pytest.mark.parametrize('field,value', [('race_facts',None),('race_facts',{}),('runner_facts',None),('values',None),
    ('missing',None),('missing',{}),('missing',[]),('missing',['finish','abnormal']),('missing',{'finish':{'reason':'opaque | \n null'}})])
def test_nested_null_and_missing_shapes(full,field,value):
    full['observations'][1]['observation']['starts'][0][field]=value
    assert exact(expand_readable_evidence(readable_view(full)))==exact(model_visible_semantic_projection(full))


@pytest.mark.parametrize('field',['race_facts','runner_facts','values','missing','day_index','label_scope'])
def test_absent_and_explicit_null_distinct(full,field):
    del full['observations'][1]['observation']['starts'][0][field]
    view=readable_view(full)
    assert exact(expand_readable_evidence(view))==exact(model_visible_semantic_projection(full))


def test_types_names_and_direct_identities(full):
    view=readable_view(full);text=json.dumps(view)
    assert view['HORSES'][0]['horse_number']=='02'
    for name in ('horse_idx','race_idx','race_map','horse_map','shared_races','schema_maps','billing_scope','receipt','provenance'):
        assert name not in text
    first=view['HORSES'][0]['observations'][0]['observation']['HISTORY_ROWS'][0]
    assert first['values'][0]=='race_shared'
    assert first['missing']=={'last3f_tenths_sec':'blank','margin_tenths_sec':'absent'}
    assert all(type(x) is str for x in first['absent_fields'])
    start=expand_readable_evidence(view)['observations'][1]['observation']['starts'][0]
    assert start['values']['last3f_tenths_sec'] is None
    assert 'margin_tenths_sec' not in start['values']
    assert type(start['values']['abnormal']) is bool
    assert type(start['runner_facts']['earned_prize']) is float


def test_no_shared_normalization_and_all_original_orders(full):
    full['observations'].append(copy.deepcopy(full['observations'][2]))
    full['observations'][4]['observation']['starts'][0]['race_facts']['distance']=2000
    view=readable_view(full)
    assert exact(expand_readable_evidence(view))==exact(model_visible_semantic_projection(full))
    assert len(view['HORSES'][0]['observations'])==4


@pytest.mark.parametrize('style',['columns','key_value'])
def test_training_representations(full,style):
    for value in (None,{},full['observations'][2]['observation']['training']):
        full['observations'][2]['observation']['training']=value
        view=readable_view(full,training_style=style)
        assert exact(expand_readable_evidence(view))==exact(model_visible_semantic_projection(full))


@pytest.mark.parametrize('target',['package','domain','fact','missing','group'])
def test_unknown_fields_fail_loudly(full,target):
    obj={'package':full,'domain':full['observations'][0]['observation'],
         'fact':full['observations'][0]['observation']['facts'],'missing':full['observations'][0]['observation']['missing'],
         'group':full['observations'][3]['observation']['groups'][0]}[target]
    obj['unreviewed_field']='preserve or fail'
    with pytest.raises(CompactIntegrityError):readable_view(full)


def test_schema_drift_and_tampering(full):
    view=readable_view(full);view['columns']['HISTORY_COLUMNS'].reverse()
    with pytest.raises(CompactIntegrityError):expand_readable_evidence(view)
    view=readable_view(full);view['HORSES'][0]['observations'][0]['observation_order']=True
    with pytest.raises(CompactIntegrityError):expand_readable_evidence(view)
    view=readable_view(full);view['HORSES'][0]['observations'][0]['observation']['HISTORY_ROWS'][0]['values'][0]='changed_race'
    with pytest.raises(CompactIntegrityError):audit_package(full,view)


def test_dictionary_order_does_not_change_encoding(full):
    def reverse(x):
        if type(x) is dict:return {k:reverse(x[k]) for k in reversed(list(x))}
        if type(x) is list:return [reverse(v) for v in x]
        return x
    assert packed(readable_view(full))==packed(readable_view(reverse(full)))


def test_catalog_change_detected(monkeypatch,full):
    from active_research.policy_amortized.compact_evidence import schema
    original=schema.approved.catalog
    def drift(*args):
        c=original(*args);c['race_facts']['unreviewed']={};return c
    monkeypatch.setattr(schema.approved,'catalog',drift)
    with pytest.raises(CompactIntegrityError,match='schema drift'):readable_view(full)

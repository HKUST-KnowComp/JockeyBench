import copy
import json
import pytest
from active_research.policy_amortized.compact_evidence.tests.test_codec import full
from active_research.policy_amortized.semantic_json.view import semantic_view, serialize, exact, audit_package


def test_exact_natural_structure_and_only_documented_deletions(full):
    untouched=copy.deepcopy(full);expected=copy.deepcopy(full)
    for o in expected['observations']:
        o.pop('receipt');o.pop('provenance')
        if o['domain']=='training':o['observation'].pop('billing_scope')
    view=semantic_view(full)
    assert exact(view)==exact(expected)==exact(json.loads(serialize(view)))
    assert full==untouched
    assert serialize(view)==serialize(semantic_view(copy.deepcopy(full)))
    for o,original in zip(view['observations'],full['observations']):
        assert o['arguments']==original['arguments']
        if o['domain']=='recent_history':assert o['observation']['starts']==original['observation']['starts']
        if o['domain'] in ('condition_stats','track_context'):assert o['observation']==original['observation']
    assert view['entrants']==['02','01']
    assert all('receipt' in o and 'provenance' in o for o in full['observations'])


@pytest.mark.parametrize('field',['race_facts','runner_facts','values','missing','day_index','label_scope'])
@pytest.mark.parametrize('value',[None,{},[],{'opaque':'无代码本','reason':None}])
def test_natural_null_absent_missing_types_and_order(full,field,value):
    start=full['observations'][1]['observation']['starts'][0];start[field]=value
    got=semantic_view(full)['observations'][1]['observation']['starts'][0]
    assert exact(got)==exact(start)
    del start[field]
    assert field not in semantic_view(full)['observations'][1]['observation']['starts'][0]


def test_unclassified_nested_data_preserved_not_pruned(full):
    full['observations'][1]['observation']['starts'][0]['runner_facts']['ambiguous_metadata']={'x':None}
    view=semantic_view(full)
    assert view['observations'][1]['observation']['starts'][0]['runner_facts']['ambiguous_metadata']=={'x':None}
    view['observations'][1]['observation']['starts'][0]['values']['finish']=True
    with pytest.raises(ValueError):audit_package(full,view)


def test_shared_race_conflicts_preserved_and_no_join(full):
    full['observations'][4]['observation']['starts'][0]['race_facts']['distance']=2000
    view=semantic_view(full)
    assert view['observations'][1]['observation']['starts'][0]['race_facts']['distance']==1600
    assert view['observations'][4]['observation']['starts'][0]['race_facts']['distance']==2000
    for word in ('horse_idx','race_idx','schema_maps','row_encoding','horse_map','race_map'):
        assert word not in serialize(view)

"""Poll real scientific persist boundaries; never change scientific functions."""
import copy
import gzip
import json
import sys
import pytest
if sys.platform!='darwin':pytest.skip('Native fixtures on Mini',allow_module_level=True)
from jockeybench.canonical import digest
from jockeybench.clock import release
from jockeybench.research.aligned.integrity import IntegrityFailure
from jockeybench.research.policy_amortized.semantic_json.tests.test_runtime import source,config,service,active,run,policy,offline,execute,released
from jockeybench.research.policy_amortized.semantic_json import supervisor as watch
from jockeybench.research.policy_amortized.semantic_json.gate import load_checkpoint


@pytest.fixture
def snapshots(run,policy,monkeypatch):
    saved=[];original=run.persist
    def capture():
        original();saved.append(copy.deepcopy(run.state))
    monkeypatch.setattr(run,'persist',capture)
    monkeypatch.setattr(run.service,'persist',capture)
    execute(run,policy)
    return saved


@pytest.mark.parametrize('point',['before_first','one','N','before_finalization','after_finalization','prediction','sealed'])
def test_poll_real_lifecycle_boundaries(snapshots,tmp_path,point):
    partial=[s for s in snapshots if 'evidence_package' in s and 'evidence_package_digest' not in s]
    counts=lambda s:len(s['evidence_package']['observations'])
    cases={
        'before_first':next(s for s in partial if counts(s)==0),
        'one':next(s for s in partial if counts(s)==1),
        'N':next(s for s in partial if counts(s)>=3),
        'before_finalization':partial[-1],
        'after_finalization':next(s for s in snapshots if 'evidence_package_digest' in s),
        'prediction':next(s for s in snapshots if s.get('llm_trajectory')),
        'sealed':next(s for s in snapshots if s.get('submissions'))}
    s=copy.deepcopy(cases[point]);s['checkpoint_digest']=digest({k:v for k,v in s.items() if k!='checkpoint_digest'})
    path=tmp_path/'checkpoint.json.gz';path.write_bytes(gzip.compress(json.dumps(s).encode()))
    before=path.read_bytes();got=watch.evidence_lifecycle(load_checkpoint(path))
    assert got['provenance_so_far']=='valid' and before==path.read_bytes()
    if point in ('before_first','one','N','before_finalization'):
        assert got['final_evidence_digest']=='not-yet-applicable' and got['stage']=='collecting'
    else:assert got['final_evidence_digest']=='valid'


def test_all_persist_snapshots_are_legal(snapshots):
    for s in snapshots:
        watch.evidence_lifecycle(s)


def test_partial_corruption_still_stops(snapshots):
    s=copy.deepcopy(next(s for s in snapshots if len(s.get('evidence_package',{}).get('observations',[]))>=1 and 'evidence_package_digest' not in s))
    s['evidence_package']['observations'][0]['receipt']['total_micro']+=1
    with pytest.raises(IntegrityFailure,match='provenance_so_far: invalid'):watch.evidence_lifecycle(s)


def test_missing_required_vs_invalid_digest(snapshots):
    s=copy.deepcopy(next(s for s in snapshots if 'evidence_package_digest' in s))
    s.pop('evidence_package_digest')
    with pytest.raises(IntegrityFailure,match='missing-when-required'):watch.evidence_lifecycle(s)
    s['evidence_package_digest']='wrong'
    with pytest.raises(IntegrityFailure,match='invalid'):watch.evidence_lifecycle(s)


def test_legal_release_and_missing_required_receipt(run,policy):
    execute(run,policy);released(run,policy)
    s=copy.deepcopy(run.state)
    assert watch.evidence_lifecycle(s)['release_receipt']=='valid'
    s['release_ledger']={}
    with pytest.raises(IntegrityFailure,match='release_receipt: missing-when-required'):watch.evidence_lifecycle(s)


def test_frame_and_next_policy_not_required_before_commits(tmp_path):
    day='2024-01-06';days={day:{'planned':24,'released':0}}
    expected={'Frame':'not-yet-applicable','next_policy':'not-yet-applicable'}
    assert watch.day_artifacts(tmp_path,days,[])[day]==expected
    days[day]['released']=24
    assert watch.day_artifacts(tmp_path,days,[{'kind':'release_gate','day':day}])[day]==expected
    with pytest.raises(IntegrityFailure,match='Frame.*missing-when-required'):
        watch.day_artifacts(tmp_path,days,[{'kind':'frame_commit','day':day}])
    f={'day':day,'release_clock':release(day)};f['frame_digest']=digest(f)
    (tmp_path/('frame-'+day+'.json')).write_text(json.dumps(f))
    assert watch.day_artifacts(tmp_path,days,[{'kind':'frame_commit','day':day}])[day]['next_policy']=='not-yet-applicable'
    with pytest.raises(IntegrityFailure,match='next_policy.*missing-when-required'):
        watch.day_artifacts(tmp_path,days,[{'kind':'day_complete','day':day}])
    f['frame_digest']='wrong';(tmp_path/('frame-'+day+'.json')).write_text(json.dumps(f))
    with pytest.raises(IntegrityFailure,match='Frame.*invalid'):watch.day_artifacts(tmp_path,days,[])


def test_unbound_or_pre_card_state_does_not_require_future_fields():
    s={'phase':'warmup','schedule':[{'day':'2024-01-06','races':['r0']}], 'submissions':{},'release_ledger':{}}
    assert watch.evidence_lifecycle(s)['final_evidence_digest']=='not-yet-applicable'

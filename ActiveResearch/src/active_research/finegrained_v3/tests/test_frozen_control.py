import copy,json
from pathlib import Path
import pytest
from jrdb_lh.common import digest,file_hash,release
from active_research.aligned.integrity import IntegrityFailure
from active_research.finegrained_v3 import lifecycle
from active_research.finegrained_v3.frozen_inputs import CONTROL,verify_matched_inputs,load_initial
from active_research.finegrained_v3.policy_contract import source_hash

@pytest.fixture
def frozen(tmp_path):
    out=tmp_path/'control';out.mkdir();origin=tmp_path/'original';origin.mkdir()
    (out/'policies').mkdir()
    source='def acquire(card, sdk):\n    return None\n'
    policy={'source':source,'policy_hash':source_hash(source),'policy_version':'v000','parent_policy_hash':None,'validation_result':{'valid':True}}
    def write(p,v):p.write_text(json.dumps(v))
    write(out/'frozen-p0-input.json',policy);write(out/'policies/research_policy_v000.json',policy)
    (out/'policies/research_policy_v000.py').write_text(source)
    write(origin/'initial-policy.json',policy)
    cfg={'managed_root':str(out/'runs'),'tariff':{'multiplier':.5}}
    provider={'model':'deepseek-ai/DeepSeek-V4-Flash-0731'}
    write(out/'config.json',cfg);write(out/'provider.json',provider)
    m={'control_protocol':CONTROL,'scientific_arm':'frozen_research_program','reused_initial_policy_hash':policy['policy_hash'],'schedule':[],'protected':{}}
    b={'P0_hash':policy['policy_hash'],'initial_policy_file_hash':file_hash(out/'frozen-p0-input.json'),
       'policy_record_file_hash':file_hash(out/'policies/research_policy_v000.json'),'control_source_hashes':{},
       'schedule_digest':digest([]),'provider':provider,'config_without_managed_root':{'tariff':{'multiplier':.5}},
       'R1':str(origin),'original_input_hashes':{'initial-policy.json':file_hash(origin/'initial-policy.json')}}
    write(out/'matched-r1-bindings.json',b);write(out/'manifest.json',m)
    return out,m,policy

def test_exact_P0_load_no_warmup(frozen,monkeypatch):
    out,m,p=frozen
    monkeypatch.setattr(lifecycle,'build',lambda *a:pytest.fail('Warmup forbidden'))
    initial,session=lifecycle.construct(out,{},None)
    assert initial==p and session is None
    initial['source']='mutation';assert load_initial(out,m)==p

@pytest.mark.parametrize('damage',['source','hash','new_version','updater','warmup','config','original','restart'])
def test_frozen_mutations_hard_stop(frozen,damage):
    out,m,p=frozen
    if damage=='source':(out/'policies/research_policy_v000.py').write_text('changed')
    elif damage=='hash':m['reused_initial_policy_hash']='bad'
    elif damage=='new_version':(out/'policies/research_policy_v001.json').write_text('{}')
    elif damage=='updater':(out/'policy_update-2024-01-06.json').write_text('{}')
    elif damage=='warmup':(out/'warmup-runs').mkdir()
    elif damage=='config':(out/'config.json').write_text('{}')
    elif damage=='original':(Path(json.loads((out/'matched-r1-bindings.json').read_text())['R1'])/'initial-policy.json').write_text('{}')
    else:(out/'initial-policy.json').write_text(json.dumps(p))
    with pytest.raises((IntegrityFailure,KeyError)):load_initial(out,m)

def test_ten_day_frozen_coordinator_never_calls_updater():
    days=['2024-01-06','2024-01-07','2024-01-08','2024-01-13','2024-01-14','2024-01-20','2024-01-21','2024-01-27','2024-01-28','2024-02-03']
    schedule=[{'day':day,'races':['race_'+str(i).zfill(24)]} for i,day in enumerate(days)]
    p={'policy_hash':'exact-P0','source':'immutable'};seen=[];frames=[];events=[]
    def one(day,race,policy):
        assert policy==p;seen.append((day,race));policy['source']='local mutation';return race
    def released(day,sealed,policy):assert policy==p;return [{'day':day}]
    def feedback(rows,history,day,policy,clock,races):
        assert policy==p;assert len(history)==len(frames);frames.append(day)
        return {'day':day,'release_clock':release(day),'frame_digest':digest(day)}
    def updater(*args):pytest.fail('Daily updater forbidden')
    active,history=lifecycle.coordinate(schedule,p,'frozen_research_program',one,released,feedback,updater,lambda kind,**kw:events.append((kind,kw)))
    assert active==p and len(seen)==len(frames)==len(history)==10
    assert all(e['policy_hash']=='exact-P0' for kind,e in events if kind=='day_start')

"""Offline tests only: in-memory fixture, no credentials/provider requests."""
import copy
import json
import sys
import time
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"tests"))
from conftest import source,config,service
from test_active_research import active
from active_research.adaptive import domains as d,runner as r
from jrdb_lh.common import Rejected,cutoff,release
from jrdb_lh.ledger import reconcile

def message(name,args):
    return {"tool_calls":[{"function":{"name":name,"arguments":json.dumps(args)}}]}

@pytest.fixture
def run(active):
    active.config=replace(active.config,limits=replace(active.config.limits,page_size=8,strategy_calls=64))
    active.state["binding"]={"source":active.source.manifest()}
    return SimpleNamespace(state=active.state,service=active,source=active.source,
        config=active.config,persist=lambda:None)

def race(run):return run.state["schedule"][0]["races"][0]

@pytest.mark.parametrize("name,args",[
 ("get_recent_history",{"horse_number":"99"}),
 ("get_training",{"horse_number":"01","race_id":"future"}),
 ("get_provider_signals",{"horse_number":"01"}),
 ("get_reference_odds",{}),
 ("get_track_context",{"horse_number":"01"})])
def test_action_boundary(name,args):
    with pytest.raises(Rejected):d.parse(message(name,args),["01"],"race")

def test_single_horse_history_original_cost_and_dedup(run):
    a={"type":"query","domain":"recent_history","horse_number":"01"}
    view,cost,_,_=d.fetch(run,a,race(run))
    assert len(view["starts"])==2
    assert all(x["values"]["finish"]==1 for x in view["starts"])
    assert "SECRET" not in json.dumps(view) and "odds" not in json.dumps(view)
    assert cost["data_micro"]>0
    _,repeat,_,_=d.fetch(run,a,race(run))
    assert repeat["repeat"] and repeat["data_micro"]==0 and repeat["call_micro"]==500000
    reconcile(run.state)

def test_training_narrows_horse_but_keeps_original_batch_fee(run):
    a={"type":"query","domain":"training","horse_number":"03"}
    view,cost,_,_=d.fetch(run,a,race(run))
    assert view["horse_number"]=="03" and view["training"]["early_time"]==123
    assert cost["charged_cells"]>len(view["training"])
    a["horse_number"]="04"
    view2,repeat,_,_=d.fetch(run,a,race(run))
    assert view2["horse_number"]=="04" and repeat["repeat"]

def test_condition_statistics_are_bounded_and_missing_not_imputed(run):
    view,_,_,_=d.fetch(run,{"type":"query","domain":"condition_stats","horse_number":"01"},race(run))
    assert view["sample_starts"]==2 and view["labelled_starts"]==2
    assert sum(x["wins"] for x in view["groups"])==2
    a={"domain":"condition_stats","horse_number":"01"}
    empty=d.project(a,{"examples":[]})
    assert empty["groups"]==[] and empty["labelled_starts"]==0

def test_future_result_cannot_be_reflected(run,tmp_path):
    with pytest.raises(Rejected,match="not released"):
        r.reflect(run,None,race(run),"2024-01-06",d.neutral(),tmp_path)
    with pytest.raises(Rejected):run.service.call("results",{"race_id":race(run)})

def test_same_day_memory_chain_rejected():
    with pytest.raises(Rejected):
        r.sequence_ok([{"day":"2024-01-06","race_id":str(i)} for i in range(3)])
    r.sequence_ok([{"day":"2024-01-0"+str(i),"race_id":str(i)} for i in (6,7,8)])

@pytest.mark.parametrize("bad",[float("nan"),-0.1,1.1,True])
def test_policy_weights_bounded(bad):
    p=d.neutral();p["domain_priorities"]["training"]=bad
    with pytest.raises(Rejected):d.validate_policy(p,None)

def test_policy_notes_bounded():
    p=d.neutral();p["strategy_notes"]="x"*1201
    with pytest.raises(Rejected):d.validate_policy(p,None)

def test_synthetic_closed_loop_and_semantic_provenance(run,tmp_path):
    observations=[]
    old=d.neutral();rid=race(run)
    new=copy.deepcopy(old);new["updated_after_race"]=rid
    new["domain_priorities"]["recent_history"]=0.6
    nums=["01","02","03","04","05"]
    responses=[message("get_recent_history",{"horse_number":"01"}),
               message("get_training",{"horse_number":"03"}),
               message("submit",{"ranking":nums,"probabilities":{n:.2 for n in nums}}),
               message("update_policy",{"reflection":"Bounded fixture update","new_policy":new})]
    class Agent:
        deadline=time.monotonic()+100
        def call(self,service,obs,tools,phase):
            observations.append(copy.deepcopy(obs));return responses[len(observations)-1]
    rid,day=r.predict(run,Agent(),old)
    assert observations[0]["evidence"]=={} and "training" not in str(observations[0]["basic_race_card"])
    run.state["prediction_phase_cost"]={}
    run.state.pop("strategy_job");run.state.pop("strategy_job_usage")
    r.settle(run,rid,day)
    assert run.state["clock"]==release(day)
    got=r.reflect(run,Agent(),rid,day,old,tmp_path)
    assert got==new and old==d.neutral()
    assert "actual_result" in observations[-1] and "evidence" not in observations[-1]
    assert run.state["model_turns"]==4
    assert r.verify_semantics(run.state)["semantic_projections"]=="PASS"
    assert (tmp_path/"memory_after.json").exists()

def test_40_turns_do_not_force_submit(run):
    class Agent:
        deadline=time.monotonic()+100
        def call(self,*a):return message("get_training",{"horse_number":"01"})
    with pytest.raises(r.SafetyLimit,match="max_model_turns"):r.predict(run,Agent(),d.neutral())
    assert run.state["model_turns"]==40 and not run.state["submissions"]
    assert len(run.state["repeated_actions"])==39

"""Three-day closed loop. No modifications to trusted service, ledger or evaluator."""
import copy
import gzip
import json
import sys
import time
from pathlib import Path
from jockeybench.errors import Rejected, Pending, BudgetExceeded, BudgetLimit, ProviderCostLimit, safe_error
from jockeybench.clock import cutoff, release
from jockeybench.canonical import digest, file_hash
from jockeybench.config import Config
from jockeybench.runner import Run, usage
from jockeybench.audit.replay import replay
from jockeybench.ledger import summary
from jockeybench.research.runner import PROJECT,ActiveService,basic_card,check_config,seal,settle
from jockeybench.research.llm.runner import wall_limit,WallClockExpired,write_json,result_report,scan_no_credential
from .model import AdaptiveAgent,load_settings,credential,redact,model_cost,PROMPT
from .domains import tools,parse,fetch,project,backend,trajectory,allocation,neutral,reflection_tool,parse_reflection,validate_policy,SUPPORTED,MISSING

MAX_TURNS=40
WALL_SECONDS=1800

class SafetyLimit(Exception): pass

def code_hash():
    return digest({p.name:file_hash(p) for p in sorted(Path(__file__).parent.glob("*.py"))})

def sequence_ok(entries):
    if len(entries)!=3 or len({e["race_id"] for e in entries})!=3:
        raise Rejected("Exactly three distinct races")
    for a,b in zip(entries,entries[1:]):
        if release(a["day"])>cutoff(b["day"]):
            raise Rejected("Previous outcome not released before next decision")

def predict(run,agent,memory):
    s=run.state;entry=s["schedule"][0];race=entry["races"][0]
    s.update(phase="predict",clock=cutoff(entry["day"]),usage=usage(),strategy_job=race,
        strategy_job_usage={"calls":0,"evidence_cells":0},active_trace=[],protocol_errors=[],
        repeated_actions=[],model_turns=0,query_count=0,memory_before=copy.deepcopy(memory))
    initial=run.service.call("card",{"race_id":race})
    card=basic_card(initial)
    s["active_initial"]={"card":card,"receipt":initial["_cost"]}
    numbers=[r["number"] for r in card["runners"]]
    knowledge={};seen={};invalid_seen={}
    run.persist()
    for turn in range(1,MAX_TURNS+1):
        if time.monotonic()>=agent.deadline: raise SafetyLimit("wall_clock")
        if s["clock"]!=cutoff(entry["day"]): raise Rejected("Prediction clock moved")
        s["model_turns"]=turn;s["usage"]["model_calls"]=turn
        observation={"mode":"prediction","basic_race_card":card,"research_policy":memory,
            "evidence":knowledge,"trajectory":trajectory(s),
            "available_domains":list(SUPPORTED),"unavailable_domains":MISSING,
            "model_turn":turn,"max_model_turns":MAX_TURNS,"query_budget":None,
            "ledger":summary(s,run.config),"safety_remaining_wall_seconds":max(0,agent.deadline-time.monotonic())}
        run.persist()
        message=agent.call(run.service,observation,tools(numbers),"prediction-"+str(turn))
        event={"turn":turn,"clock":s["clock"]}
        s["active_trace"].append(event)
        try:
            action=parse(message,numbers,race);event["action"]=action
            signature=digest(action)
            event["repeated_action"]=signature in seen
            if signature in seen:
                s["repeated_actions"].append({"turn":turn,"previous":seen[signature],"action":action})
            seen[signature]=turn
            if action["type"]=="submit":
                receipt=seal(run,race,action,numbers)
                event.update(cost=receipt["_cost"],sealed=True)
                s["submission_mode"]="voluntary"
                run.persist()
                return race,entry["day"]
            s["query_count"]+=1
            view,cost,evidence_id,operation=fetch(run,action,race)
            key=action["domain"]+":"+action.get("horse_number","race")
            knowledge[key]=view
            event.update(cost=cost,evidence_id=evidence_id,service_operation=operation,
                         observation_digest=digest(view),backend_repeat=cost.get("repeat",False),
                         cumulative_cost=summary(s,run.config)["cost_yen"])
        except (BudgetExceeded,BudgetLimit) as exc:
            event["error"]={"code":type(exc).__name__}
            run.persist()
            raise SafetyLimit("service_safety_ceiling") from None
        except Rejected as exc:
            signature=digest(message);repeated=signature in invalid_seen
            invalid_seen[signature]=turn
            error={"turn":turn,"detail":redact(safe_error(exc)),"identical_invalid_repeat":repeated}
            event["error"]=error;s["protocol_errors"].append(error)
        run.persist()
    raise SafetyLimit("max_model_turns")

def reflect(run,agent,race,day,memory,folder):
    s=run.state
    if s["clock"]<release(day) or s.get("release_ledger",{}).get(race)!=release(day):
        raise Rejected("Outcome reflection is not released")
    if s["model_turns"]>=MAX_TURNS: raise SafetyLimit("max_model_turns_before_reflection")
    s.update(phase="reflect",reflection_started=True)
    # Actual result is requested through the same timed, paid service.
    actual=run.service.call("results",{"race_id":race})
    payload={"mode":"reflection","race_id":race,"old_policy":memory,
        "final_prediction":s["submissions"][race]["prediction"],
        "actual_result":{k:v for k,v in actual.items() if not k.startswith("_")},
        "prediction_score":s["scores"][race]["score_revenue_yen"],
        "prediction_phase_cost":s["prediction_phase_cost"],
        "ledger":summary(s,run.config),"model_cost_so_far":model_cost(s),
        "domain_trajectory":trajectory(s),"allocation":allocation(s),
        "stopping_mode":s["submission_mode"],"unavailable_domains":MISSING}
    write_json(folder/"reflection-input.json",payload)
    s["reflection_result_cost"]=actual["_cost"]
    s["model_turns"]+=1;s["usage"]["model_calls"]=s["model_turns"]
    run.persist()
    response=agent.call(run.service,payload,[reflection_tool(race)],"reflection")
    update=parse_reflection(response,race)
    record={"old_policy":memory,**update,"clock":s["clock"],"race_id":race,
            "input_digest":digest(payload),"output_digest":digest(update)}
    s["policy_reflection"]=record;s["memory_after"]=update["new_policy"]
    s["phase"]="completed"
    write_json(folder/"reflection.json",record)
    write_json(folder/"memory_after.json",s["memory_after"])
    run.persist()
    return s["memory_after"]

def verify_semantics(s):
    ops={a["operation"]:a for a in s["audit"]}
    events={e["evidence_id"]:e for e in s.get("active_trace",[]) if e.get("evidence_id")}
    for p in s.get("semantic_provenance",[]):
        row=ops[p["service_operation"]];event=events[p["evidence_id"]]
        name,args=backend(event["action"],s["schedule"][0]["races"][0])
        if p["backend_tool"]!=name or p["backend_arguments"]!=args or row["tool"]!=name or row["arguments"]!=args:
            raise Rejected("Horse/domain backend binding changed")
        if row["response_digest"]!=digest(p["backend_response"]) or p["response_digest"]!=row["response_digest"]:
            raise Rejected("Backend evidence digest changed")
        view=project(event["action"],p["backend_response"])
        if digest(view)!=p["semantic_view_digest"] or digest(view)!=event["observation_digest"]:
            raise Rejected("Semantic projection changed")
        if event["cost"]!=row["_cost"]: raise Rejected("Semantic cost receipt changed")
    requests=s.get("llm_trajectory",[])
    for t in requests:
        body=t["request"]
        if body["messages"][0]["content"]!=PROMPT:
            raise Rejected("Frozen prompt changed")
        obs=json.loads(body["messages"][1]["content"])
        if obs["mode"]=="prediction":
            if obs["research_policy"]!=s["memory_before"]: raise Rejected("Memory input changed")
            if "actual_result" in obs: raise Rejected("Future result in prediction observation")
        elif obs["mode"]=="reflection":
            if s["policy_reflection"]["clock"]<release(s["schedule"][0]["day"]):
                raise Rejected("Early reflection")
    return {"semantic_projections":"PASS","frozen_prompt_memory":"PASS",
            "evidence_records":len(s.get("semantic_provenance",[]))}

def run_one(config,settings,entry,memory,out,index,manifest_digest):
    ident="adaptive-"+str(index).zfill(2)
    folder=out/ident
    if folder.exists() or (Path(config.managed_root)/ident).exists(): raise Rejected("Never resume/rerun an attempted race")
    folder.mkdir(mode=0o700)
    write_json(folder/"memory_before.json",memory)
    started=time.monotonic();run=None;failure=None;audit=None;semantic=None;next_memory=None;status="infrastructure_error"
    try:
        with wall_limit(WALL_SECONDS):
            agent=AdaptiveAgent(settings,started+WALL_SECONDS)
            run=Run(config,ident,agent)
            run.state["schedule"]=[{"day":entry["day"],"races":[entry["race_id"]]}]
            run.state.update(clock=cutoff(entry["day"]),initial_clock=cutoff(entry["day"]))
            run.state["binding"]["adaptive"]={"manifest":manifest_digest,"code":code_hash(),
                "prompt":digest(PROMPT),"memory_before":digest(memory),"assignment":entry}
            service=ActiveService(run.source,config,run.state,run.persist)
            service.executor=run.executor;run.service=service;run.executor.service=service
            run.persist();run.preflight()
            race,day=predict(run,agent,memory)
            run.state["prediction_phase_cost"]=summary(run.state,config)
            run.state["prediction_model_cost"]=model_cost(run.state)
            run.state.pop("strategy_job",None);run.state.pop("strategy_job_usage",None)
            settle(run,race,day)
            next_memory=reflect(run,agent,race,day,memory,folder)
            audit=replay(config,run.state)
            semantic=verify_semantics(run.state)
            status="completed"
    except (SafetyLimit,WallClockExpired,ProviderCostLimit,BudgetExceeded,BudgetLimit) as exc:
        status="safety_limit_reached";failure=str(exc) if isinstance(exc,SafetyLimit) else type(exc).__name__
    except Pending:
        failure="provider_failed_after_at_most_one_retry"
    except Exception as exc:
        failure=type(exc).__name__
    if run is None:
        result={"race_id":entry["race_id"],"completed":False,"stop_status":status,"failure":failure}
    else:
        try:
            s=run.state
            if failure:
                s["active_failure"]=failure
                for a in s.get("provider_attempts",[]):
                    if a["status"]=="pending": a["status"]="interrupted"
                s["pending"]=None
                run.persist()
                try: audit=replay(config,s)
                except Exception: audit=None
            result=result_report(run,audit,failure)
            result.update(track="trajectory_conditioned_active_research",completed=status=="completed",
                stop_status=status,semantic_audit=semantic,
                audit_status="PASS" if audit else "FAIL",
                audit_scope="full_with_reflection" if status=="completed" else "partial",
                domain_trajectory=trajectory(s),allocation=allocation(s),
                unique_horses_inspected=len(allocation(s)["horses"]),
                backend_repeat_queries=sum(bool(e.get("backend_repeat")) for e in s.get("active_trace",[])),
                memory_before=memory,memory_after=s.get("memory_after"),
                prediction_phase_cost=s.get("prediction_phase_cost"),
                prediction_model_cost=s.get("prediction_model_cost"),
                reflection_result_cost=s.get("reflection_result_cost"),
                decision_model_turns=len([t for t in s.get("llm_trajectory",[]) if t["phase"].startswith("prediction-")]),
                reflection_model_turns=len([t for t in s.get("llm_trajectory",[]) if t["phase"]=="reflection"]),
                model_turns_including_reflection=s.get("model_turns",0),
                network_retries=max(0,len(s.get("provider_attempts",[]))-len(s.get("llm_trajectory",[]))),
                wall_seconds=time.monotonic()-started,adaptive_code_hash=code_hash(),prompt_hash=digest(PROMPT))
            run.storage.write("adaptive-report.json.gz",redact(result))
        finally: run.close()
    write_json(folder/"result.json",result)
    return result,next_memory if status=="completed" else None

def main():
    out=Path(sys.argv[1]).resolve()
    if not out.is_relative_to(PROJECT): raise Rejected("Copy-only output")
    manifest=json.loads((out/"manifest.json").read_text())
    if (out/"started.json").exists(): raise Rejected("Batch already attempted")
    sequence_ok(manifest["races"])
    if manifest["code_digest"]!=code_hash() or manifest["prompt_digest"]!=digest(PROMPT):
        raise Rejected("Frozen track changed")
    for name in ("config","provider"):
        if file_hash(out/(name+".json"))!=manifest[name+"_digest"]: raise Rejected("Frozen settings changed")
    config=Config.load(out/"config.json");check_config(config)
    settings=load_settings(out/"provider.json");credential(settings)
    memory=neutral();validate_policy(memory,None)
    write_json(out/"active_research_policy.json",memory)
    write_json(out/"started.json",{"manifest":digest(manifest),"maximum_real_races":3})
    results=[]
    for i,entry in enumerate(manifest["races"],1):
        if i>1:
            # The latest compact file is the ONLY cross-race agent state.
            memory=json.loads((out/"active_research_policy.json").read_text())
            validate_policy(memory,manifest["races"][i-2]["race_id"])
        write_json(out/"status.json",{"stage":"running","race_index":i,"completed":len(results)})
        result,next_memory=run_one(config,settings,entry,memory,out,i,digest(manifest))
        results.append(result);write_json(out/"results.json",results)
        if next_memory is None: break  # Never invent a replacement learned policy after failure.
        write_json(out/"active_research_policy.json",next_memory)
    write_json(out/"credential-check.json",scan_no_credential(out))
    write_json(out/"status.json",{"stage":"finished","attempts":len(results),
        "stable_completions":sum(r["completed"] for r in results),"no_fourth_race":True})
    print(json.dumps({"attempts":len(results),"stable_completions":sum(r["completed"] for r in results)}))

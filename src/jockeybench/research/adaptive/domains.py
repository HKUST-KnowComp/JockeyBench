"""Semantic projections over original paid service responses. No direct DB reads."""
import json
import math
from collections import Counter
from jockeybench.errors import Rejected
from jockeybench.canonical import digest
from jockeybench.research.llm.agent import action_tools as old_tools, parse_action as old_parse

DOMAINS = ("recent_history","training","condition_stats","provider_signals","track_context","reference_odds")
SUPPORTED = ("recent_history","training","condition_stats","track_context")
MISSING = {"provider_signals":"Excluded by objective-v5; no authorized service projection",
           "reference_odds":"Market/reference/odds fields excluded by objective-v5"}
NOTES_LIMIT = 1200

def native(name, description, props, required):
    return {"type":"function","function":{"name":name,"description":description,
        "parameters":{"type":"object","properties":props,"required":required,"additionalProperties":False}}}

def tools(numbers):
    horse={"type":"string","enum":numbers}
    return [
        native("get_recent_history","Current entrant's five latest earlier starts, only released labels. Null is unknown. Paid native horse_history; repeat is not deeper history.",
               {"horse_number":horse},["horse_number"]),
        native("get_training","Selected entrant's permitted workout fields. Original backend charges the whole race training response; only selected horse is shown. Identical backend reads follow original dedup.",
               {"horse_number":horse},["horse_number"]),
        native("get_condition_stats","Descriptive surface/distance counts from this horse's five latest earlier starts, NOT lifetime or fitted stats. Original horse_history is charged; missing labels excluded.",
               {"horse_number":horse},["horse_number"]),
        native("get_track_context","Race-level permitted context including available forecasts beyond the basic card. Original whole card is metered and deduplicated.",{},[]),
        old_tools(numbers,True)[0]]

def one_call(message):
    calls=message.get("tool_calls") or []
    if len(calls)!=1: raise Rejected("Exactly one native action required")
    try:
        fn=calls[0]["function"]
        args=json.loads(fn["arguments"],parse_constant=lambda x: (_ for _ in ()).throw(ValueError()))
        if not isinstance(args,dict): raise ValueError()
        return fn["name"],args
    except (KeyError,TypeError,ValueError):
        raise Rejected("Invalid action JSON") from None

def parse(message,numbers,race):
    name,args=one_call(message)
    if name=="submit": return old_parse(message,numbers,race)
    if name not in {"get_"+d for d in SUPPORTED}: raise Rejected("Unavailable domain")
    domain=name[4:]
    required=set() if domain=="track_context" else {"horse_number"}
    if set(args)!=required: raise Rejected("Unexpected domain arguments")
    if required and (not isinstance(args["horse_number"],str) or args["horse_number"] not in numbers):
        raise Rejected("Horse must be a current entrant")
    return {"type":"query","domain":domain,**args}

def backend(action,race):
    domain=action["domain"]
    if domain in {"recent_history","condition_stats"}:
        return "horse_history",{"race_id":race,"number":action["horse_number"],"limit":5,"order":"recent"}
    return ("training" if domain=="training" else "card"),{"race_id":race}

def project(action,answer):
    domain=action["domain"]; number=action.get("horse_number")
    if domain=="track_context":
        return {"facts":answer["facts"],"missing":answer["missing"],"feature_asof":answer["feature_asof"]}
    if domain=="training":
        records=[r for r in answer["records"] if r["number"]==number]
        if len(records)!=1: raise Rejected("Missing or ambiguous training entrant")
        return {"horse_number":number,"training":records[0]["values"],"missing":records[0]["missing"],
                "feature_asof":answer["feature_asof"],"billing_scope":"whole-race original training response"}
    starts=[]
    for sample in answer["examples"]:
        n=sample["matched_number"]
        runners=sample["card"]["runners"]
        if len(runners)!=1 or runners[0]["number"]!=n: raise Rejected("Ambiguous horse history")
        starts.append({"race_id":sample["card"]["race_id"],"day_index":sample["card"]["day_index"],
                       "race_facts":sample["card"]["facts"],"runner_facts":runners[0]["facts"],
                       "values":(sample.get("labels") or {}).get(n),
                       "missing":(sample.get("label_missing") or {}).get(n),
                       "label_scope":sample["label_scope"]})
    if domain=="recent_history":
        return {"horse_number":number,"starts":starts,"scope":"latest five earlier starts; released labels only"}
    groups={}
    for s in starts:
        v=s["values"] or {}; finish=v.get("finish")
        if type(finish) not in (int,float) or finish<1: continue
        surface=s["race_facts"].get("surface"); distance=s["race_facts"].get("distance")
        key=json.dumps([surface,distance])
        g=groups.setdefault(key,{"surface":surface,"distance":distance,"starts":0,"wins":0,"top3":0})
        g["starts"]+=1;g["wins"]+=int(finish==1);g["top3"]+=int(finish<=3)
    return {"horse_number":number,"groups":list(groups.values()),"sample_starts":len(starts),
            "labelled_starts":sum(g["starts"] for g in groups.values()),
            "scope":"only latest five earlier starts; descriptive counts, not lifetime; missing not imputed"}

def fetch(run,action,race):
    name,args=backend(action,race)
    answer=run.service.call(name,args)
    view=project(action,answer)
    record=run.state["audit"][-1]
    provenance={"evidence_id":digest([record["operation"],action,view]),
        "service_operation":record["operation"],"backend_tool":name,"backend_arguments":args,
        "clock":run.state["clock"],"race_id":race,"horse_number":action.get("horse_number"),
        "source_manifest":run.state["binding"]["source"],"response_digest":digest(answer),
        "source_tables":["races","entries"],"field_provenance":"Original reviewed service projection; raw timestamps not exposed",
        "backend_response":answer,"semantic_view_digest":digest(view)}
    run.state.setdefault("semantic_provenance",[]).append(provenance)
    return view,answer["_cost"],provenance["evidence_id"],record["operation"]

def neutral():
    return {"domain_priorities":{d:0.5 for d in DOMAINS},"strategy_notes":"","updated_after_race":None}

def validate_policy(value,race):
    if not isinstance(value,dict) or set(value)!={"domain_priorities","strategy_notes","updated_after_race"}:
        raise Rejected("Invalid policy shape")
    weights=value["domain_priorities"]
    if not isinstance(weights,dict) or set(weights)!=set(DOMAINS): raise Rejected("Invalid priority domains")
    if any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1 for v in weights.values()):
        raise Rejected("Priority must be finite in [0,1]")
    if not isinstance(value["strategy_notes"],str) or len(value["strategy_notes"])>NOTES_LIMIT:
        raise Rejected("Policy notes too long")
    if value["updated_after_race"]!=race: raise Rejected("Policy race binding mismatch")
    return value

def reflection_tool(race):
    return native("update_policy","Return bounded reflection and compact policy; descriptive beliefs, not forced sampling.",
        {"reflection":{"type":"string","maxLength":1200},
         "new_policy":{"type":"object","properties":{
             "domain_priorities":{"type":"object","properties":{d:{"type":"number","minimum":0,"maximum":1} for d in DOMAINS},
                 "required":list(DOMAINS),"additionalProperties":False},
             "strategy_notes":{"type":"string","maxLength":NOTES_LIMIT},
             "updated_after_race":{"type":"string","enum":[race]}},
             "required":["domain_priorities","strategy_notes","updated_after_race"],"additionalProperties":False}},
        ["reflection","new_policy"])

def parse_reflection(message,race):
    name,args=one_call(message)
    if name!="update_policy" or set(args)!={"reflection","new_policy"}:
        raise Rejected("Expected update_policy")
    if not isinstance(args["reflection"],str) or len(args["reflection"])>1200:
        raise Rejected("Reflection too long")
    validate_policy(args["new_policy"],race)
    return args

def trajectory(state):
    return [{"step":e["turn"],"domain":e.get("action",{}).get("domain",e.get("action",{}).get("type","invalid")),
             "horse_number":e.get("action",{}).get("horse_number"),
             "cost":e.get("cost"),"repeated":e.get("repeated_action",False),
             "error":e.get("error")} for e in state.get("active_trace",[])]

def allocation(state):
    events=[e.get("action",{}) for e in state.get("active_trace",[]) if e.get("action",{}).get("type")=="query"]
    return {"domains":dict(Counter(a["domain"] for a in events)),
            "horses":dict(Counter(a["horse_number"] for a in events if "horse_number" in a))}

"""Frozen five-race behavioral smoke; no changes to the original provider/core."""
import copy
import gzip
import json
import sys
import time
from pathlib import Path
from jockeybench.errors import Rejected, Pending, BudgetExceeded, ProviderCostLimit, safe_error
from jockeybench.clock import cutoff
from jockeybench.canonical import digest, file_hash
from jockeybench.config import Config
from jockeybench.runner import Run, usage
from jockeybench.ledger import summary
from jockeybench.audit.replay import replay
from jockeybench.research.runner import PROJECT, ActiveService, basic_card, check_config, query_arguments, seal, settle
from jockeybench.research.llm.agent import LLMActionAgent, SYSTEM_PROMPT, action_tools, parse_action, load_settings, credential, redact
from jockeybench.research.llm.runner import wall_limit, WallClockExpired, write_json, result_report, scan_no_credential

MAX_TURNS = 30
WALL_SECONDS = 1800
OLD_LIMITS = "At most 6 query actions\nand 8 total model turns are allowed. If only submit is offered you MUST submit now."
NEW_LIMITS = ("There is no query-count research budget; decide when to submit based on information value and cost. "
              "The engineering safety ceilings are 30 model turns, 30 minutes and a provider spend limit; "
              "reaching a ceiling stops the run without submitting on your behalf.")
assert SYSTEM_PROMPT.count(OLD_LIMITS) == 1
FROZEN_PROMPT = SYSTEM_PROMPT.replace(OLD_LIMITS, NEW_LIMITS)


class SafetyLimit(Exception):
    pass


class BehavioralAgent(LLMActionAgent):
    def call(self, service, observation, tools, phase_key):
        return super().call(service, observation, tools, phase_key, system=FROZEN_PROMPT)


def code_hash():
    return digest({p.name: file_hash(p) for p in sorted(Path(__file__).parent.glob("*.py"))})


def loop(run, agent):
    state = run.state
    day = state["schedule"][0]
    race = day["races"][0]
    state.update(phase="predict", clock=cutoff(day["day"]), usage=usage(), model_turns=0,
                 query_count=0, protocol_errors=[], repeated_actions=[], active_trace=[],
                 strategy_job=race, strategy_job_usage={"calls": 0, "evidence_cells": 0})
    initial = run.service.call("card", {"race_id": race})
    card = basic_card(initial)
    state["active_initial"] = {"card": card, "receipt": initial["_cost"]}
    numbers = [r["number"] for r in card["runners"]]
    evidence = {}
    seen = {digest(["card", {"race_id": race}]): "initial_card"}
    run.persist()
    for turn in range(1, MAX_TURNS + 1):
        if time.monotonic() >= agent.deadline:
            raise SafetyLimit("wall_clock")
        if state["clock"] != cutoff(day["day"]):
            raise Rejected("Decision clock moved")
        # All four actions remain exposed, including the final allowed turn.
        obs = {"track": "active_research", "basic_card": copy.deepcopy(card),
               "evidence": copy.deepcopy(evidence), "model_turn": turn, "query_actions": state["query_count"],
               "max_query_actions": None, "max_model_turns": MAX_TURNS, "limits_are_engineering_safety_only": True,
               "must_submit": False, "allowed_actions": ["form", "connections", "card", "submit"],
               "remaining_wall_seconds": max(0, round(agent.deadline - time.monotonic(), 2)),
               "ledger": summary(state, run.config), "budget": run.service.budget(),
               "protocol_feedback": state["protocol_errors"][-1:]}
        state["model_turns"] = turn
        state["usage"]["model_calls"] = turn
        run.persist()
        message = agent.call(run.service, obs, action_tools(numbers), "race-turn-" + str(turn))
        event = {"turn": turn, "clock": state["clock"], "submit_only_reasons": []}
        state["active_trace"].append(event)
        try:
            action = parse_action(message, numbers, race, False)
            event["action"] = action
            if action["type"] == "query":
                tool, args = query_arguments(action, race)
                state["query_count"] += 1
                key = digest([tool, args])
                event.update(query_object=args, repeated_action=key in seen)
                if key in seen:
                    state["repeated_actions"].append({"turn": turn, "tool": tool, "arguments": args, "previous": seen[key]})
                seen[key] = turn
                answer = run.service.call(tool, args)
                evidence[tool] = answer
                event.update(evidence=answer, cost=answer["_cost"], cumulative_protocol_cost=summary(state, run.config)["cost_yen"])
            else:
                receipt = seal(run, race, action, numbers)
                event.update(cost=receipt["_cost"], sealed=True, cumulative_protocol_cost=summary(state, run.config)["cost_yen"])
                state["submission_mode"] = "voluntary"
                run.persist()
                return race, day["day"]
        except BudgetExceeded as exc:
            event["error"] = {"kind": "service_safety_limit", "detail": redact(safe_error(exc))}
            if state.get("audit"):
                event["cost"] = state["audit"][-1].get("_cost")
            run.persist()
            raise SafetyLimit("service_safety_ceiling") from None
        except Rejected as exc:
            error = {"turn": turn, "kind": "protocol_or_query_error", "detail": redact(safe_error(exc))}
            event["error"] = error
            if event.get("action", {}).get("type") == "query" and state.get("audit"):
                row = state["audit"][-1]
                if row.get("tool") == event["action"]["tool"]:
                    event["cost"] = row.get("_cost")
            event["cumulative_protocol_cost"] = summary(state, run.config)["cost_yen"]
            state["protocol_errors"].append(error)
        run.persist()
    raise SafetyLimit("max_model_turns")


def run_race(config, settings, entry, manifest_digest, batchout, index):
    runid = "behavioral-" + str(index).zfill(2)
    if (Path(config.managed_root) / runid).exists():
        raise Rejected("Existing race attempt; never resume or rerun")
    started = time.monotonic()
    run = None
    audit, failure, status = None, None, "infrastructure_error"
    try:
        with wall_limit(WALL_SECONDS):
            agent = BehavioralAgent(settings, started + WALL_SECONDS)
            run = Run(config, runid, agent)
            # The immutable assignment was chosen chronologically, before any outcome read.
            run.state["schedule"] = [{"day": entry["day"], "races": [entry["race_id"]]}]
            run.state.update(clock=cutoff(entry["day"]), initial_clock=cutoff(entry["day"]))
            run.state["binding"]["behavioral"] = {"manifest": manifest_digest, "assignment": entry,
                                                "code": code_hash(), "prompt": digest(FROZEN_PROMPT)}
            service = ActiveService(run.source, config, run.state, run.persist)
            service.executor = run.executor
            run.service = service
            run.executor.service = service
            run.persist()
            run.preflight()
            race, day = loop(run, agent)
            run.state.pop("strategy_job", None)
            run.state.pop("strategy_job_usage", None)
            settle(run, race, day)
            audit = replay(config, run.state)
            status = "voluntary_submit"
    except (WallClockExpired, SafetyLimit, ProviderCostLimit, BudgetExceeded) as exc:
        status = "safety_limit_reached"
        failure = str(exc) if isinstance(exc, SafetyLimit) else type(exc).__name__
    except Pending:
        failure = "provider_request_failed_zero_retries"
    except Exception as exc:
        failure = type(exc).__name__
    if run is None:
        result = {"run_id": runid, "race_id": entry["race_id"], "completed": False,
                  "stop_status": status, "protocol_failure": failure, "audit_status": "NOT_RUN"}
    else:
        try:
            if failure:
                run.state["active_failure"] = failure
                run.state["stop_status"] = status
                for a in run.state.get("provider_attempts", []):
                    if a["status"] == "pending":
                        a["status"] = "interrupted"
                run.state["pending"] = None
                run.persist()
                # Read-only audit of any partial charges, never a retry or salvage.
                try:
                    audit = replay(config, run.state)
                except Exception:
                    audit = None
            result = result_report(run, audit, failure)
            result.update(stop_status=status, safety_limit_reached=status == "safety_limit_reached",
                          audit_status="PASS" if audit else "FAIL",
                          wall_seconds=time.monotonic() - started,
                          frozen_prompt_sha256=digest(FROZEN_PROMPT),
                          behavioral_code_hash=code_hash(), manifest_digest=manifest_digest)
            result["completed"] = bool(run.state["completed"] and status == "voluntary_submit" and audit)
            result["final_ranking"] = (result.get("prediction") or {}).get("ranking")
            result["final_probabilities"] = (result.get("prediction") or {}).get("probabilities")
            run.storage.write("behavioral-report.json.gz", redact(result))
        finally:
            run.close()
    write_json(batchout / (runid + ".json"), result)
    return result


def main():
    manifest_path = Path(sys.argv[1]).resolve()
    batchout = manifest_path.parent
    manifest = json.loads(manifest_path.read_text())
    if (batchout / "started.json").exists():
        raise Rejected("Batch already started; no continuation or rerun")
    assert len(manifest["races"]) == 5 and len({e["race_id"] for e in manifest["races"]}) == 5
    assert manifest["prompt_digest"] == digest(FROZEN_PROMPT)
    assert manifest["code_digest"] == code_hash()
    assert manifest["config_digest"] == file_hash(PROJECT / manifest["config"])
    assert manifest["provider_digest"] == file_hash(PROJECT / manifest["provider"])
    settings = load_settings(PROJECT / manifest["provider"])
    credential(settings)
    config = Config.load(PROJECT / manifest["config"])
    check_config(config)
    assert config.protocol == "economic-v1" and config.tariff.hit_bonus_yen == 0
    assert Path(config.managed_root).resolve().is_relative_to(batchout)
    write_json(batchout / "started.json", {"manifest": digest(manifest), "real_races_authorized": 5})
    results = []
    for i, entry in enumerate(manifest["races"], 1):
        write_json(batchout / "status.json", {"stage": "running", "race_index": i, "completed_attempts": len(results)})
        result = run_race(config, settings, entry, digest(manifest), batchout, i)
        results.append(result)
        write_json(batchout / "results.json", results)
        if result["stop_status"] == "infrastructure_error":
            break
    scan = scan_no_credential(batchout)
    write_json(batchout / "credential-check.json", scan)
    write_json(batchout / "status.json", {"stage": "finished", "attempted_races": len(results),
                                        "stable_completions": sum(r["completed"] for r in results),
                                        "no_sixth_race": True})
    print(json.dumps({"attempted_races": len(results), "stable_completions": sum(r["completed"] for r in results)}))

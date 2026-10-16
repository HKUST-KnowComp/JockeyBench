import argparse
import copy
import gzip
import json
import os
import signal
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from jrdb_lh.audit import replay
from jrdb_lh.common import Rejected, Pending, digest, file_hash, cutoff, safe_error
from jrdb_lh.ledger import reconcile
from jrdb_lh.runner import Run, usage
from active_research.runner import (PROJECT, ActiveService, basic_card, check_config,
    query_arguments, seal, settle, observation, track_hash)
from jrdb_lh.config import Config
from .agent import (LLMActionAgent, MAX_QUERIES, MAX_TURNS, WALL_SECONDS, action_tools,
    parse_action, load_settings, credential, redact, model_cost, submit_only_reasons)


class WallClockExpired(BaseException):
    pass


@contextmanager
def wall_limit(seconds):
    def expire(signum, frame):
        raise WallClockExpired()
    previous = signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def llm_hash():
    return digest({"base": track_hash(), "llm": {p.name: file_hash(p) for p in sorted(Path(__file__).parent.glob("*.py"))}})


def write_json(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(redact(value), ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def preflight(settings, out):
    state = {"run_id": "active-native-preflight", "phase": "preflight", "index": 0,
             "usage": {}, "provider_attempts": [], "model_outputs": []}
    service = SimpleNamespace(state=state, persist=lambda: write_json(out / "preflight-trajectory.json", state))
    checked = dict(settings, max_estimated_cost_usd=0.03)
    agent = LLMActionAgent(checked, time.monotonic() + 120)
    numbers = ["01", "02", "03"]
    # Synthetic protocol checks, not additional evaluated races or real data queries.
    with wall_limit(120):
        message = agent.call(service, {"runner_numbers": numbers, "instruction": "Call form with k=1."},
                             action_tools(numbers), "preflight-query", forced="form",
                             system="Protocol preflight with synthetic placeholders. Follow the requested single native action.")
        action = parse_action(message, numbers, "synthetic")
        if action != {"type": "query", "tool": "form", "arguments": {"k": 1}}:
            raise Rejected("Preflight query schema failed")
        message = agent.call(service, {"runner_numbers": numbers, "instruction":
                            "Submit ranking 01,02,03 and probabilities 0.5,0.3,0.2."},
                             action_tools(numbers, True), "preflight-submit", forced="submit",
                             system="Protocol preflight with synthetic placeholders. Follow the requested single native action.")
        action = parse_action(message, numbers, "synthetic", True)
        if action["prediction"]["ranking"] != numbers:
            raise Rejected("Preflight submit schema failed")
    result = {"passed": True, "provider": "DeepInfra", "model": settings["model"],
              "requests": len(state["provider_attempts"]), "network_retries": 0,
              "native_query_schema": True, "native_submit_schema": True, "model_cost": model_cost(state)}
    write_json(out / "preflight-result.json", result)
    return result


def public_observation(run, card, evidence, turn, query_count, errors):
    value = observation(run, card, evidence, turn - 1)
    reasons = submit_only_reasons(query_count, turn)
    value.update(step=turn, model_turn=turn, max_model_turns=MAX_TURNS,
                 query_actions=query_count, max_query_actions=MAX_QUERIES,
                 must_submit=bool(reasons), submit_only_reasons=reasons,
                 allowed_actions=["submit"] if reasons else ["form", "connections", "card", "submit"],
                 remaining_wall_seconds=max(0, round(run.agent.deadline - time.monotonic(), 2)),
                 protocol_feedback=errors[-1:] if errors else [])
    return value


def bounded_loop(run, agent):
    state = run.state
    day = state["schedule"][0]
    if len(state["schedule"]) != 1 or len(day["races"]) != 1:
        raise Rejected("Exactly one race required")
    race = day["races"][0]
    state.update(phase="predict", clock=cutoff(day["day"]), usage=usage(),
                 strategy_job=race, strategy_job_usage={"calls": 0, "evidence_cells": 0})
    state["binding"]["active_research_llm"] = {"code": llm_hash(), "max_query_actions": MAX_QUERIES,
                                              "max_model_turns": MAX_TURNS, "wall_seconds": WALL_SECONDS}
    state.update(active_trace=[], protocol_errors=[], repeated_actions=[], model_turns=0, query_count=0)
    initial = run.service.call("card", {"race_id": race})
    card = basic_card(initial)
    state["active_initial"] = {"card": card, "receipt": initial["_cost"]}
    numbers = [r["number"] for r in card["runners"]]
    evidence = {}
    seen = {digest(["card", {"race_id": race}]): "initial_card"}
    run.persist()
    for turn in range(1, MAX_TURNS + 1):
        if time.monotonic() >= agent.deadline:
            raise WallClockExpired()
        if state["clock"] != cutoff(day["day"]):
            raise Rejected("Decision clock moved")
        obs = public_observation(run, card, evidence, turn, state["query_count"], state["protocol_errors"])
        state["model_turns"] = turn
        state["usage"]["model_calls"] = turn
        run.persist()
        message = agent.call(run.service, obs, action_tools(numbers, obs["must_submit"]),
                             "race-turn-" + str(turn))
        event = {"turn": turn, "clock": state["clock"], "submit_only_reasons": obs["submit_only_reasons"]}
        state["active_trace"].append(event)
        try:
            action = parse_action(message, numbers, race, obs["must_submit"])
            event["action"] = action
            if action["type"] == "query":
                tool, args = query_arguments(action, race)
                # Count valid attempts, including service errors and repeated reads.
                state["query_count"] += 1
                key = digest([tool, args])
                repeated = key in seen
                event.update(query_object=args, repeated_action=repeated)
                if repeated:
                    state["repeated_actions"].append({"turn": turn, "tool": tool, "arguments": args, "previous": seen[key]})
                seen[key] = turn
                answer = run.service.call(tool, args)
                evidence[tool] = answer
                event.update(evidence=answer, cost=answer["_cost"])
            else:
                receipt = seal(run, race, action, numbers)
                event.update(cost=receipt["_cost"], sealed=True)
                state["submission_mode"] = "query_cap" if "query_cap" in obs["submit_only_reasons"] else (
                    "model_turn_cap" if obs["must_submit"] else "voluntary")
                run.persist()
                return race, day["day"]
        except Rejected as exc:
            error = {"turn": turn, "kind": "protocol_or_query_error", "detail": redact(safe_error(exc))}
            event["error"] = error
            if event.get("action", {}).get("type") == "query" and state.get("audit"):
                receipt = state["audit"][-1]
                if receipt.get("tool") == event["action"]["tool"]:
                    event["cost"] = receipt.get("_cost")
            state["protocol_errors"].append(error)
        run.persist()
    raise Rejected("Protocol failure: model turn limit reached without a valid submit")


def result_report(run, audit=None, failure=None):
    state = run.state
    race = state["schedule"][0]["races"][0]
    totals = reconcile(state)
    model = model_cost(state)
    net = (totals["revenue_micro"] - totals["cost_micro"]) / 1e6
    entries = state.get("ledger", {}).get("entries", [])
    # Ledger structure is read through the original helper, not reconstructed.
    from jrdb_lh.ledger import ledger
    entries = ledger(state)["entries"]
    costs = {k: sum(e["amount_micro"] for e in entries if e["category"] == k) / 1e6
             for k in ("execution_data", "exploration_data", "calls", "compute")}
    sealed = state["submissions"].get(race)
    return {"track": "active_research_llm", "provider": "DeepInfra", "model": run.agent.settings["model"],
            "run_id": state["run_id"], "race_id": race, "date": state["schedule"][0]["day"],
            "completed": state["completed"], "protocol_failure": failure,
            "llm_turns": state.get("model_turns", 0), "query_actions": state.get("query_count", 0),
            "submission_mode": state.get("submission_mode"), "trace": state.get("active_trace", []),
            "initial_card_cost": state.get("active_initial", {}).get("receipt"),
            "prediction": sealed["prediction"] if sealed else None,
            "prediction_score": state["scores"].get(race, {}).get("score_revenue_yen"),
            "score_currency": "score_yen_not_money", "cost_breakdown": costs,
            "total_information_cost": costs["execution_data"] + costs["exploration_data"] + costs["calls"],
            "total_protocol_cost": totals["cost_micro"] / 1e6,
            "model_cost": model, "net_score": net if state["completed"] else None,
            "net_B_after_model_estimate": net - model["observed_or_estimated_usd"] * run.config.tariff.usd_to_yen
                 if state["completed"] and not model["unknown_attempts"] else None,
            "usd_to_score_yen": run.config.tariff.usd_to_yen,
            "protocol_errors": state.get("protocol_errors", []), "repeated_actions": state.get("repeated_actions", []),
            "provider_errors": [x for x in state.get("provider_attempts", []) if x["status"] != "complete"],
            "network_retries": 0, "provider_attempts": len(state.get("provider_attempts", [])),
            "isolation": state.get("isolation"), "audit": audit,
            "trajectory": str(run.storage.path / "checkpoint.json.gz"),
            "core_code_hash": state["binding"]["code"], "llm_code_hash": llm_hash()}


def run_one(config, settings, run_id, out):
    check_config(config)
    if (Path(config.managed_root) / run_id).exists():
        raise Rejected("Run exists; no resume or second attempt allowed")
    run, audit, failure = None, None, None
    started = time.monotonic()
    try:
        with wall_limit(WALL_SECONDS):
            agent = LLMActionAgent(settings, started + WALL_SECONDS)
            run = Run(config, run_id, agent)
            service = ActiveService(run.source, config, run.state, run.persist)
            service.executor = run.executor
            run.service = service
            run.executor.service = service
            run.preflight()
            race, day = bounded_loop(run, agent)
            run.state.pop("strategy_job", None)
            run.state.pop("strategy_job_usage", None)
            settle(run, race, day)
            audit = replay(config, run.state)
    except WallClockExpired:
        failure = "wall_clock_limit_600_seconds"
    except Pending:
        failure = "provider_request_failed_zero_retries"
    except Exception as exc:
        # Do not log exception bodies/headers or credentials.
        failure = "protocol_or_provider_failure:" + type(exc).__name__
        if run is not None and isinstance(exc, Rejected):
            run.state.setdefault("protocol_errors", []).append({"kind": type(exc).__name__, "detail": redact(safe_error(exc))})
    if run is None:
        result = {"completed": False, "protocol_failure": failure, "race_not_started": True}
    else:
        try:
            if failure:
                run.state["active_failure"] = failure
                for a in run.state.get("provider_attempts", []):
                    if a["status"] == "pending":
                        a["status"] = "interrupted"
                run.state["pending"] = None
            run.persist()
            result = result_report(run, audit, failure)
            result["wall_seconds"] = time.monotonic() - started
            run.storage.write("active-llm-report.json.gz", redact(result))
        finally:
            run.close()
    write_json(out / "race-result.json", result)
    return result


def audit_saved(config, run_id):
    path = Path(config.managed_root) / run_id / "checkpoint.json.gz"
    with gzip.open(path, "rt") as stream:
        state = json.load(stream)
    if state.get("checkpoint_digest") != digest({k: v for k, v in state.items() if k != "checkpoint_digest"}):
        raise Rejected("Checkpoint digest mismatch")
    if state["binding"].get("active_research_llm", {}).get("code") != llm_hash():
        raise Rejected("Exact LLM track code required")
    return replay(config, state)


def scan_no_credential(folder):
    secret = os.environ.get("DEEPINFRA_API_KEY", "")
    if not secret:
        return {"credential_scan": "unavailable"}
    needle = secret.encode()
    checked = 0
    for p in Path(folder).rglob("*"):
        if not p.is_file():
            continue
        raw = p.read_bytes()
        if p.suffix == ".gz":
            raw = gzip.decompress(raw)
        if needle in raw:
            raise Rejected("Credential persistence check failed")
        checked += 1
    return {"credential_scan": "passed", "files_checked": checked}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--run-id", default="active-llm-smoke-001")
    args = parser.parse_args()
    out = Path(args.output).resolve()
    if not out.is_relative_to(PROJECT) or out.exists():
        raise Rejected("Use a fresh output directory inside this project copy")
    settings = load_settings(args.provider)
    credential(settings)  # if unavailable: stop before files or model calls
    out.mkdir(parents=True, mode=0o700)
    write_json(out / "status.json", {"stage": "preflight"})
    try:
        checked = preflight(settings, out)
        write_json(out / "status.json", {"stage": "single_race", "preflight": "passed"})
        config = Config.load(args.config)
        result = run_one(config, settings, args.run_id, out)
        if result.get("completed") and not result.get("protocol_failure"):
            write_json(out / "independent-replay.json", audit_saved(config, args.run_id))
        scan = scan_no_credential(out)
        scan2 = scan_no_credential(config.managed_root)
        write_json(out / "credential-check.json", {"output": scan, "runs": scan2})
        write_json(out / "status.json", {"stage": "completed" if result.get("completed") and not result.get("protocol_failure") else "failed",
                                        "real_races_attempted": 1, "no_second_race": True})
        print(json.dumps({"completed": result.get("completed"), "output": str(out),
                          "protocol_failure": result.get("protocol_failure")}))
    except (Exception, WallClockExpired) as exc:
        write_json(out / "status.json", {"stage": "failed", "error_type": type(exc).__name__,
                                        "no_retry_or_second_race": True})
        print(json.dumps({"completed": False, "error_type": type(exc).__name__, "output": str(out)}))
        return 2
    return 0 if result.get("completed") and not result.get("protocol_failure") else 2

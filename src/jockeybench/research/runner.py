"""One-race active loop using the original source, service, sandbox and evaluator."""
import argparse
import copy
import gzip
import json
from pathlib import Path
from jockeybench.audit.replay import replay
from jockeybench.errors import Rejected
from jockeybench.clock import cutoff, release
from jockeybench.canonical import digest, file_hash, packed
from jockeybench.config import Config
from jockeybench.protocol.service import Service as EconomicService
from jockeybench.scoring import validate, score as economic_score, revenue_components
from jockeybench.ledger import book, micro, reconcile, summary
from jockeybench.runner import Run, usage

PROJECT = Path(__file__).resolve().parents[1]
MAX_DECISIONS = 4
TOOLS = {"card", "form", "connections"}


def track_hash():
    return digest({p.name: file_hash(p) for p in sorted(Path(__file__).parent.glob("*.py"))})


class ActiveService(EconomicService):
    # Worker returns an action. Only the trusted outer loop may execute it.
    # Original service validation/receipts reject any direct worker SDK calls.
    read_tools = frozenset()


class MockAgent:
    identity = {"kind": "active-research-mock", "version": 1}

    def decide(self, run, observation):
        code = (Path(__file__).parent / "mock_agent.py").read_text()
        code += "\nresult = decide(" + repr(observation) + ")\n"
        return run.service.call("compute", {"code": code})["result"]


def basic_card(card):
    """Narrow an already metered card; do not silently reveal training/form."""
    return {
        "race_id": card["race_id"], "day_index": card["day_index"],
        "feature_asof": card["feature_asof"],
        "facts": {k: card["facts"].get(k) for k in ("distance", "surface", "direction", "venue")},
        "runners": [{"number": r["number"],
                     "facts": {k: r["facts"].get(k) for k in ("draw", "sex", "carried_weight_tenths_kg")},
                     "announced_cancelled": r.get("announced_cancelled")} for r in card["runners"]]}


def query_arguments(action, race):
    if not isinstance(action, dict) or set(action) != {"type", "tool", "arguments"} or action["type"] != "query":
        raise Rejected("Expected one query action")
    tool, args = action["tool"], action["arguments"]
    if not isinstance(tool, str) or tool not in TOOLS or not isinstance(args, dict):
        raise Rejected("Only card, form and connections queries are available")
    allowed = {"k"} if tool == "form" else set()
    if set(args) - allowed:
        raise Rejected("Only the current race may be queried; unsupported arguments")
    if tool == "form" and (type(args.get("k", 3)) is not int or not 1 <= args.get("k", 3) <= 5):
        raise Rejected("form k must be an integer from 1 to 5")
    return tool, {"race_id": race, **args}


def check_config(config):
    if (config.protocol != "economic-v1" or config.arm != "A" or config.projection != "objective-v5"
            or config.schedule_mode != "smoke" or config.smoke_days != 1 or config.races_per_day != 1):
        raise Rejected("Prototype requires economic-v1/A/objective-v5 and exactly one smoke race")
    if config.tariff.version != "score-yen-v2" or config.tariff.hit_bonus_yen != 0:
        raise Rejected("Use warmup information policy and score-yen-v2 with hit_bonus_yen=0; no program-delivery bonus")
    study = PROJECT.parent.resolve()
    for path in (config.source, config.python):
        if not Path(path).resolve().is_relative_to(study):
            raise Rejected("Data and Python must belong to this independent copy")
    if not Path(config.managed_root).resolve().is_relative_to(PROJECT):
        raise Rejected("Outputs must stay inside this project copy")


def observation(run, card, evidence, step):
    return {"track": "active_research", "basic_card": copy.deepcopy(card),
            "evidence": copy.deepcopy(evidence), "step": step,
            "must_submit": step == MAX_DECISIONS - 1,
            "allowed_actions": ["submit"] if step == MAX_DECISIONS - 1 else ["card", "form", "connections", "submit"],
            "ledger": summary(run.state, run.config),
            "budget": run.service.budget()}


def seal(run, race, action, entrants):
    if not isinstance(action, dict) or set(action) != {"type", "prediction"} or action["type"] != "submit":
        raise Rejected("Expected one submit action with ranking and probabilities")
    prediction = validate(action["prediction"], entrants)
    receipt = run.service.seal_prediction(race, prediction)
    run.state["submissions"][race].update(strategy=None, delivery="active_research", format_fallback=False)
    run.persist()
    return receipt


def settle(run, race, day):
    """Use the original scorer and ledger; only lifecycle orchestration is new."""
    state = run.state
    if race not in state["submissions"]:
        raise Rejected("Cannot settle before sealing")
    state.update(phase="settle", clock=release(day))
    value = economic_score(state["submissions"][race]["prediction"],
                           run.source.settlements(race), run.config.tariff,
                           delivery="active_research")
    value["format_fallback"] = False
    for category in ("revenue_log", "revenue_hit"):
        book(state, category + ":" + race, category, micro(value[category + "_yen"] or 0), race_id=race)
    state["scores"][race] = value
    state["feedback"][race] = {"status": value["status"], "revenue_yen": value["score_revenue_yen"],
                               **revenue_components(value)}
    state["release_ledger"][race] = release(day)
    state.update(completed=True, phase="completed")
    state["source_verified_after"] = run.source.manifest()
    if state["source_verified_after"] != state["binding"]["source"]:
        raise Rejected("Source changed")
    run.persist()


def active_loop(run, agent):
    state = run.state
    batch = state["schedule"][0]
    if len(state["schedule"]) != 1 or len(batch["races"]) != 1:
        raise Rejected("Exactly one race required")
    race = batch["races"][0]
    state.update(phase="predict", clock=cutoff(batch["day"]), usage=usage(),
                 strategy_job=race, strategy_job_usage={"calls": 0, "evidence_cells": 0})
    state["binding"]["active_research"] = {"code": track_hash(), "max_decisions": MAX_DECISIONS}
    state["active_trace"] = []
    run.persist()
    # Initial card is charged at its original full-card tariff, then narrowed.
    initial = run.service.call("card", {"race_id": race})
    card = basic_card(initial)
    state["active_initial"] = {"card": card, "receipt": initial["_cost"]}
    evidence = {}
    for step in range(MAX_DECISIONS):
        if state["clock"] != cutoff(batch["day"]):
            raise Rejected("Decision clock moved")
        public = observation(run, card, evidence, step)
        before = run.executor.files_digest()
        action = agent.decide(run, public)
        if run.executor.files_digest() != before:
            raise Rejected("Active agent must not write strategy files")
        event = {"step": step, "clock": state["clock"], "observation_digest": digest(public),
                 "action": action}
        state["active_trace"].append(event)
        run.persist()
        if isinstance(action, dict) and action.get("type") == "query":
            if public["must_submit"]:
                raise Rejected("Decision limit reached; final action must submit")
            tool, args = query_arguments(action, race)
            answer = run.service.call(tool, args)
            evidence[tool] = answer
            event.update(receipt=answer["_cost"], response_digest=digest(answer))
            run.persist()
        else:
            receipt = seal(run, race, action, [r["number"] for r in card["runners"]])
            event.update(receipt=receipt["_cost"], sealed=True)
            run.persist()
            break
    if race not in state["submissions"]:
        raise Rejected("Agent did not submit")
    state.pop("strategy_job", None)
    state.pop("strategy_job_usage", None)
    settle(run, race, batch["day"])
    audit = replay(run.config, state)  # original audit, including score-yen-v2 re-scoring
    totals = reconcile(state)
    score = state["scores"][race]
    result = {"track": "active_research", "agent": agent.identity, "completed": True,
              "run_id": state["run_id"], "date": batch["day"], "races": 1,
              "core_code_hash": state["binding"]["code"], "track_code_hash": track_hash(),
              "actions": [e["action"]["type"] + (":" + e["action"]["tool"] if e["action"]["type"] == "query" else "") for e in state["active_trace"]],
              "initial_card_cost": initial["_cost"], "delivery": "active_research",
              "top1_hit": score.get("top1"), "top3_hits": score.get("top3_hits"),
              "revenue_yen": totals["revenue_micro"] / 1e6,
              "cost_yen": totals["cost_micro"] / 1e6,
              "net_score": (totals["revenue_micro"] - totals["cost_micro"]) / 1e6,
              "currency": "score_yen_not_money", "tariff": run.config.tariff.public(),
              "llm_requests": len(state.get("provider_attempts", [])),
              "strategy_files": [p.name for p in run.executor.work.iterdir()],
              "isolation": state["isolation"], "audit": audit}
    run.storage.write("active-report.json.gz", result)
    return result


def run_once(config, run_id):
    check_config(config)
    if (Path(config.managed_root) / run_id).exists():
        raise Rejected("Use a new run id; prototype does not resume or overwrite")
    agent = MockAgent()
    run = Run(config, run_id, agent)
    try:
        service = ActiveService(run.source, config, run.state, run.persist)
        service.executor = run.executor
        run.service = service
        run.executor.service = service
        run.preflight()
        return active_loop(run, agent)
    finally:
        run.close()


def audit_saved(config, run_id):
    check_config(config)
    if not run_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in run_id):
        raise Rejected("Invalid run name")
    path = Path(config.managed_root) / run_id / "checkpoint.json.gz"
    with gzip.open(path, "rt") as stream:
        state = json.load(stream)
    if state.get("checkpoint_digest") != digest({k: v for k, v in state.items() if k != "checkpoint_digest"}):
        raise Rejected("Checkpoint digest mismatch")
    if state["binding"].get("active_research", {}).get("code") != track_hash():
        raise Rejected("Active runner code changed; retain the exact version")
    return replay(config, state)


def main():
    parser = argparse.ArgumentParser(description="Single-race offline active research prototype")
    parser.add_argument("--config", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("command", choices=["run", "replay-audit"])
    args = parser.parse_args()
    config = Config.load(args.config)
    try:
        result = run_once(config, args.run_id) if args.command == "run" else audit_saved(config, args.run_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (Rejected, OSError, ValueError) as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}))
        return 2
    return 0

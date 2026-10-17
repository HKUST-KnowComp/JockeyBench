import json
import math
import os
import time
from pathlib import Path
from jockeybench.agents import provider
from jockeybench.errors import Rejected
from jockeybench.canonical import digest, packed
from jockeybench.scoring import validate
from jockeybench.research.runner import PROJECT, query_arguments

MAX_QUERIES = 6
MAX_TURNS = 8
WALL_SECONDS = 600

SYSTEM_PROMPT = """You are a decision-time active research agent for exactly one horse race.
You initially receive only a basic race card. Each turn MUST call exactly one offered native function.
Use form for legal pre-race historical performance, connections for pre-race jockey/trainer statistics,
or card for the full permitted card (including facts/training omitted from the initial basic card).
All evidence is as-of the frozen pre-race cutoff. Never request future results or another race.
Queries cost score-yen: 0.01 per returned evidence cell and 0.5 per data call; identical repeat
reads may waive evidence fees but still cost a call and consume a query action. Repeated queries
do not unlock newer information. Stop when extra evidence is not worth its cost.
Submit a complete ranking of all runner numbers and a probability for every runner; probabilities
must be finite, between 0 and 1 and sum to 1. The score is 100*ln(n*(0.98*p_winner+0.02/n))
minus service fees; there is no hit bonus. You may submit at any turn. At most 6 query actions
and 8 total model turns are allowed. If only submit is offered you MUST submit now.
Do not write code, train a model, generate strategy.py, or reflect across days.
Return the native function call only, not prose or a batch of calls."""


def redact(value):
    secret = os.environ.get("DEEPINFRA_API_KEY", "")
    if isinstance(value, str):
        return value.replace(secret, "<redacted>") if secret else value
    if isinstance(value, list):
        return [redact(x) for x in value]
    if isinstance(value, dict):
        return {redact(k): redact(v) for k, v in value.items()}
    return value


def credential(settings):
    # The public runtime reads only the explicitly configured environment key.
    provider.require_credential(settings)


def load_settings(path):
    raw = json.loads(Path(path).read_text())
    allowed = {"model", "base_url", "api_key_env", "timeout_seconds", "temperature", "seed",
               "reasoning_effort", "output_token_field", "input_usd_per_million",
               "output_usd_per_million", "max_estimated_cost_usd", "retry_attempts",
               "retry_base_seconds", "retry_max_seconds", "reservation_output_tokens"}
    if set(raw) - allowed or raw.get("api_key_env") != "DEEPINFRA_API_KEY":
        raise Rejected("Provider config must contain only non-secret DeepInfra settings")
    if raw.get("base_url") != "https://api.deepinfra.com/v1/openai":
        raise Rejected("This authorized smoke uses the existing DeepInfra endpoint only")
    if raw.get("retry_attempts") != 1 or raw.get("timeout_seconds", 0) > 60:
        raise Rejected("Require exactly one transport attempt (zero retries), timeout <=60s")
    if raw.get("output_token_field") != "max_tokens":
        raise Rejected("Use the existing max_tokens transport option")
    provider.require_prices(raw)
    return raw


def action_tools(numbers, submit_only=False):
    def tool(name, description, properties, required):
        return {"type": "function", "function": {"name": name, "description": description,
                "parameters": {"type": "object", "properties": properties,
                               "required": required, "additionalProperties": False}}}
    submit = tool("submit", "Seal final full ranking and probabilities for the current race.",
                  {"ranking": {"type": "array", "items": {"type": "string", "enum": numbers},
                               "minItems": len(numbers), "maxItems": len(numbers)},
                   "probabilities": {"type": "object", "properties": {
                       n: {"type": "number", "minimum": 0, "maximum": 1} for n in numbers},
                       "required": numbers, "additionalProperties": False}},
                  ["ranking", "probabilities"])
    if submit_only:
        return [submit]
    return [tool("form", "Paid pre-race recent historical form for this race's runners.",
                 {"k": {"type": "integer", "minimum": 1, "maximum": 5}}, ["k"]),
            tool("connections", "Paid as-of jockey/trainer counts for current runners.", {}, []),
            tool("card", "Paid full permitted current card, including training and other facts omitted from basic card.", {}, []),
            submit]


def parse_action(message, numbers, race, submit_only=False):
    calls = message.get("tool_calls") or []
    if len(calls) != 1:
        raise Rejected("Expected exactly one native action")
    call = calls[0]["function"]
    name = call["name"]
    try:
        args = json.loads(call["arguments"], parse_constant=lambda x: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError):
        raise Rejected("Action arguments must be finite JSON") from None
    if not isinstance(args, dict):
        raise Rejected("Action arguments must be an object")
    if name == "submit":
        if set(args) != {"ranking", "probabilities"}:
            raise Rejected("submit requires exactly ranking and probabilities")
        return {"type": "submit", "prediction": validate(args, numbers)}
    if submit_only:
        raise Rejected("Only submit is available")
    action = {"type": "query", "tool": name, "arguments": args}
    query_arguments(action, race)
    if name == "form" and set(args) != {"k"}:
        raise Rejected("form requires k")
    return action


def cost_record(result, request_id):
    usage = result.get("usage") or {}
    def token(key):
        v = usage.get(key)
        return v if type(v) is int and 0 <= v <= 10**9 else None
    return {"request_id": request_id, "prompt_tokens": token("prompt_tokens"),
            "completion_tokens": token("completion_tokens")}


class LLMActionAgent:
    def __init__(self, settings, deadline):
        self.settings, self.deadline = dict(settings), deadline
        self.identity = {"kind": "active-research-native-llm", "provider": "DeepInfra",
                         "model": settings["model"], "version": 1}

    def call(self, service, observation, tools, phase_key, forced=None, system=SYSTEM_PROMPT):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise Rejected("Wall-clock deadline reached")
        body = {"model": self.settings["model"],
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": json.dumps(observation, ensure_ascii=False)}],
                "tools": tools, "tool_choice": {"type": "function", "function": {"name": forced}} if forced else "required",
                "parallel_tool_calls": False, "max_tokens": 4096,
                "temperature": self.settings.get("temperature", 0)}
        for k in ("seed", "reasoning_effort"):
            if k in self.settings:
                body[k] = self.settings[k]
        if redact(body) != body:
            raise Rejected("Credential contamination blocked before request")
        request_id = digest([service.state["run_id"], phase_key, body])
        settings = dict(self.settings, timeout_seconds=min(60, max(0.1, remaining)))
        service.state.setdefault("llm_trajectory", []).append({
            "phase": phase_key, "request_id": request_id, "request": body})
        service.persist()
        result, attempt = provider.request(service, settings, body, request_id, phase_key)
        # Drop provider-internal reasoning; retain public native actions and content.
        raw = result["choices"][0]["message"]
        message = redact({k: raw[k] for k in ("role", "content", "tool_calls") if k in raw})
        record = cost_record(result, request_id)
        record.update(message=message, response_model=redact(result.get("model")),
                      finish_reason=result["choices"][0].get("finish_reason"),
                      elapsed_seconds=attempt["elapsed_seconds"])
        amount = (result.get("usage") or {}).get("estimated_cost")
        if type(amount) in (int, float) and math.isfinite(amount) and amount >= 0:
            record.update(cost_usd=amount, cost_basis="provider_reported_estimate")
        elif record["prompt_tokens"] is not None and record["completion_tokens"] is not None:
            amount = (record["prompt_tokens"] * settings["input_usd_per_million"]
                      + record["completion_tokens"] * settings["output_usd_per_million"]) / 1e6
            record.update(cost_usd=amount, cost_basis="configured_uncached_rate_estimate")
        else:
            record.update(cost_usd=None, cost_basis="unavailable")
        service.state.setdefault("model_outputs", []).append(record)
        service.state["pending"] = None
        service.state["llm_trajectory"][-1]["response"] = record
        service.persist()
        return message


def submit_only_reasons(query_count, turn):
    return (["query_cap"] if query_count >= MAX_QUERIES else []) + (["model_turn_cap"] if turn >= MAX_TURNS else [])


def model_cost(state):
    rows = state.get("model_outputs", [])
    attempts = state.get("provider_attempts", [])
    unknown = sum(x.get("cost_usd") is None for x in rows) + max(0, len(attempts) - len(rows))
    return {"observed_or_estimated_usd": sum(x.get("cost_usd") or 0 for x in rows),
            "unknown_attempts": unknown, "basis": sorted({x.get("cost_basis", "unavailable") for x in rows})}

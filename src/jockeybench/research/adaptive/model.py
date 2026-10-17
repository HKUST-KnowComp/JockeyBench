"""Uses the unchanged jrdb_lh.provider transport; bounded timing is track-local."""
import json
import math
import time
from pathlib import Path
from jockeybench.agents import provider
from jockeybench.errors import Rejected
from jockeybench.canonical import digest
from jockeybench.research.llm.agent import LLMActionAgent,cost_record,redact,credential,model_cost

PROMPT = "You are a trajectory-conditioned active research agent in a horse-racing benchmark.\nUse exactly one offered native function per turn. Never return prose instead of the function.\nIn prediction mode, the initial evidence is ONLY the current basic race card and a compact policy memory.\nAutonomously choose the entrant number and information domain to inspect, then reason again and continue or submit.\nThere is no query-count research budget and no prescribed tool order or required coverage of every horse/domain.\nPolicy priorities are descriptive beliefs, never compulsory sampling weights. Decide using evidence value and costs.\nAvailable domains: recent_history, training, condition_stats, track_context.\nprovider_signals and reference_odds are unavailable under the fixed objective-v5 permissions; do not invent them.\nOnly current entrant numbers are legal horse targets. Historical evidence is restricted by the original pre-race clock.\nRecent history and condition stats each use the native latest-five-start horse history, so repeating is not deeper history.\nCondition stats are bounded-sample descriptive counts, not fitted models or lifetime performance.\nTraining is fetched/charged by the original whole-race backend then narrowed to the selected horse.\nTrack context is fetched/charged as the original whole card then narrowed to race facts.\nAll information queries pass through the original ledger: 0.01 score-yen per charged evidence cell, 0.5 per data call.\nIdentical backend responses can waive evidence charges but still incur a call fee. Receipts report actual original fees.\nNull/missing means unknown, not zero. Opaque categorical codes have no assumed codebook.\nSubmit a complete ranking and finite probabilities for every entrant summing to 1 when more evidence is not worth its cost.\nScoring is 100*ln(n*(0.98*p_winner+0.02/n)) minus original service fees; there is no hit bonus.\nEngineering safety ceilings: 40 total model turns, 30 minutes per race and a provider spend threshold.\nHitting a ceiling stops the run without submitting on your behalf. Do not treat safety stops as voluntary decisions.\nIn reflection mode, only the previous race's legally released result, sealed prediction, costs, semantic trajectory and old policy are supplied.\nReturn update_policy with a short reflection and a compact new policy. Do not claim causal learning gains from one outcome.\nDo not copy full transcripts/evidence into memory. Do not refer to future races or assume access to their data.\nNo code generation, strategy.py, fitting mathematical models, or tool calls outside the offered schema.\n"

def load_settings(path):
    raw=json.loads(Path(path).read_text())
    allowed={"model","base_url","api_key_env","timeout_seconds","temperature","seed","reasoning_effort",
             "output_token_field","input_usd_per_million","output_usd_per_million",
             "max_estimated_cost_usd","retry_attempts","retry_base_seconds","retry_max_seconds","reservation_output_tokens"}
    if set(raw)-allowed or raw.get("api_key_env")!="DEEPINFRA_API_KEY":
        raise Rejected("Non-secret DeepInfra config required")
    if raw.get("base_url")!="https://api.deepinfra.com/v1/openai" or raw.get("model") not in {"XiaomiMiMo/MiMo-V2.5-Pro","deepseek-ai/DeepSeek-V4-Flash-0731"}:
        raise Rejected("Unexpected provider/model")
    if raw.get("retry_attempts") not in (1,2) or not 0<raw.get("timeout_seconds",0)<=180:
        raise Rejected("At most one retry and 180 seconds per attempt")
    if raw.get("output_token_field")!="max_tokens": raise Rejected("Unsupported output cap")
    if not 0<raw.get("max_estimated_cost_usd",0)<=0.75: raise Rejected("Provider safety ceiling required")
    provider.require_prices(raw)
    return raw

class AdaptiveAgent(LLMActionAgent):
    # Native request serialization/cost logging retained from the existing adapter.
    def call(self, service, observation, tools, phase_key, forced=None, system=PROMPT):
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
        settings = dict(self.settings, timeout_seconds=min(self.settings["timeout_seconds"], max(0.1, remaining / self.settings["retry_attempts"])))
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


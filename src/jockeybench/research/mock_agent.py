"""Deterministic decision-time agent. Runs in the existing native sandbox."""
import math


def decide(observation):
    """One action per invocation; inspect only the supplied public observation."""
    evidence = observation["evidence"]
    if "form" not in evidence and not observation["must_submit"]:
        return {"type": "query", "tool": "form", "arguments": {"k": 3}}
    if "connections" not in evidence and not observation["must_submit"]:
        return {"type": "query", "tool": "connections", "arguments": {}}
    form = {r["number"]: r["starts"] for r in evidence.get("form", {}).get("runners", [])}
    connections = {r["number"]: r["values"] for r in evidence.get("connections", {}).get("runners", [])}
    scores = {}
    for runner in observation["basic_card"]["runners"]:
        number = runner["number"]
        finishes = [s["values"].get("finish") for s in form.get(number, [])]
        finishes = [x for x in finishes if isinstance(x, (int, float)) and x > 0]
        score = -0.3 * (sum(finishes) / len(finishes) if finishes else 8.0)
        counts = connections.get(number, {})
        for role in ("jockey", "trainer"):
            score += (counts.get(role + "_wins", 0) + 1) / (counts.get(role + "_starts", 0) + 10)
        scores[number] = score
    maximum = max(scores.values())
    weights = {n: math.exp(v - maximum) for n, v in scores.items()}
    total = sum(weights.values())
    probabilities = {n: value / total for n, value in weights.items()}
    return {"type": "submit", "prediction": {
        "ranking": sorted(scores, key=lambda n: (-scores[n], n)),
        "probabilities": probabilities}}

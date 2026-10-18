"""
check_memory.py -- Stretch Goal A: conversation memory.

  1. Follow-ups resolve   "How many active projects does Acme Corp have?" then
                          "What about their total contract value?" with the
                          history from turn 1 -> filtered to Acme Corp
  2. Control              the same follow-up with NO history, to show the
                          history is what resolved "their"
  3. Cost impact          turn 1 vs turn 2 tokens, and the exact memory
                          overhead: turn 2 with history minus turn 2 without,
                          on the two calls that receive history

  local (default)   calls lambda_handler.handler directly
  --deployed        runs the real `aws lambda invoke` CLI against the function

Responses are saved to deliverables/stretch_a/<local|deployed>/.

USAGE: python check_memory.py [--deployed]
"""
import json
import os
import subprocess
import sys
import tempfile

FUNCTION = "multi-agent-orchestrator-aaronhsu"
ROOT = os.path.dirname(os.path.abspath(__file__))
DEPLOYED = "--deployed" in sys.argv
MEMORY_CALLS = ["classification", "sql_generation"]  # the calls history is replayed into

TURN_1 = "How many active projects does Acme Corp have?"
TURN_2 = "What about their total contract value?"


def invoke(body: dict) -> dict:
    """One request, locally or through `aws lambda invoke`; returns the response body."""
    if not DEPLOYED:
        from lambda_handler import handler
        return json.loads(handler({"body": json.dumps(body)}, None)["body"])
    with tempfile.TemporaryDirectory() as tmp:
        payload, out = os.path.join(tmp, "payload.json"), os.path.join(tmp, "response.json")
        with open(payload, "w") as f:
            json.dump({"body": json.dumps(body)}, f)
        subprocess.run(["aws", "lambda", "invoke", "--function-name", FUNCTION,
                        "--payload", f"file://{payload}", "--cli-binary-format", "raw-in-base64-out",
                        "--cli-read-timeout", "150", out], check=True, capture_output=True)
        with open(out) as f:
            return json.loads(json.load(f)["body"])


def save(folder: str, name: str, request: dict, response: dict):
    path = os.path.join(ROOT, "deliverables", folder, "deployed" if DEPLOYED else "local")
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, f"{name}.json"), "w") as f:
        json.dump({"request": request, "response": response}, f, indent=2)


def input_tokens(response: dict, components=None) -> int:
    by = response.get("token_usage", {}).get("by_component", {})
    return sum(s["input_tokens"] for n, s in by.items() if components is None or n in components)


def one_line(text: str, n: int = 200) -> str:
    return " ".join(text.split())[:n]


if __name__ == "__main__":
    mode = "DEPLOYED (aws lambda invoke)" if DEPLOYED else "LOCAL (lambda_handler)"
    print(f"Mode: {mode}\n")

    req1 = {"question": TURN_1}
    r1 = invoke(req1)
    save("stretch_a", "turn_1", req1, r1)
    print(f"Turn 1: {TURN_1}\n  answer: {one_line(r1['answer'])}\n  history returned: {len(r1['history'])} turn(s)\n")

    req2 = {"question": TURN_2, "history": r1["history"]}
    r2 = invoke(req2)
    save("stretch_a", "turn_2_with_history", req2, r2)
    print(f"Turn 2 (history from turn 1): {TURN_2}\n  answer: {one_line(r2['answer'])}\n")

    req2c = {"question": TURN_2}
    r2c = invoke(req2c)
    save("stretch_a", "turn_2_no_history_control", req2c, r2c)
    print(f"Control (same question, no history): {TURN_2}\n  answer: {one_line(r2c['answer'])}\n")

    checks = {
        "turn 2 is filtered to Acme Corp": "acme" in r2["answer"].lower(),
        "turn 2 gives Acme's contract value ($250,000)": "250" in r2["answer"],
        "turn 2 is not a total across all clients ($1,655,000)": "1,655" not in r2["answer"] and "1655" not in r2["answer"],
        "without history the same question does NOT resolve to Acme": "acme" not in r2c["answer"].lower(),
        "returned history grows to 2 turns": len(r2["history"]) == 2,
    }

    t1_total, t2_total = input_tokens(r1), input_tokens(r2)
    t1_mem, t2_mem, ctrl_mem = (input_tokens(r, MEMORY_CALLS) for r in (r1, r2, r2c))
    overhead = t2_mem - ctrl_mem
    print("Cost impact (input tokens):")
    print(f"  {'':<34}{'turn 1':>8}{'turn 2':>8}")
    print(f"  {'all calls':<34}{t1_total:>8}{t2_total:>8}")
    print(f"  {'classification + sql_generation':<34}{t1_mem:>8}{t2_mem:>8}")
    print(f"  memory overhead on turn 2 = {t2_mem} (with history) - {ctrl_mem} (same question, no history)"
          f" = +{overhead} input tokens ({overhead / ctrl_mem:.0%} on those two calls)\n")
    checks["memory adds input tokens on turn 2"] = overhead > 0

    for name, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\nMemory check ({'deployed' if DEPLOYED else 'local'}): {'PASS' if all(checks.values()) else 'FAIL'}")
    sys.exit(0 if all(checks.values()) else 1)

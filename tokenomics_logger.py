"""
tokenomics_logger.py -- structured logging, per-call token cost, and
full-chain tracing for the orchestrator.

Every Claude call goes through bedrock_client.invoke_claude, which calls
log_claude_call() with a component label -- so there is no Claude call that
skips logging. Components:

  classification       router: which tool(s) the question needs
  sql_generation       SQL agent: question -> SQL
  sql_interpretation   SQL agent: rows -> answer
  retrieval_answer     retrieval agent: excerpts -> cited answer
  synthesis            orchestrator: agent results -> final answer

run_traced_query() opens a trace for one question; every call made while it
runs (including from the parallel agent branches -- LangGraph copies context
into its worker threads) is tagged with that trace_id and summed into a
per-component cost summary at the end.

Log lines go to stdout (CloudWatch on Lambda) and to /tmp/agent_decisions.jsonl
(the only writable path on Lambda).
"""
import contextvars
import json
import uuid
from datetime import datetime

LOG_PATH = "/tmp/agent_decisions.jsonl"

# USD per 1M tokens for Claude Sonnet 4.6 on Amazon Bedrock (on-demand), from
# https://aws.amazon.com/bedrock/pricing/. Every cost figure in the system
# comes from these two numbers. Batch ($1.50/$7.50) and prompt caching rates
# don't apply: every call here is on-demand with no cache.
PRICE_PER_MTOK = {"input": 3.00, "output": 15.00}

# One trace per question: its id and the list of Claude calls made in it.
_trace = contextvars.ContextVar("trace", default=None)


def cost_usd(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * PRICE_PER_MTOK["input"] + output_tokens * PRICE_PER_MTOK["output"]) / 1_000_000


def _write(entry: dict):
    try:
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass  # logging must never break a query


def log_decision(stage: str, question: str, details: dict):
    trace = _trace.get()
    entry = {
        "timestamp": datetime.now().isoformat(),
        "trace_id": trace["trace_id"] if trace else None,
        "stage": stage,
        "question": question,
        "details": details,
    }
    print(f"[LOG] {stage}: {json.dumps(details, default=str)}")
    _write(entry)


def log_claude_call(component: str, input_tokens: int, output_tokens: int):
    """Called by invoke_claude after every Claude call."""
    trace = _trace.get()
    call = {
        "component": component,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost_usd(input_tokens, output_tokens), 6),
    }
    if trace is not None:
        trace["calls"].append(call)  # list.append is thread-safe
    trace_id = trace["trace_id"] if trace else "-"
    print(f"[TOKENS] trace={trace_id} {component:<19} in={input_tokens:>5} out={output_tokens:>4} "
          f"cost=${call['cost_usd']:.6f}")
    _write({"timestamp": datetime.now().isoformat(), "trace_id": trace["trace_id"] if trace else None,
            "stage": "claude_call", **call})


def summarise_calls(calls: list[dict]) -> dict:
    """Per-component and total tokens and cost for a list of logged calls."""
    by_component = {}
    for c in calls:
        s = by_component.setdefault(c["component"], {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
        s["calls"] += 1
        s["input_tokens"] += c["input_tokens"]
        s["output_tokens"] += c["output_tokens"]
        s["cost_usd"] += c["cost_usd"]
    for s in by_component.values():
        s["cost_usd"] = round(s["cost_usd"], 6)
    total_in = sum(c["input_tokens"] for c in calls)
    total_out = sum(c["output_tokens"] for c in calls)
    return {
        "by_component": by_component,
        "total": {"calls": len(calls), "input_tokens": total_in, "output_tokens": total_out,
                  "cost_usd": round(sum(c["cost_usd"] for c in calls), 6)},
    }


def run_traced_query(orchestrator, question: str) -> dict:
    trace = {"trace_id": str(uuid.uuid4())[:8], "calls": []}
    token = _trace.set(trace)
    try:
        log_decision("query_start", question, {"trace_id": trace["trace_id"]})

        result = orchestrator.invoke({
            "question": question, "route": None, "sql_result": None,
            "retrieval_result": None, "final_answer": None, "token_usage": None
        })

        # Built from the calls actually logged, not from what nodes chose to
        # report -- so a call can't be missing from the cost.
        cost = summarise_calls(trace["calls"])
        result["cost"] = cost

        # sql_result's "validation" is nested under "metadata", not top-level.
        log_decision("query_complete", question, {
            "trace_id": trace["trace_id"],
            "route": result["route"],
            "sql_validation": result["sql_result"].get("metadata", {}).get("validation") if result.get("sql_result") else None,
            "retrieval_chunk_count": result["retrieval_result"].get("metadata", {}).get("chunk_count") if result.get("retrieval_result") else None,
            "cost": cost,
        })
        return result
    finally:
        _trace.reset(token)

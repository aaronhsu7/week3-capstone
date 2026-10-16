"""
check_tokenomics.py -- Bronze Step 6 (first requirement): every Claude call
is logged with input tokens, output tokens, and USD cost.

Runs one multi-tool ("both") question through the same traced path the
Lambda uses, prints the per-call [TOKENS] log lines as they happen, then
checks that all five components were logged with non-zero tokens and cost.

(The 10-query cost summary is a separate step.)

USAGE: python check_tokenomics.py
"""
import sys
from orchestrator import orchestrator
from tokenomics_logger import run_traced_query, PRICE_PER_MTOK

QUESTION = "What pitfalls should we watch for in a cloud migration, and how many Active projects do we have?"
EXPECTED = ["classification", "sql_generation", "sql_interpretation", "retrieval_answer", "synthesis"]

if __name__ == "__main__":
    print(f"Pricing: ${PRICE_PER_MTOK['input']:.2f} / ${PRICE_PER_MTOK['output']:.2f} per 1M input/output tokens\n")
    result = run_traced_query(orchestrator, QUESTION)
    by_component = result["cost"]["by_component"]
    total = result["cost"]["total"]

    print(f"\nroute: {result['route']}")
    print(f"\n{'component':<20}{'input':>8}{'output':>8}{'cost (USD)':>13}")
    for name, s in by_component.items():
        print(f"{name:<20}{s['input_tokens']:>8}{s['output_tokens']:>8}{s['cost_usd']:>13.6f}")
    print(f"{'TOTAL':<20}{total['input_tokens']:>8}{total['output_tokens']:>8}{total['cost_usd']:>13.6f}\n")

    all_ok = result["route"] == "both"
    print(f"  [{'PASS' if all_ok else 'FAIL'}] route is 'both' (so every component should run)")
    for name in EXPECTED:
        s = by_component.get(name)
        ok = bool(s) and s["input_tokens"] > 0 and s["output_tokens"] > 0 and s["cost_usd"] > 0
        all_ok &= ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {name} logged with input, output, and cost")
    unlabeled = [n for n in by_component if n not in EXPECTED]
    all_ok &= not unlabeled
    print(f"  [{'PASS' if not unlabeled else 'FAIL'}] no unlabeled Claude calls {unlabeled or ''}")

    print(f"\nTokenomics logging check: {'PASS' if all_ok else 'FAIL'}")
    sys.exit(0 if all_ok else 1)

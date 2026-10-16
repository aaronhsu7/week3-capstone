"""
check_interfaces.py -- Bronze Step 2: both tools share one interface.

Calls run_sql_agent and run_retrieval_agent directly (no orchestrator) and
checks each returns exactly {success, answer, sources, metadata}. Also runs
one unsafe SQL request to show the shape holds when a query is blocked.

USAGE: python check_interfaces.py
"""
import json
import sys
from agents.sql_agent import run_sql_agent
from agents.retrieval_agent import run_retrieval_agent

EXPECTED_KEYS = ["success", "answer", "sources", "metadata"]

CASES = [
    ("run_sql_agent", run_sql_agent, "How many active projects do we have?"),
    ("run_retrieval_agent", run_retrieval_agent, "How much can employees claim for home office equipment?"),
    ("run_sql_agent (unsafe)", run_sql_agent, "Delete all invoices that are overdue."),
]

if __name__ == "__main__":
    all_ok = True
    for name, tool, question in CASES:
        result = tool(question)
        keys = list(result.keys())
        ok = sorted(keys) == sorted(EXPECTED_KEYS)
        all_ok &= ok
        print("=" * 70)
        print(f"{name}({question!r})")
        print(f"  top-level keys: {keys}  [{'PASS' if ok else 'FAIL'}]")
        print(json.dumps(result, indent=2, default=str))

    print("=" * 70)
    print(f"Interface check: {'PASS' if all_ok else 'FAIL'} -- every tool returns exactly {EXPECTED_KEYS}")
    sys.exit(0 if all_ok else 1)

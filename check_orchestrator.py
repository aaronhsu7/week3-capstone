"""
check_orchestrator.py -- Bronze Step 4: the LangGraph orchestrator routes
correctly and synthesis draws on every result present.

For each question it streams the graph node by node, recording which nodes
actually ran and the exact state the synthesiser received, then checks:

  SQL-only / retrieval-only: route is correct and only the needed tool ran
  Both:                      route is "both", sql_result AND retrieval_result
                             are present when synthesis runs, and the answer
                             attributes facts to both agents

USAGE: python check_orchestrator.py
"""
import json
import sys
from orchestrator import orchestrator

CASES = [
    ("sql", "How many active projects do we have?"),
    ("retrieval", "How much can employees claim for home office equipment?"),
    ("both", "What pitfalls should we watch for in a cloud migration, and how many Active projects do we have?"),
]

TOOL_NODES = {"sql": ["sql_agent"], "retrieval": ["retrieval_agent"], "both": ["sql_agent", "retrieval_agent"]}


def summarise_result(result):
    if result is None:
        return None
    return {
        "success": result["success"],
        "answer": result["answer"][:150] + ("..." if len(result["answer"]) > 150 else ""),
        "sources": result["sources"],
        "validation": result["metadata"].get("validation"),
    }


def run(question: str):
    """Stream the graph; return (nodes that ran, state entering synthesis, final state)."""
    initial = {"question": question, "route": None, "sql_result": None,
               "retrieval_result": None, "final_answer": None, "token_usage": None}
    nodes_ran, state, state_at_synthesis = [], dict(initial), None
    for step in orchestrator.stream(initial, stream_mode="updates"):
        for node, update in step.items():
            if node == "synthesiser":
                state_at_synthesis = dict(state)
            nodes_ran.append(node)
            state.update(update)
    return nodes_ran, state_at_synthesis, state


if __name__ == "__main__":
    all_ok = True
    for expected_route, question in CASES:
        nodes_ran, at_synthesis, final = run(question)
        tools_ran = [n for n in nodes_ran if n in ("sql_agent", "retrieval_agent")]

        checks = {
            f"route == {expected_route!r}": final["route"] == expected_route,
            f"only {TOOL_NODES[expected_route]} ran": sorted(tools_ran) == sorted(TOOL_NODES[expected_route]),
        }
        if expected_route == "both":
            checks["sql_result present at synthesis"] = at_synthesis["sql_result"] is not None
            checks["retrieval_result present at synthesis"] = at_synthesis["retrieval_result"] is not None
            checks["answer attributes SQL Agent"] = "SQL Agent" in final["final_answer"]
            checks["answer attributes Retrieval Agent"] = "Retrieval Agent" in final["final_answer"]

        print("=" * 78)
        print(f"Q: {question}")
        print(f"nodes ran: {' -> '.join(nodes_ran)}")
        print("state when synthesis ran:")
        print(json.dumps({
            "route": at_synthesis["route"],
            "sql_result": summarise_result(at_synthesis["sql_result"]),
            "retrieval_result": summarise_result(at_synthesis["retrieval_result"]),
        }, indent=2))
        print(f"final_answer:\n{final['final_answer']}\n")
        for name, ok in checks.items():
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
            all_ok &= ok

    print("=" * 78)
    print(f"Orchestrator check: {'PASS' if all_ok else 'FAIL'}")
    sys.exit(0 if all_ok else 1)

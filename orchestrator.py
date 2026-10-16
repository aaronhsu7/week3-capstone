"""
orchestrator.py -- routes a question to the SQL agent, the retrieval agent,
or both, then synthesizes a single cited answer.

Tools and every system prompt come from tool_registry.py: the router prompt
is built from the tools' descriptions, and the worker nodes look their tool
up by name rather than importing the agents directly.
"""
import json
import re
from langgraph.graph import StateGraph, END
from typing import TypedDict, Optional
from bedrock_client import invoke_claude
from tool_registry import ORCHESTRATOR_PROMPTS, get_tool
from dotenv import load_dotenv
load_dotenv()

VALID_ROUTES = {"sql", "retrieval", "both"}


class OrchestratorState(TypedDict):
    question: str
    route: Optional[str]
    sql_result: Optional[dict]
    retrieval_result: Optional[dict]
    final_answer: Optional[str]
    token_usage: Optional[dict]


# Fallback only: used if the Claude router call fails or returns something
# unparseable, so a routing hiccup degrades the answer rather than crashing.
SQL_KEYWORDS = [
    "how many", "count", "total", "average", "sum", "revenue",
    "how much", "list all", "which clients", "projects", "invoices",
    "consultants", "compare", "trend", "budget"
]
DOC_KEYWORDS = [
    "policy", "process", "procedure", "methodology", "what is",
    "explain", "describe", "guidelines", "approach", "onboarding"
]


def keyword_route(question: str) -> str:
    question = question.lower()
    is_sql = any(k in question for k in SQL_KEYWORDS)
    is_doc = any(k in question for k in DOC_KEYWORDS)
    if is_sql and is_doc:
        return "both"
    return "sql" if is_sql else "retrieval"


# Every node returns only the keys it changes (a partial update), never the
# whole state. LangGraph merges the update in; on a "both" route the two
# agent nodes run in parallel, and two full-state returns in the same step
# would collide on keys like "question".

def route_question(state: OrchestratorState) -> dict:
    """Classification node: asks Claude which tool(s) the question needs."""
    usage = {}
    try:
        result = invoke_claude(
            f"Question: {state['question']}", max_tokens=50, system=ORCHESTRATOR_PROMPTS["router"],
            component="classification",
        )
        usage = {"classification": {"input": result["input_tokens"], "output": result["output_tokens"]}}
        match = re.search(r"\{.*\}", result["text"], re.DOTALL)
        route = json.loads(match.group(0))["route"] if match else None
    except Exception:
        route = None

    if route not in VALID_ROUTES:
        route = keyword_route(state["question"])

    return {"route": route, "token_usage": {**(state.get("token_usage") or {}), **usage}}


def select_tools(state: OrchestratorState) -> list[str]:
    """Conditional edge: the route decides which agent node(s) run next."""
    return {"sql": ["sql_agent"], "retrieval": ["retrieval_agent"],
            "both": ["sql_agent", "retrieval_agent"]}[state["route"]]


def call_sql_agent(state: OrchestratorState) -> dict:
    """SQL tool node: only reached when route is "sql" or "both"."""
    return {"sql_result": get_tool("sql_agent")(state["question"])}


def call_retrieval_agent(state: OrchestratorState) -> dict:
    """Retrieval tool node: only reached when route is "retrieval" or "both"."""
    return {"retrieval_result": get_tool("retrieval_agent")(state["question"])}


def synthesise(state: OrchestratorState) -> dict:
    """Synthesis node: combines whichever agent results are present."""
    # Every Claude call made so far, keyed by component: classification from
    # the router, plus whatever each agent reported in its metadata.
    token_usage = dict(state.get("token_usage") or {})
    sections = []
    for label, key in [("SQL Agent", "sql_result"), ("Retrieval Agent", "retrieval_result")]:
        r = state.get(key)
        if not r:
            continue
        token_usage.update(r.get("metadata", {}).get("token_usage", {}))
        status = "OK" if r["success"] else "FAILED / BLOCKED / NOT FOUND"
        sections.append(f"{label} Result [{status}]:\n{r['answer']}\nSources: {r['sources']}")

    if not any(state.get(k) and state[k]["success"] for k in ["sql_result", "retrieval_result"]):
        # Nothing usable came back -- decline without spending a synthesis
        # call, and pass through why (e.g. the validator's block reason).
        reasons = " ".join(state[k]["answer"] for k in ["sql_result", "retrieval_result"] if state.get(k))
        return {
            "final_answer": f"I was unable to answer this question. {reasons}".strip(),
            "token_usage": token_usage,
        }

    result = invoke_claude(
        f"Question: {state['question']}\n\n" + "\n\n".join(sections),
        max_tokens=800,
        system=ORCHESTRATOR_PROMPTS["synthesis"],
        component="synthesis",
    )
    token_usage["synthesis"] = {"input": result["input_tokens"], "output": result["output_tokens"]}

    return {"final_answer": result["text"], "token_usage": token_usage}


def build_orchestrator():
    """
                       ┌─> sql_agent ───────┐
        router ──(route)┤                    ├─> synthesiser ─> END
                       └─> retrieval_agent ─┘
    "sql" / "retrieval" run one branch; "both" runs the two in parallel and
    synthesiser runs once, after both have finished.
    """
    graph = StateGraph(OrchestratorState)
    graph.add_node("router", route_question)
    graph.add_node("sql_agent", call_sql_agent)
    graph.add_node("retrieval_agent", call_retrieval_agent)
    graph.add_node("synthesiser", synthesise)

    graph.set_entry_point("router")
    graph.add_conditional_edges("router", select_tools, ["sql_agent", "retrieval_agent"])
    graph.add_edge("sql_agent", "synthesiser")
    graph.add_edge("retrieval_agent", "synthesiser")
    graph.add_edge("synthesiser", END)

    return graph.compile()


orchestrator = build_orchestrator()

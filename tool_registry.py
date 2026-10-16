"""
tool_registry.py -- Bronze Step 3: the shared tool registry, and the single
source of truth for every system prompt in the system.

  TOOL_REGISTRY        one entry per tool: the function behind it, its input
                       schema, a description saying what it IS and is NOT for,
                       and the system prompt(s) that tool's Claude calls use.
  ORCHESTRATOR_PROMPTS the router's and synthesizer's system prompts. The
                       router prompt is built from the tool descriptions
                       above, so routing always reflects the registry.

Agents and the orchestrator read their prompts from here rather than
defining their own, so changing how any component behaves is a one-file edit.

Tool functions are stored as "module:function" strings and imported only
when called -- the agents import this module for their prompts, so importing
them back at the top would be circular.

USAGE: python tool_registry.py     # prints the registry (Step 3 evidence)
"""
import importlib
import json

# Schema documentation from the RDS lab, with the real (case-sensitive)
# values confirmed against the database in Setup.
SCHEMA_CONTEXT = """Tables:
- clients(id, name, industry, region, contract_value, created_date)
- projects(id, client_id -> clients.id, name, status, start_date, end_date, budget)
- consultants(id, name, grade, specialisation, project_id -> projects.id, billable_hours)
- invoices(id, project_id -> projects.id, amount, status, due_date, paid_date)

All monetary values are in USD. Dates are ISO 8601 (YYYY-MM-DD).
Text values are case-sensitive; use them exactly as written:
- projects.status: 'Active', 'Completed', 'On Hold'
- invoices.status: 'Paid', 'Pending', 'Overdue'
- clients.region: 'North', 'South', 'East', 'West'
- consultants.grade: 'Junior', 'Senior', 'Principal'"""

# The exact phrase the retrieval agent uses to decline; run_retrieval_agent
# checks for it to set success=False.
RETRIEVAL_DECLINE = "I cannot find this information in the provided documents."

TOOL_REGISTRY = {
    "sql_agent": {
        "function": "agents.sql_agent:run_sql_agent",
        "description": (
            "Use for questions answered by structured records in the firm's "
            "PostgreSQL database: clients (name, industry, region, contract "
            "value), projects (status Active/Completed/On Hold, start/end "
            "dates, budget), consultants (grade, specialisation, billable "
            "hours, project assignment), and invoices (amount, Paid/Pending/"
            "Overdue status, due and paid dates). Good for counts, totals, "
            "averages, lists, filters, rankings, and comparisons, e.g. 'How "
            "many active projects do we have?' or 'Which client has the "
            "highest contract value?'. "
            "Do NOT use for policies, procedures, methodologies, guidelines, "
            "or narrative content from case studies and reports; those live "
            "in documents, not database rows. Read-only: it will never "
            "create, update, or delete data, and any such request is blocked "
            "by the SQL validator."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The natural-language question to answer from the database.",
                }
            },
            "required": ["question"],
        },
        "system_prompts": {
            "sql_generation": (
                "You are the SQL Query Agent for a consulting firm. You translate "
                "a question into one PostgreSQL query against this schema:\n\n"
                f"{SCHEMA_CONTEXT}\n\n"
                "Rules:\n"
                "- Write exactly one SELECT statement. Never chain statements with ';'.\n"
                "- Use only the tables and columns listed above.\n"
                "- Use JOINs on the foreign keys shown when a question spans tables.\n"
                "- Add ORDER BY for rankings and LIMIT for 'top N' questions.\n"
                "- If the question asks to insert, update, delete, or otherwise change "
                "data or schema, output exactly: UNSAFE_REQUEST\n"
                "- The question may include parts meant for another agent (policies, "
                "playbooks, case studies, reports). Ignore those parts and write SQL for "
                "the part the database can answer.\n"
                "- Only if no part of the question can be answered from this schema, "
                "output exactly: CANNOT_ANSWER\n"
                "- Output only the SQL (or one of the two markers). No explanation, "
                "no markdown, no backticks."
            ),
            "sql_interpretation": (
                "You are the SQL Query Agent for a consulting firm. You are given a "
                "question, the SQL that was run, and the rows it returned. Answer the "
                "question in one to three sentences using only those rows.\n"
                "- State figures exactly as returned; format money as USD.\n"
                "- If no rows were returned, say no matching records were found.\n"
                "- Do not speculate or add information that is not in the rows.\n"
                "- The question may include parts meant for another agent (e.g. about "
                "policies or documents). Answer only the part the rows cover and do not "
                "mention the rest."
            ),
        },
    },
    "retrieval_agent": {
        "function": "agents.retrieval_agent:run_retrieval_agent",
        "description": (
            "Use for questions answered by the firm's written documents, "
            "searched semantically in OpenSearch: the Remote Work Policy "
            "(eligibility, home office stipend, availability, equipment, data "
            "security), the Cloud Data Migration Playbook (migration phases, "
            "approach, common pitfalls), the Q2 2026 Quarterly Engagement "
            "Report (milestones, risks, budget narrative for that "
            "engagement), and the Retail Supply Chain case study (grocery "
            "client's challenge, approach, results). Good for 'what is our "
            "policy on...', 'how do we approach...', 'what happened in...'. "
            "Answers cite the document they came from. "
            "Do NOT use for live numbers about clients, projects, consultants, "
            "or invoices, such as counts, totals, statuses, or lists of "
            "records; those come from the database and figures quoted inside "
            "a document may be out of date. Do NOT use for topics outside "
            "these four documents; it will report that it cannot find the "
            "information rather than guess."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The natural-language question to answer from the documents.",
                }
            },
            "required": ["question"],
        },
        "system_prompts": {
            "retrieval_answer": (
                "You are the Retrieval Agent for a consulting firm. You answer "
                "questions using only the numbered document excerpts provided in "
                "the user message.\n"
                "- Use only information stated in the excerpts. Never use outside knowledge.\n"
                "- Cite every claim with its excerpt number AND document name, exactly as "
                "shown in the excerpt header, e.g. [Source 1: remote-work-policy.pdf].\n"
                "- The question may include parts meant for another agent (e.g. live "
                "counts or totals from the database). Answer only the part the excerpts "
                "cover and do not mention the rest.\n"
                "- Only if the excerpts cover no part of the question, reply with exactly: "
                f"\"{RETRIEVAL_DECLINE}\"\n"
                "- Be concise: a short paragraph or a few bullet points."
            ),
        },
    },
}


ORCHESTRATOR_PROMPTS = {
    "router": (
        "You are the router for a consulting firm's multi-agent assistant. Decide "
        "which tool(s) a question needs. The tools are:\n\n"
        + "\n\n".join(f"{name}: {spec['description']}" for name, spec in TOOL_REGISTRY.items())
        + "\n\n"
        "Reply with a JSON object and nothing else: {\"route\": \"sql\"}, "
        "{\"route\": \"retrieval\"}, or {\"route\": \"both\"}.\n"
        "- \"sql\": only database records are needed.\n"
        "- \"retrieval\": only document content is needed.\n"
        "- \"both\": the question needs facts from the database AND from the documents.\n"
        "- Requests to change data go to \"sql\", where the validator blocks them.\n"
        "- If neither tool clearly covers the question, choose \"retrieval\"; it "
        "will decline honestly if the documents don't cover it."
    ),
    "synthesis": (
        "You are the synthesizer for a consulting firm's multi-agent assistant. "
        "You receive the question and the results from one or more agents. Write "
        "one clear, combined answer.\n"
        "- Use only the information in the agent results. Do not add facts.\n"
        "- Attribute every fact to its agent: (SQL Agent) for database results, "
        "(Retrieval Agent: <document name>) for document results.\n"
        "- If an agent reports it was blocked, failed, or could not find the "
        "information, say so plainly instead of filling the gap.\n"
        "- Take document names from the agent's own citations; never guess which "
        "document a fact came from.\n"
        "- Keep it concise: plain prose or short bullets, no headings or tables."
    ),
}


def get_system_prompt(tool: str, call: str) -> str:
    """System prompt for one Claude call made by a tool, e.g. ("sql_agent", "sql_generation")."""
    return TOOL_REGISTRY[tool]["system_prompts"][call]


def get_tool(name: str):
    """Return the callable registered under name (imported on first use)."""
    module_name, func_name = TOOL_REGISTRY[name]["function"].split(":")
    return getattr(importlib.import_module(module_name), func_name)


def describe_tools() -> str:
    """Name + description of every tool, formatted for a routing prompt."""
    return "\n\n".join(f"{name}: {spec['description']}" for name, spec in TOOL_REGISTRY.items())


def as_claude_tools() -> list[dict]:
    """The registry in the Messages API tool format (for invoke_claude_raw)."""
    return [
        {"name": name, "description": spec["description"], "input_schema": spec["input_schema"]}
        for name, spec in TOOL_REGISTRY.items()
    ]


if __name__ == "__main__":
    print(json.dumps(as_claude_tools(), indent=2))
    print()
    for name, spec in TOOL_REGISTRY.items():
        has_for = spec["description"].startswith("Use for")
        has_not_for = "Do NOT use" in spec["description"]
        status = "PASS" if has_for and has_not_for else "FAIL"
        print(f"[{status}] {name} -> {spec['function']}: "
              f"states what it's for: {has_for}, states what it's not for: {has_not_for}")
    print()
    print("System prompts:")
    for name, spec in TOOL_REGISTRY.items():
        for call in spec["system_prompts"]:
            print(f"  {name}.{call}")
    for call in ORCHESTRATOR_PROMPTS:
        print(f"  orchestrator.{call}")

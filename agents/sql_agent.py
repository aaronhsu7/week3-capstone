"""
agents/sql_agent.py -- validated SQL Query Agent, wrapped to the standard
{success, answer, sources, metadata} interface from the Shared Tool Registry
lab.

NOTE ON STRUCTURE: earlier in this program, run_sql_agent() reached into a
sibling repo via sys.path.append("../add-validation-layer-lab") to import
validated_sql_agent. That works for local development, where both repos'
directories physically exist side by side -- it does NOT work in a deployed
Lambda package, which only has access to what's actually bundled inside the
zip. Every piece of logic (generate_sql, validate_sql, execute_query_safely)
is inlined directly here instead, so this file is genuinely self-contained
and deployable on its own.
"""
import os
import re
import psycopg2
from dotenv import load_dotenv
from bedrock_client import invoke_claude
from tool_registry import get_system_prompt
from agents.sql_review import review_sql, issue_token, verify_token
from memory import format_history
load_dotenv()

# System prompts (including the schema context) live in tool_registry.py.

# Statements and clauses that write, change schema or permissions, or run
# arbitrary code. INTO covers SELECT ... INTO new_table, which creates a
# table despite starting with SELECT.
BLOCKED_KEYWORDS = ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "TRUNCATE",
                    "CREATE", "GRANT", "REVOKE", "EXEC", "EXECUTE", "MERGE",
                    "COPY", "CALL", "INTO", "VACUUM", "REINDEX", "LOCK"]

# Server-side functions that read files, sleep, or reach other servers --
# all reachable from an otherwise read-only SELECT.
BLOCKED_FUNCTIONS = ["PG_SLEEP", "PG_READ_FILE", "PG_READ_BINARY_FILE", "PG_LS_DIR",
                     "LO_IMPORT", "LO_EXPORT", "DBLINK", "PG_TERMINATE_BACKEND"]


def validate_sql(query: str) -> dict:
    """
    Runs every check below, in order, before any database call. Each check is
    independent; the first one that fails blocks the query and is reported
    as "check" so a block can be traced to the rule that caused it.

      1. keywords        whole-word match, so deleted_at is allowed but
                         DELETE is blocked wherever it appears -- after a
                         newline, a tab, or "(" as well as a space
      2. functions       dangerous built-in functions
      3. comments        "--" and "/* */" can hide the rest of a query from a
                         reviewer, and generated SQL never needs them
      4. select_only     must be a read: starts with SELECT, or WITH for a CTE
                         (a data-modifying CTE is already caught by check 1)
      5. stacked         at most one statement, regardless of what the
                         statements contain -- two harmless SELECTs chained
                         with ";" pass checks 1-4, so this must stand alone

    Fails closed: a blocked word inside a string literal (e.g. a client named
    'Update Corp') is also blocked. A false block is safe; a false pass isn't.
    """
    statement = query.strip()
    upper = statement.upper()

    for keyword in BLOCKED_KEYWORDS:
        if re.search(rf"\b{keyword}\b", upper):
            return {"valid": False, "check": "keywords", "reason": f"Blocked: '{keyword}' is not permitted."}

    for func in BLOCKED_FUNCTIONS:
        if re.search(rf"\b{func}\b", upper):
            return {"valid": False, "check": "functions", "reason": f"Blocked: function '{func.lower()}' is not permitted."}

    if "--" in statement or "/*" in statement:
        return {"valid": False, "check": "comments", "reason": "Blocked: SQL comments are not permitted."}

    if not re.match(r"(SELECT|WITH)\b", upper):
        return {"valid": False, "check": "select_only", "reason": "Blocked: only SELECT queries are permitted."}

    # A single trailing ";" is allowed; any other ";" means a second statement.
    if ";" in statement.rstrip().removesuffix(";"):
        return {"valid": False, "check": "stacked", "reason": "Blocked: multiple statements detected."}

    return {"valid": True, "check": None, "reason": "OK"}


def generate_sql(question: str, history: list[dict] | None = None) -> tuple[str, dict]:
    result = invoke_claude(
        f"{format_history(history)}Question: {question}",
        max_tokens=512,
        system=get_system_prompt("sql_agent", "sql_generation"),
        component="sql_generation",
    )
    sql = result["text"].strip()
    sql = sql.removeprefix("```sql").removeprefix("```").removesuffix("```").strip()
    return sql, {"input": result["input_tokens"], "output": result["output_tokens"]}


def execute_query_safely(sql: str) -> dict:
    """
    The ONLY function in this codebase that connects to RDS or executes SQL.
    validate_sql runs first, unconditionally; a blocked query returns before
    a connection is ever opened.

    Defense in depth: even a query that passes validation runs in a read-only
    session with a statement timeout, so Postgres itself rejects any write
    the validator missed and a runaway query can't hang the Lambda.
    """
    validation = validate_sql(sql)
    if not validation["valid"]:
        return {"success": False, "error": validation["reason"], "validation": "BLOCKED",
                "executed": False, "sql": sql}

    conn = None
    try:
        conn = psycopg2.connect(
            host=os.getenv("RDS_HOST"), port=int(os.getenv("RDS_PORT")),
            dbname=os.getenv("RDS_DATABASE"), user=os.getenv("RDS_USER"),
            password=os.getenv("RDS_PASSWORD"), connect_timeout=10,
            options="-c statement_timeout=10000",
        )
        conn.set_session(readonly=True)
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        return {"success": True, "columns": cols, "rows": rows, "validation": "PASSED",
                "executed": True, "sql": sql}
    except Exception as e:
        return {"success": False, "error": str(e), "validation": "EXECUTION_ERROR",
                "executed": False, "sql": sql}
    finally:
        if conn is not None:
            conn.close()


def validated_sql_agent(question: str, confirmation: dict | None = None,
                        history: list[dict] | None = None) -> dict:
    """
    New question:  generate -> validate -> review -> execute (or pause)
    Confirmation:  verify token -> execute the confirmed SQL (validator
                   runs again inside execute_query_safely; review is skipped
                   because a person just approved this exact query)
    """
    # token_usage is keyed per Claude call, so tokenomics can attribute cost
    # to each component -- including generation on a query that then gets
    # blocked, since that call was still made and billed.
    token_usage = {}

    if confirmation:
        sql = confirmation.get("sql", "")
        ok, reason = verify_token(question, sql, confirmation.get("token", ""))
        if not ok:
            # Rejected before any validation or execution: nothing runs.
            return {"answer": f"Confirmation rejected: {reason}", "sql": sql,
                    "validation": "NOT_RUN", "validation_reason": reason,
                    "review": "CONFIRMATION_REJECTED", "executed": False, "token_usage": token_usage}
        review = "CONFIRMED"
    else:
        # History resolves "their" etc. On a confirmation it isn't needed: the
        # SQL is fixed, and already reflects whatever context produced it.
        sql, generation_usage = generate_sql(question, history)
        token_usage["sql_generation"] = generation_usage

        validation = validate_sql(sql)
        if validation["valid"]:
            reasons = review_sql(sql)
            if reasons:
                return {
                    "answer": ("This query passed validation but needs your confirmation before it "
                               "runs: " + " ".join(reasons)),
                    "sql": sql, "validation": "PASSED", "validation_reason": "OK",
                    "review": "NEEDS_CONFIRMATION", "executed": False,
                    "needs_confirmation": True, "confirmation_reasons": reasons,
                    "confirmation_token": issue_token(question, sql),
                    "token_usage": token_usage,
                }
        review = "ROUTINE"

    result = execute_query_safely(sql)

    if not result["success"]:
        return {
            "answer": f"Query blocked or failed: {result['error']}",
            "sql": sql,
            "validation": result["validation"],
            "validation_reason": result["error"],
            "review": review,
            "executed": result["executed"],
            "token_usage": token_usage,
        }

    prompt = (
        f"Question: {question}\nSQL: {sql}\nColumns: {result['columns']}\n"
        f"Rows (first 20 of {len(result['rows'])}): {result['rows'][:20]}"
    )
    interpretation = invoke_claude(
        prompt, max_tokens=500, system=get_system_prompt("sql_agent", "sql_interpretation"),
        component="sql_interpretation",
    )
    token_usage["sql_interpretation"] = {
        "input": interpretation["input_tokens"], "output": interpretation["output_tokens"]
    }

    return {
        "answer": interpretation["text"],
        "sql": sql,
        "validation": result["validation"],
        "validation_reason": "OK",
        "review": review,
        "executed": True,
        "row_count": len(result["rows"]),
        "token_usage": token_usage,
    }


def run_sql_agent(question: str, confirmation: dict | None = None,
                  history: list[dict] | None = None) -> dict:
    """
    Standardized tool interface: {success, answer, sources, metadata}.
    sources holds the SQL that was run (or blocked, or paused). Never raises
    -- any failure comes back as success=False in the same shape, so the
    orchestrator can decline gracefully instead of crashing.

    confirmation: {"sql": ..., "token": ...} from a previous
    needs_confirmation response, to run that exact flagged query.
    history: prior {question, answer} turns, to resolve follow-ups.
    """
    try:
        result = validated_sql_agent(question, confirmation, history)
    except Exception as e:
        return {
            "success": False,
            "answer": f"SQL agent error: {type(e).__name__}: {e}",
            "sources": [],
            "metadata": {"validation": "AGENT_ERROR", "executed": False, "token_usage": {}},
        }

    metadata = {
        "validation": result["validation"],
        "validation_reason": result["validation_reason"],
        "review": result["review"],
        "executed": result["executed"],
        "token_usage": result["token_usage"],
    }
    for key in ("row_count", "needs_confirmation", "confirmation_reasons", "confirmation_token"):
        if key in result:
            metadata[key] = result[key]

    return {
        # A paused query passed validation but produced no answer yet.
        "success": result["validation"] == "PASSED" and result["executed"],
        "answer": result["answer"],
        "sources": [result["sql"]],
        "metadata": metadata,
    }

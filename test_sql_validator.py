"""
test_sql_validator.py -- Bronze Step 5: validate every SQL query.

Part 1  validate_sql cases: the 5 required by the capstone, plus bypass
        attempts a space-delimited keyword check misses.
Part 2  No path to the database skips the validator:
          a) a blocked query never opens a database connection
          b) static scan: execute_query_safely is the only runtime function
             that connects to RDS or executes SQL, and it calls validate_sql
          c) defense in depth: a write forced past the validator is still
             rejected by the read-only session (live RDS call; the probe is a
             TEMP table, so nothing persists even if the session weren't
             read-only)

USAGE: python test_sql_validator.py
"""
import ast
import pathlib
import sys
from unittest import mock
from agents import sql_agent
from agents.sql_agent import validate_sql, execute_query_safely

# (description, query, expected valid, expected check that blocks it)
CASES = [
    # --- The 5 required cases ---------------------------------------------
    ("1. valid SELECT (allowed)",
     "SELECT name, contract_value FROM clients WHERE region = 'North'", True, None),
    ("2. destructive statement (blocked)",
     "DELETE FROM invoices WHERE status = 'Overdue'", False, "keywords"),
    ("3. column containing a blocked word: deleted_at (allowed)",
     "SELECT id, deleted_at FROM invoices", True, None),
    ("4. stacked query with a blocked keyword (blocked)",
     "SELECT * FROM clients; DROP TABLE clients", False, "keywords"),
    ("5. stacked query, neither half has a blocked keyword (blocked)",
     "SELECT * FROM clients; SELECT * FROM invoices", False, "stacked"),
    # --- Additional allowed reads -----------------------------------------
    ("valid SELECT with trailing semicolon (allowed)",
     "SELECT COUNT(*) FROM projects WHERE status = 'Active';", True, None),
    ("multi-line JOIN (allowed)",
     "SELECT c.name, SUM(i.amount)\nFROM clients c\nJOIN projects p ON p.client_id = c.id\n"
     "JOIN invoices i ON i.project_id = p.id\nGROUP BY c.name", True, None),
    ("read-only CTE (allowed)",
     "WITH totals AS (SELECT project_id, SUM(amount) AS t FROM invoices GROUP BY project_id) "
     "SELECT * FROM totals", True, None),
    ("other substrings of blocked words: updated_at, created_date (allowed)",
     "SELECT updated_at, created_date FROM clients", True, None),
    # --- Bypass attempts --------------------------------------------------
    ("lowercase destructive statement (blocked)",
     "drop table clients", False, "keywords"),
    ("keyword after a newline (blocked)",
     "SELECT 1\nUNION ALL\nSELECT 1 FROM clients WHERE 1=1\nDELETE", False, "keywords"),
    ("keyword after '(' (blocked)",
     "SELECT * FROM invoices WHERE id IN (DELETE FROM invoices RETURNING id)", False, "keywords"),
    ("SELECT ... INTO creates a table (blocked)",
     "SELECT * INTO clients_copy FROM clients", False, "keywords"),
    ("data-modifying CTE (blocked)",
     "WITH d AS (UPDATE projects SET budget = 0 RETURNING *) SELECT * FROM d", False, "keywords"),
    ("server file read function (blocked)",
     "SELECT pg_read_file('/etc/passwd')", False, "functions"),
    ("sleep function (blocked)",
     "SELECT pg_sleep(60)", False, "functions"),
    ("comment hiding the rest of a query (blocked)",
     "SELECT * FROM clients -- WHERE region = 'North'", False, "comments"),
    ("not a SELECT: SHOW (blocked)",
     "SHOW ALL", False, "select_only"),
    ("agent's refusal marker UNSAFE_REQUEST (blocked)",
     "UNSAFE_REQUEST", False, "select_only"),
    ("stacked SELECTs without spaces (blocked)",
     "SELECT 1;SELECT 2", False, "stacked"),
]


def run_validate_cases() -> bool:
    print("Part 1: validate_sql")
    all_ok = True
    for desc, query, want_valid, want_check in CASES:
        result = validate_sql(query)
        ok = result["valid"] == want_valid and result["check"] == want_check
        all_ok &= ok
        verdict = "ALLOWED" if result["valid"] else f"BLOCKED by {result['check']}"
        print(f"  [{'PASS' if ok else 'FAIL'}] {desc}")
        print(f"         {query!r:.90}")
        print(f"         -> {verdict}: {result['reason']}")
    return all_ok


def test_blocked_query_never_connects() -> bool:
    with mock.patch.object(sql_agent.psycopg2, "connect",
                           side_effect=AssertionError("database was contacted")) as connect:
        result = execute_query_safely("DROP TABLE clients")
    ok = result["validation"] == "BLOCKED" and not connect.called
    print(f"  [{'PASS' if ok else 'FAIL'}] a) blocked query returns {result['validation']} "
          f"and psycopg2.connect was called {connect.call_count} time(s)")
    return ok


RUNTIME_FILES = ["agents/sql_agent.py", "agents/sql_review.py", "memory.py", "agents/retrieval_agent.py", "orchestrator.py",
                 "tool_registry.py", "tokenomics_logger.py", "lambda_handler.py", "bedrock_client.py"]


def test_single_execution_path() -> bool:
    """Find every function that calls psycopg2.connect or .execute()."""
    root = pathlib.Path(__file__).parent
    db_callers = []
    for rel in RUNTIME_FILES:
        tree = ast.parse((root / rel).read_text())
        for func in ast.walk(tree):
            if not isinstance(func, ast.FunctionDef):
                continue
            calls = {ast.unparse(n.func) for n in ast.walk(func) if isinstance(n, ast.Call)}
            if any(c.endswith("psycopg2.connect") or c.endswith(".execute") for c in calls):
                db_callers.append((rel, func.name, "validate_sql" in calls))
    ok = db_callers == [("agents/sql_agent.py", "execute_query_safely", True)]
    print(f"  [{'PASS' if ok else 'FAIL'}] b) functions that touch the database: "
          + ", ".join(f"{f}:{n} (calls validate_sql: {v})" for f, n, v in db_callers))
    return ok


def test_readonly_session() -> bool:
    forced_pass = {"valid": True, "check": None, "reason": "forced for test"}
    with mock.patch.object(sql_agent, "validate_sql", return_value=forced_pass):
        result = execute_query_safely("CREATE TEMP TABLE validator_probe (id int)")
    ok = result["validation"] == "EXECUTION_ERROR" and "read-only" in result.get("error", "")
    print(f"  [{'PASS' if ok else 'FAIL'}] c) write forced past the validator -> "
          f"{result['validation']}: {result.get('error', '').strip()}")
    return ok


if __name__ == "__main__":
    part1 = run_validate_cases()
    print("\nPart 2: no path to the database skips the validator")
    part2 = all([test_blocked_query_never_connects(), test_single_execution_path(), test_readonly_session()])

    print(f"\n{len(CASES)} validator cases + 3 execution-path tests")
    print(f"SQL validation suite: {'PASS' if part1 and part2 else 'FAIL'}")
    sys.exit(0 if part1 and part2 else 1)

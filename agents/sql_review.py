"""
agents/sql_review.py -- Stretch Goal B: human-in-the-loop review of queries
that pass validation but are unusual enough that a person should look first.

Flow (in sql_agent.validated_sql_agent):
    generate SQL -> validate -> review_sql -> unusual? pause : execute
A paused query is returned with its SQL, the reasons it was flagged,
needs_confirmation=True, and a confirmation token. Nothing executes.

CONFIRMATION WITHOUT SERVER STATE: Lambda keeps nothing between
invocations, so the server can't remember what it flagged. Instead the token
is an HMAC-SHA256 signature over the exact question and SQL, made with a
secret only the server holds (CONFIRMATION_SECRET). A confirmation must send
back the same question, the same SQL, and the token. Changing one character
of the SQL breaks the signature, so a confirmation can only ever run the
exact query that was flagged. Tokens expire after CONFIRMATION_TTL_SECONDS.

A confirmed query still goes through execute_query_safely, so the validator
runs again -- confirmation skips the review, never the validation.
"""
import hashlib
import hmac
import os
import re
import time

CONFIRMATION_TTL_SECONDS = 15 * 60

# ── What counts as "unusual" ────────────────────────────────────────────────
# A query is flagged if ANY of these is true. Each returns a reason string.
#
#   1. SELECT * with no WHERE      every column of every row of a table
#   2. Unbounded read              no WHERE, no LIMIT, and no aggregation
#                                  (COUNT/SUM/AVG/MIN/MAX/GROUP BY), so it
#                                  returns every row it touches
#   3. Wide join                   joins more than two tables
#
# Aggregates and LIMITed queries are exempt from rule 2 because their output
# is bounded however large the table grows. Known limitation: rules 1-2 look
# for WHERE anywhere in the statement, so a WHERE inside a subquery exempts
# the outer query.
MAX_TABLES = 2


def review_sql(sql: str) -> list[str]:
    """Return the reasons a validated query is unusual; empty list = routine."""
    upper = " ".join(sql.upper().split())
    has_where = re.search(r"\bWHERE\b", upper) is not None
    has_limit = re.search(r"\bLIMIT\b", upper) is not None
    has_aggregate = re.search(r"\b(COUNT|SUM|AVG|MIN|MAX)\s*\(|\bGROUP BY\b", upper) is not None
    select_star = re.search(r"\bSELECT\s+(DISTINCT\s+)?\*", upper) is not None
    tables = 1 + len(re.findall(r"\bJOIN\b", upper))

    reasons = []
    if select_star and not has_where:
        reasons.append("SELECT * with no WHERE clause returns every column of every row.")
    if not (has_where or has_limit or has_aggregate):
        reasons.append("No WHERE, LIMIT, or aggregation: the query returns every row it reads.")
    if tables > MAX_TABLES:
        reasons.append(f"Joins {tables} tables (more than {MAX_TABLES}).")
    return reasons


# ── Confirmation tokens ─────────────────────────────────────────────────────

def _secret() -> bytes:
    secret = os.getenv("CONFIRMATION_SECRET")
    if not secret:
        # Fail closed: without a secret no token can be issued or verified,
        # so a flagged query can never be confirmed.
        raise RuntimeError("CONFIRMATION_SECRET is not set; flagged queries cannot be confirmed.")
    return secret.encode()


def _signature(issued_at: int, question: str, sql: str) -> str:
    message = f"{issued_at}\n{question}\n{sql}".encode()
    return hmac.new(_secret(), message, hashlib.sha256).hexdigest()


def issue_token(question: str, sql: str, issued_at: int | None = None) -> str:
    """Token binding this exact question + SQL: '<issued_at>.<hmac>'."""
    issued_at = int(time.time()) if issued_at is None else issued_at
    return f"{issued_at}.{_signature(issued_at, question, sql)}"


def verify_token(question: str, sql: str, token: str) -> tuple[bool, str]:
    """(ok, reason). ok only if the token was issued for exactly this question and SQL."""
    try:
        issued_str, signature = token.split(".", 1)
        issued_at = int(issued_str)
    except (AttributeError, ValueError):
        return False, "Malformed confirmation token."
    if not hmac.compare_digest(signature, _signature(issued_at, question, sql)):
        return False, "Confirmation does not match the flagged query; the SQL or question was changed."
    if time.time() - issued_at > CONFIRMATION_TTL_SECONDS:
        return False, "Confirmation token has expired; ask the question again."
    return True, "OK"

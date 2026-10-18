"""
check_confirmation.py -- Stretch Goal B: human-in-the-loop confirmation.

Runs each requirement through lambda_handler.handler (the deployed entry
point) and counts real psycopg2.connect calls, so "nothing executed" is
measured rather than assumed:

  1. Unusual query pauses     SQL + reason + needs_confirmation, 0 connections
  2a. Confirm exact query     runs it (1 connection)
  2b. Confirm altered SQL     rejected, 0 connections
  3. Routine question         runs with no confirmation step
  4. Confirmed but invalid    still BLOCKED by the validator, 0 connections
  +  Expired token            rejected, 0 connections

Each response is saved to deliverables/stretch_b/.

USAGE: python check_confirmation.py
"""
import json
import os
import sys
from unittest import mock
from agents import sql_agent
from agents.sql_review import issue_token
from lambda_handler import handler

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deliverables", "stretch_b")
FLAGGED_Q = "Show me everything in the invoices table."
ROUTINE_Q = "How many active projects do we have?"


def call(name: str, body: dict) -> tuple[dict, int]:
    """Invoke the handler; return (response body, number of DB connections opened)."""
    real_connect = sql_agent.psycopg2.connect
    with mock.patch.object(sql_agent.psycopg2, "connect", side_effect=real_connect) as spy:
        result = handler({"body": json.dumps(body)}, None)
    response = json.loads(result["body"])
    with open(os.path.join(OUT_DIR, f"{name}.json"), "w") as f:
        json.dump({"request": body, "response": response, "db_connections": spy.call_count}, f, indent=2)
    return response, spy.call_count


def show(title, response, connections, checks):
    print("=" * 78)
    print(title)
    for key in ("route", "sql_validation", "sql_review", "sql_executed", "needs_confirmation",
                "pending_sql", "confirmation_reasons"):
        if key in response:
            print(f"  {key}: {response[key]}")
    print(f"  db connections opened: {connections}")
    print(f"  answer: {' '.join(response.get('answer', response.get('error', '')).split())[:220]}")
    for name, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    return all(checks.values())


if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    results = []

    # 1. An unusual query pauses
    r1, c1 = call("1_flagged", {"question": FLAGGED_Q})
    results.append(show(f"1. Unusual query pauses: {FLAGGED_Q!r}", r1, c1, {
        "needs_confirmation is true": r1.get("needs_confirmation") is True,
        "response contains the SQL": bool(r1.get("pending_sql")),
        "response contains the reason": bool(r1.get("confirmation_reasons")),
        "nothing executed (0 DB connections)": c1 == 0 and r1.get("sql_executed") is False,
    }))
    flagged_sql, token = r1.get("pending_sql", ""), r1.get("confirmation_token", "")

    # 2a. Confirming the exact flagged query runs it
    r2, c2 = call("2a_confirm_exact", {"question": FLAGGED_Q, "confirm": {"sql": flagged_sql, "token": token}})
    results.append(show("2a. Confirm the exact flagged query", r2, c2, {
        "review is CONFIRMED": r2.get("sql_review") == "CONFIRMED",
        "validation PASSED": r2.get("sql_validation") == "PASSED",
        "the query executed (1 DB connection)": c2 == 1 and r2.get("sql_executed") is True,
    }))

    # 2b. The same token can't run a different query
    altered = "SELECT * FROM clients"
    r3, c3 = call("2b_confirm_altered", {"question": FLAGGED_Q, "confirm": {"sql": altered, "token": token}})
    results.append(show(f"2b. Confirm with altered SQL: {altered!r}", r3, c3, {
        "review is CONFIRMATION_REJECTED": r3.get("sql_review") == "CONFIRMATION_REJECTED",
        "nothing executed (0 DB connections)": c3 == 0 and r3.get("sql_executed") is False,
    }))

    # 3. A routine question has no confirmation step
    r4, c4 = call("3_routine", {"question": ROUTINE_Q})
    results.append(show(f"3. Routine question: {ROUTINE_Q!r}", r4, c4, {
        "needs_confirmation is false": r4.get("needs_confirmation") is False,
        "review is ROUTINE": r4.get("sql_review") == "ROUTINE",
        "executed directly (1 DB connection)": c4 == 1 and r4.get("sql_executed") is True,
    }))

    # 4. A confirmed query that fails validation is still blocked. The token is
    #    genuine -- signed with the server's secret -- standing in for a query
    #    confirmed before a validation rule tightened.
    bad_sql = "SELECT * FROM invoices; DROP TABLE invoices"
    r5, c5 = call("4_confirmed_invalid", {"question": FLAGGED_Q,
                                          "confirm": {"sql": bad_sql, "token": issue_token(FLAGGED_Q, bad_sql)}})
    results.append(show(f"4. Confirmed query that fails validation: {bad_sql!r}", r5, c5, {
        "token accepted (review CONFIRMED)": r5.get("sql_review") == "CONFIRMED",
        "validator still BLOCKED it": r5.get("sql_validation") == "BLOCKED",
        "nothing executed (0 DB connections)": c5 == 0 and r5.get("sql_executed") is False,
    }))

    # +. An expired token is rejected
    old_token = issue_token(FLAGGED_Q, flagged_sql, issued_at=1)
    r6, c6 = call("5_expired_token", {"question": FLAGGED_Q, "confirm": {"sql": flagged_sql, "token": old_token}})
    results.append(show("+. Confirm with an expired token", r6, c6, {
        "review is CONFIRMATION_REJECTED": r6.get("sql_review") == "CONFIRMATION_REJECTED",
        "nothing executed (0 DB connections)": c6 == 0,
    }))

    print("=" * 78)
    print(f"Confirmation check: {'PASS' if all(results) else 'FAIL'} ({sum(results)}/{len(results)} scenarios)")
    sys.exit(0 if all(results) else 1)

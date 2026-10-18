"""
check_gold.py -- Gold: conversation memory and confirmation work together.

A multi-turn conversation where a follow-up produces a flagged query, which
is then confirmed:

  Turn 1   "Which client has the highest contract value?"  -> Beta Financial
  Turn 2   "List all of their invoices together with the project and client
           details." + history -> SQL filtered to Beta Financial (resolved
           context), joins 3 tables -> flagged, nothing executed
  Confirm  same question + history + {sql, token} -> executes exactly the
           flagged SQL; the completed turn is added to the history

  local (default)   calls lambda_handler.handler directly
  --deployed        runs the real `aws lambda invoke` CLI against the function

Responses are saved to deliverables/gold/<local|deployed>/.

USAGE: python check_gold.py [--deployed]
"""
import sys
from check_memory import DEPLOYED, invoke, save, one_line

TURN_1 = "Which client has the highest contract value?"
TURN_2 = "List all of their invoices together with the project and client details."

if __name__ == "__main__":
    print(f"Mode: {'DEPLOYED (aws lambda invoke)' if DEPLOYED else 'LOCAL (lambda_handler)'}\n")

    req1 = {"question": TURN_1}
    r1 = invoke(req1)
    save("gold", "1_turn_1", req1, r1)
    print(f"Turn 1: {TURN_1}\n  answer: {one_line(r1['answer'])}\n")

    req2 = {"question": TURN_2, "history": r1["history"]}
    r2 = invoke(req2)
    save("gold", "2_turn_2_flagged", req2, r2)
    flagged_sql = r2.get("pending_sql", "")
    print(f"Turn 2 (history from turn 1): {TURN_2}")
    print(f"  needs_confirmation: {r2.get('needs_confirmation')}  executed: {r2.get('sql_executed')}")
    print(f"  reasons: {r2.get('confirmation_reasons')}")
    print(f"  pending_sql:\n    " + "\n    ".join(flagged_sql.splitlines()) + "\n")

    req3 = {"question": TURN_2, "history": r1["history"],
            "confirm": {"sql": flagged_sql, "token": r2.get("confirmation_token", "")}}
    r3 = invoke(req3)
    save("gold", "3_confirm", req3, r3)
    print(f"Confirm the flagged query:\n  review: {r3.get('sql_review')}  validation: {r3.get('sql_validation')}"
          f"  executed: {r3.get('sql_executed')}\n  answer: {one_line(r3['answer'], 260)}\n")

    checks = {
        "turn 1 identifies Beta Financial": "beta financial" in r1["answer"].lower(),
        "turn 2 is flagged (needs_confirmation)": r2.get("needs_confirmation") is True,
        "turn 2 did not execute": r2.get("sql_executed") is False,
        "flagged SQL reflects resolved context (filters on Beta Financial)": "beta financial" in flagged_sql.lower(),
        "paused turn not yet added to history": len(r2.get("history", [])) == 1,
        "confirmation accepted (review CONFIRMED)": r3.get("sql_review") == "CONFIRMED",
        "confirmed query executed and passed validation": r3.get("sql_executed") is True and r3.get("sql_validation") == "PASSED",
        "answer is about Beta Financial's invoices": "beta financial" in r3["answer"].lower(),
        "completed turn added to history (2 turns)": len(r3.get("history", [])) == 2,
    }
    for name, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\nGold check ({'deployed' if DEPLOYED else 'local'}): {'PASS' if all(checks.values()) else 'FAIL'}")
    sys.exit(0 if all(checks.values()) else 1)

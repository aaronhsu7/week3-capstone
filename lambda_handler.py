"""
lambda_handler.py -- wraps the orchestrator in the format Lambda expects.

Request body:
  {"question": "..."}                                      a new question
  {"question": "...", "confirm": {"sql": "...", "token": "..."}}
                                                           confirm a flagged query

A flagged query comes back with needs_confirmation=true, pending_sql,
confirmation_reasons, and confirmation_token. To run it, send the same
question with confirm = {"sql": pending_sql, "token": confirmation_token}.

Either request may include "history": [{"question", "answer"}, ...]. Lambda
keeps no memory, so the caller sends the history and gets the updated one
back in "history" to send with the next request. A paused (flagged) turn is
not added: it's added when the confirmation completes it.
"""
import json
from orchestrator import build_orchestrator
from tokenomics_logger import run_traced_query
from memory import clean_history, append_turn

orchestrator = build_orchestrator()


def handler(event, context):
    try:
        body = json.loads(event.get("body", "{}"))
        question = body.get("question", "").strip()
        confirm = body.get("confirm")

        if not question:
            return {"statusCode": 400, "body": json.dumps({"error": "question is required"})}
        if confirm is not None and not (isinstance(confirm, dict) and "sql" in confirm and "token" in confirm):
            return {"statusCode": 400, "body": json.dumps({"error": "confirm must be {\"sql\": ..., \"token\": ...}"})}

        history = clean_history(body.get("history"))
        result = run_traced_query(orchestrator, question, confirmation=confirm, history=history)

        sql_meta = (result.get("sql_result") or {}).get("metadata", {})
        response = {
            "answer": result["final_answer"],
            "route": result["route"],
            "sql_validation": sql_meta.get("validation"),
            "sql_review": sql_meta.get("review"),
            "sql_executed": sql_meta.get("executed"),
            "needs_confirmation": bool(sql_meta.get("needs_confirmation")),
            # Per-component and total tokens + USD, from every logged Claude call
            "token_usage": result.get("cost", {}),
        }
        if response["needs_confirmation"]:
            response["pending_sql"] = result["sql_result"]["sources"][0]
            response["confirmation_reasons"] = sql_meta["confirmation_reasons"]
            response["confirmation_token"] = sql_meta["confirmation_token"]
            response["history"] = history
        else:
            response["history"] = append_turn(history, question, result["final_answer"])

        return {"statusCode": 200, "body": json.dumps(response)}
    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}

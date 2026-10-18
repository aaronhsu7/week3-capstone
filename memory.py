"""
memory.py -- Stretch Goal A: conversation memory.

Lambda keeps nothing between invocations, so the CALLER carries the
conversation: each request may include "history" (prior {question, answer}
turns), and each response returns the updated history to send next time.

The history is replayed into two prompts -- the router and SQL generation --
so references like "their" or "that project" resolve to what was said
earlier.

THE CAP: every replayed turn adds input tokens to both of those calls, on
every later request. Two limits keep that bounded:

  MAX_HISTORY_TURNS = 3   Follow-ups in this domain refer back to the last
                          turn or two ("their", "that project"); three turns
                          covers a reference to something two questions ago
                          without replaying a whole session.
  MAX_ANSWER_CHARS  = 400 Answers to "both" questions run to 1,500+ chars.
                          The router and SQL generator need the entities
                          named (client, project), which appear in the first
                          sentence or two -- not the full bulleted answer.

Worst case that's ~3 x (question + 400 chars) =~ 400 extra input tokens per
call, replayed into 2 calls per request. Measured cost: see check_memory.py.

History is client-supplied, so it is treated as untrusted input: malformed
turns are dropped, and it can only influence which SELECT is generated --
the validator still blocks any write it might try to induce.
"""

MAX_HISTORY_TURNS = 3
MAX_ANSWER_CHARS = 400


def clean_history(history) -> list[dict]:
    """Keep only well-formed turns, capped to the most recent MAX_HISTORY_TURNS."""
    if not isinstance(history, list):
        return []
    turns = [
        {"question": t["question"].strip(), "answer": t["answer"].strip()}
        for t in history
        if isinstance(t, dict) and isinstance(t.get("question"), str) and isinstance(t.get("answer"), str)
    ]
    return turns[-MAX_HISTORY_TURNS:]


def format_history(history: list[dict] | None) -> str:
    """Prompt block for the replayed turns; empty string when there are none."""
    turns = clean_history(history)
    if not turns:
        return ""
    lines = ["Conversation so far (oldest first):"]
    for i, t in enumerate(turns, 1):
        answer = " ".join(t["answer"].split())
        if len(answer) > MAX_ANSWER_CHARS:
            answer = answer[:MAX_ANSWER_CHARS].rsplit(" ", 1)[0] + " ..."
        lines.append(f"{i}. Q: {t['question']}\n   A: {answer}")
    return "\n".join(lines) + "\n\n"


def append_turn(history: list[dict] | None, question: str, answer: str) -> list[dict]:
    """History to return to the caller after this turn, already capped."""
    return clean_history((history or []) + [{"question": question, "answer": answer}])

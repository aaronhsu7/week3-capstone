# Setup & Step 1: Confirm Your Foundation

## RDS

- **Instance:** `orchestrator-db-aaronhsu` (Postgres 16, database `bootcamp`, user `dbadmin`)
- **Tables:** `clients`, `projects`, `consultants`, `invoices`
- **`.env`:** `RDS_HOST`, `RDS_PORT`, `RDS_DATABASE`, `RDS_USER`, `RDS_PASSWORD`

## OpenSearch

This is a new domain for the capstone, separate from the lab domains.

- **Domain:** `capstone-search-aaronhsu` (OpenSearch 2.11, t3.small, 1 node, 10GB)
- **Index:** `documents` (384-dim `knn_vector`, FAISS HNSW, `innerproduct`)
- **Access:** the Lambda role `orchestrator-lambda-role-aaronhsu` and the IAM user running the scripts
- **`.env`:** `OPENSEARCH_ENDPOINT` (no `https://`), `OPENSEARCH_INDEX`

```bash
./infra/provision_opensearch.sh aaronhsu
venv/bin/python ingest_documents.py
```

## Step 1: Confirm Your Foundation

Run every command from inside `unit-3-capstone/`. Both `venv/bin/python` and `.env` are found relative to the current folder, so the commands fail anywhere else:

```bash
cd ~/Desktop/capstones/week3/unit-3-capstone
```

**1. Both resources are up.** The RDS command should print `available`. The OpenSearch command prints `Processing` and then the endpoint. `False` means the domain has finished provisioning and is ready. `True` means it's still being created, so wait.

```bash
aws rds describe-db-instances --db-instance-identifier orchestrator-db-aaronhsu \
  --query "DBInstances[0].DBInstanceStatus" --output text

aws opensearch describe-domain --domain-name capstone-search-aaronhsu \
  --query 'DomainStatus.[Processing,Endpoint]' --output text
```

**2. RDS is reachable and populated.** Every table should return a count above 0:

```bash
venv/bin/python -c "
import os, psycopg2; from dotenv import load_dotenv; load_dotenv()
conn = psycopg2.connect(host=os.getenv('RDS_HOST'), port=os.getenv('RDS_PORT'), dbname=os.getenv('RDS_DATABASE'), user=os.getenv('RDS_USER'), password=os.getenv('RDS_PASSWORD'))
cur = conn.cursor()
for t in ['clients', 'projects', 'consultants', 'invoices']:
    cur.execute(f'SELECT COUNT(*) FROM {t}'); print(t, cur.fetchone()[0])
"
```

**3. OpenSearch is reachable and populated.** The count should be above 0:

```bash
venv/bin/python -c "
from agents.retrieval_agent import get_opensearch_client
print('documents:', get_opensearch_client().count(index='documents')['count'])
"
```

**4. Run both checks in one go.** This produces the evidence below and exits non-zero if either check fails:

```bash
venv/bin/python check_foundation.py
```
## Step 2: Standardize Your Two Tools

**Requirement:** `run_sql_agent` and `run_retrieval_agent` both return exactly these keys:

```python
{
    "success": bool,
    "answer": str,
    "sources": list,     # SQL: the query used. Retrieval: document names
    "metadata": dict     # validation status, chunk scores, grounding, token usage
}
```

**How to check:**

```bash
venv/bin/python check_interfaces.py
```

The script calls each tool directly, with no orchestrator involved, and prints its top-level keys and full output. It runs three cases:

| Case | Expect |
|---|---|
| `run_sql_agent("How many active projects do we have?")` | `success: true`, `validation: PASSED` |
| `run_retrieval_agent("How much can employees claim for home office equipment?")` | `success: true`, sources include `remote-work-policy.pdf` |
| `run_sql_agent("Delete all invoices that are overdue.")` | `success: false`, `validation: BLOCKED`, still the same 4 keys |

**Passes when** each case shows `[PASS]` next to its keys and the last line reads `Interface check: PASS`.

## Step 3: Build the Shared Tool Registry

**Requirement:** both tools are registered in [tool_registry.py](../tool_registry.py). Each description must name what the tool **is** for and what it is **not** for.

| Tool | For | Not for |
|---|---|---|
| `sql_agent` | Structured records in the 4 tables: counts, totals, lists, comparisons | Policies, procedures, document content; read-only, so data changes are blocked |
| `retrieval_agent` | The 4 documents: remote work policy, cloud migration playbook, Q2 2026 report, retail case study | Live record counts or totals; topics outside those documents |

**How to check:**

```bash
venv/bin/python tool_registry.py
```

The script prints each tool's name, description and input schema in Claude's tool format, then one check line per tool.

**Passes when** both tools show `[PASS]`, meaning the description states what it's for: `True` and what it's not for: `True`.

## Stretch Goal B: Human-in-the-Loop Confirmation

**Requirement:** a query that passes validation but is unusual doesn't run. The system returns the SQL, the reason it was flagged, and `needs_confirmation: true`. It runs only after a follow-up request confirms that **exact** query, and it still goes through the validator.

**What counts as unusual** ([agents/sql_review.py](../agents/sql_review.py)). Any one of these flags a query:

| Rule | Why |
|---|---|
| `SELECT *` with no `WHERE` | Returns every column of every row |
| No `WHERE`, `LIMIT`, or aggregate (`COUNT`/`SUM`/`AVG`/`MIN`/`MAX`/`GROUP BY`) | Returns every row it reads; the output grows with the table |
| Joins more than 2 tables | Wide joins are easy to get subtly wrong |

**Flow:** generate → validate → review → execute *or* pause. A flagged response includes `pending_sql` and a `confirmation_token`. The token is an HMAC signature over the exact question and SQL, made with `CONFIRMATION_SECRET` (in `.env` and the Lambda environment), and it expires after 15 minutes. To confirm, resend the same question with the SQL and the token:

```json
{"question": "...", "confirm": {"sql": "<pending_sql>", "token": "<confirmation_token>"}}
```

Any change to the SQL or the question breaks the signature, so the confirmation is rejected and nothing runs.

**How to check:**

```bash
venv/bin/python check_confirmation.py
```

The script runs each case through `lambda_handler` and counts real database connections. It saves every request and response to `deliverables/stretch_b/`.

| Case | Passes when |
|---|---|
| "Show me everything in the invoices table." | `needs_confirmation: true`, with the SQL and the reasons; **0** DB connections |
| Confirm with the exact SQL and token | Runs the query (1 connection), review `CONFIRMED` |
| Confirm with altered SQL and the same token | `CONFIRMATION_REJECTED`, 0 connections |
| "How many active projects do we have?" | Runs directly, review `ROUTINE`, no confirmation step |
| Confirm a query that fails validation | Still `BLOCKED` by the validator, 0 connections |
| Confirm with an expired token | `CONFIRMATION_REJECTED`, 0 connections |

**Passes when** the last line reads `Confirmation check: PASS (6/6 scenarios)`.

## Stretch Goal A: Conversation Memory

**Requirement:** a follow-up like "What about their total contract value?" works without repeating context, locally and deployed, and the token cost of memory is measured.

**How it works** ([memory.py](../memory.py)). Lambda keeps no memory, so the **caller carries the conversation**:
- Each request may include `"history": [{"question", "answer"}, ...]`.
- Each response returns the updated `history` to send with the next request.
- The history is replayed into the **router** and **SQL generation** prompts, so "their" resolves to the client named earlier.
- A paused (flagged) turn isn't added to the history until its confirmation completes it.

**History cap:**

| Limit | Value | Why |
|---|---|---|
| `MAX_HISTORY_TURNS` | 3 | Follow-ups refer back a turn or two; 3 covers that without replaying a whole session |
| `MAX_ANSWER_CHARS` | 400 per replayed answer | The router and SQL generator need the entities named, not full bulleted answers |

Every replayed turn adds input tokens to 2 calls on every later request, so the cap bounds the worst case at roughly +400 tokens per call.

**How to check:**

```bash
venv/bin/python check_memory.py              # local
venv/bin/python check_memory.py --deployed   # through aws lambda invoke
```

| Check | Result (deployed) |
|---|---|
| Turn 1: "How many active projects does Acme Corp have?" | 1 active project |
| Turn 2: "What about their total contract value?" with history | **Acme Corp: $250,000**, not the all-client total of $1,655,000 |
| Same question with no history (control) | Doesn't resolve to Acme |
| Cost: turn 2 with history minus the same question without | **+104 input tokens**, 9% on classification + SQL generation |

**Passes when** the last line reads `Memory check (...): PASS`. Responses are saved to `deliverables/stretch_a/`.

## Gold: Memory + Confirmation Together

**Requirement:** a multi-turn conversation where a follow-up produces a flagged query. The flagged query reflects the earlier context, confirming it runs exactly that query, and it works deployed.

```bash
venv/bin/python check_gold.py              # local
venv/bin/python check_gold.py --deployed   # through aws lambda invoke
```

| Turn | Result |
|---|---|
| 1. "Which client has the highest contract value?" | Beta Financial |
| 2. "List all of their invoices together with the project and client details." + history | Flagged: joins 3 tables. SQL filters `WHERE c.name = 'Beta Financial'`, the **resolved context**. Not executed |
| 3. Confirm with the same question + history + `{sql, token}` | Runs **exactly** the flagged SQL (review `CONFIRMED`, validation `PASSED`) and adds the completed turn to the history |

**Passes when** the last line reads `Gold check (...): PASS`, both locally and with `--deployed`. Responses are saved to `deliverables/gold/`.

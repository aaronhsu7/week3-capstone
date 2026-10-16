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

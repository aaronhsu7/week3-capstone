"""
check_foundation.py -- Bronze Step 1: confirm each data store works on its
own before anything is integrated.

  RDS:        one query per table -> every table returns rows
  OpenSearch: document count on the index -> count > 0

Exits non-zero if any check fails, so it can gate later steps.

USAGE: python check_foundation.py
"""
import os
import sys
import psycopg2
from dotenv import load_dotenv
from agents.retrieval_agent import get_opensearch_client
load_dotenv()

TABLES = ["clients", "projects", "consultants", "invoices"]
INDEX_NAME = os.getenv("OPENSEARCH_INDEX", "documents")


def check_rds() -> bool:
    print(f"RDS: {os.getenv('RDS_HOST')}")
    conn = psycopg2.connect(
        host=os.getenv("RDS_HOST"), port=int(os.getenv("RDS_PORT")),
        dbname=os.getenv("RDS_DATABASE"), user=os.getenv("RDS_USER"),
        password=os.getenv("RDS_PASSWORD"), connect_timeout=10,
    )
    ok = True
    with conn, conn.cursor() as cur:
        for table in TABLES:
            # Table names come from the fixed list above, never from input.
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            count = cur.fetchone()[0]
            cur.execute(f"SELECT * FROM {table} LIMIT 1")
            sample = cur.fetchone()
            status = "PASS" if count > 0 else "FAIL"
            ok &= count > 0
            print(f"  [{status}] {table:<12} {count:>4} rows   sample: {sample}")
    conn.close()
    return ok


def check_opensearch() -> bool:
    print(f"\nOpenSearch: {os.getenv('OPENSEARCH_ENDPOINT')}  index: {INDEX_NAME}")
    client = get_opensearch_client()
    count = client.count(index=INDEX_NAME)["count"]
    sources = client.search(
        index=INDEX_NAME,
        body={"size": 0, "aggs": {"docs": {"terms": {"field": "source", "size": 50}}}},
    )["aggregations"]["docs"]["buckets"]
    status = "PASS" if count > 0 else "FAIL"
    print(f"  [{status}] {count} chunks across {len(sources)} documents")
    for b in sources:
        print(f"         {b['key']}: {b['doc_count']} chunk(s)")
    return count > 0


if __name__ == "__main__":
    results = {}
    for name, check in [("RDS", check_rds), ("OpenSearch", check_opensearch)]:
        try:
            results[name] = check()
        except Exception as e:
            print(f"  [FAIL] {name} unreachable: {type(e).__name__}: {e}")
            results[name] = False

    print("\n" + "\n".join(f"{n}: {'PASS' if ok else 'FAIL'}" for n, ok in results.items()))
    sys.exit(0 if all(results.values()) else 1)

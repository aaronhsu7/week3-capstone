# SQL Validation Test Suite

Output of `venv/bin/python test_sql_validator.py` (exit code 0).

Cases 1–5 are the five required by the capstone README. The rest are extra bypass attempts and allowed reads. Part 2 shows there is no path to the database that skips the validator.

```
Part 1: validate_sql
  [PASS] 1. valid SELECT (allowed)
         "SELECT name, contract_value FROM clients WHERE region = 'North'"
         -> ALLOWED: OK
  [PASS] 2. destructive statement (blocked)
         "DELETE FROM invoices WHERE status = 'Overdue'"
         -> BLOCKED by keywords: Blocked: 'DELETE' is not permitted.
  [PASS] 3. column containing a blocked word: deleted_at (allowed)
         'SELECT id, deleted_at FROM invoices'
         -> ALLOWED: OK
  [PASS] 4. stacked query with a blocked keyword (blocked)
         'SELECT * FROM clients; DROP TABLE clients'
         -> BLOCKED by keywords: Blocked: 'DROP' is not permitted.
  [PASS] 5. stacked query, neither half has a blocked keyword (blocked)
         'SELECT * FROM clients; SELECT * FROM invoices'
         -> BLOCKED by stacked: Blocked: multiple statements detected.
  [PASS] valid SELECT with trailing semicolon (allowed)
         "SELECT COUNT(*) FROM projects WHERE status = 'Active';"
         -> ALLOWED: OK
  [PASS] multi-line JOIN (allowed)
         'SELECT c.name, SUM(i.amount)\nFROM clients c\nJOIN projects p ON p.client_id = c.id\nJOIN
         -> ALLOWED: OK
  [PASS] read-only CTE (allowed)
         'WITH totals AS (SELECT project_id, SUM(amount) AS t FROM invoices GROUP BY project_id) SE
         -> ALLOWED: OK
  [PASS] other substrings of blocked words: updated_at, created_date (allowed)
         'SELECT updated_at, created_date FROM clients'
         -> ALLOWED: OK
  [PASS] lowercase destructive statement (blocked)
         'drop table clients'
         -> BLOCKED by keywords: Blocked: 'DROP' is not permitted.
  [PASS] keyword after a newline (blocked)
         'SELECT 1\nUNION ALL\nSELECT 1 FROM clients WHERE 1=1\nDELETE'
         -> BLOCKED by keywords: Blocked: 'DELETE' is not permitted.
  [PASS] keyword after '(' (blocked)
         'SELECT * FROM invoices WHERE id IN (DELETE FROM invoices RETURNING id)'
         -> BLOCKED by keywords: Blocked: 'DELETE' is not permitted.
  [PASS] SELECT ... INTO creates a table (blocked)
         'SELECT * INTO clients_copy FROM clients'
         -> BLOCKED by keywords: Blocked: 'INTO' is not permitted.
  [PASS] data-modifying CTE (blocked)
         'WITH d AS (UPDATE projects SET budget = 0 RETURNING *) SELECT * FROM d'
         -> BLOCKED by keywords: Blocked: 'UPDATE' is not permitted.
  [PASS] server file read function (blocked)
         "SELECT pg_read_file('/etc/passwd')"
         -> BLOCKED by functions: Blocked: function 'pg_read_file' is not permitted.
  [PASS] sleep function (blocked)
         'SELECT pg_sleep(60)'
         -> BLOCKED by functions: Blocked: function 'pg_sleep' is not permitted.
  [PASS] comment hiding the rest of a query (blocked)
         "SELECT * FROM clients -- WHERE region = 'North'"
         -> BLOCKED by comments: Blocked: SQL comments are not permitted.
  [PASS] not a SELECT: SHOW (blocked)
         'SHOW ALL'
         -> BLOCKED by select_only: Blocked: only SELECT queries are permitted.
  [PASS] agent's refusal marker UNSAFE_REQUEST (blocked)
         'UNSAFE_REQUEST'
         -> BLOCKED by select_only: Blocked: only SELECT queries are permitted.
  [PASS] stacked SELECTs without spaces (blocked)
         'SELECT 1;SELECT 2'
         -> BLOCKED by stacked: Blocked: multiple statements detected.

Part 2: no path to the database skips the validator
  [PASS] a) blocked query returns BLOCKED and psycopg2.connect was called 0 time(s)
  [PASS] b) functions that touch the database: agents/sql_agent.py:execute_query_safely (calls validate_sql: True)
  [PASS] c) write forced past the validator -> EXECUTION_ERROR: cannot execute CREATE TABLE in a read-only transaction

20 validator cases + 3 execution-path tests
SQL validation suite: PASS
```

Unit 3 Capstone: Building an Intelligent Document Search Pipeline
===========================

### Project Overview

In this capstone, you'll design and build a real-world intelligent document search system using AWS. Your pipeline will automate document ingestion, ETL, vectorization, structured data storage, and setup for future Retrieval-Augmented Generation (RAG) workflows.

**You'll deliver a working pipeline that processes both unstructured (PDF) and structured (CSV, JSON) files, making them searchable and ready for analytics.**

**This year, the AI query layer (previously a stretch goal) is a required Bronze deliverable, and includes a mandatory SQL validation layer and tokenomics tracking.**

**Two implementation notes based on confirmed sandbox testing:**
1. **Document embeddings use Sentence Transformers, not Bedrock.** Both of Bedrock's embedding model families were tested directly and both are denied — `amazon.titan-embed-text-v2:0` and `cohere.embed-english-v3` — while Claude (text generation) is confirmed working. This is a structural gap in current sandbox permissions (embeddings capability, not one specific model), not something fixable by picking a different Bedrock embedding model. Sentence Transformers runs locally, requires no API key, and produces the same kind of embedding vector for OpenSearch indexing.
2. **All Bedrock/Claude calls must use the inference-profile ARN as the model ID, not the bare model name.** Newer Claude models on Bedrock (including `claude-sonnet-4-6`) require this — calling with just `anthropic.claude-sonnet-4-6-v1:0` returns a `ValidationException`. See Step 2.5 for the correct pattern.

**✅ This entire pipeline has been verified end-to-end in a live sandbox run** — chunking, Sentence Transformers embedding generation, vector indexing, semantic retrieval, and Claude-via-Bedrock generation were all confirmed working together against a real test document, producing a correct, properly-cited answer. This is not a theoretical fix; every piece has been run and confirmed. See the Instructor Guide's verification script for the exact test used.

* * * * *

Step 1: Review Your Materials
-----------------------------

-   **Understand your reference architecture** and data flow (diagram will be provided in class).

-   Review AWS service documentation and code/lab samples provided in the course.

Step 2: Build the Required AWS Data Flow & Infrastructure
---------------------------------------------------------

#### **Must-Have Features:**

-   **Central Storage:**

    -   Use **S3** as the main repository for all incoming documents (PDFs, CSVs, JSON).

-   **Automated Ingestion & Processing:**

    -   Uploads to S3 automatically trigger **AWS Lambda** functions.

    -   For PDFs: Textract extraction must use the **asynchronous** API (`start_document_text_detection` + `get_document_text_detection`), not the synchronous `detect_document_text`. The synchronous API only supports single-page PDFs — a hard limit, not just a slower path — and real policy/case-study documents are frequently multiple pages. This requires **two Lambda functions**: one triggered by the S3 upload that starts the Textract job and returns immediately, and a second triggered by an SNS notification when Textract completes, which retrieves the results and continues the pipeline (chunking, embedding via Sentence Transformers, indexing). See Step 2.1 for the exact architecture and IAM setup this requires.

    -   Intelligently chunk the text (500-1000 tokens) and generate embeddings via **Sentence Transformers** (local, no API key required — see implementation note above).

    -   Store raw extracted text in **AWS RDS** for structured querying.

    -   Store vector embeddings in **OpenSearch** for semantic search.

Step 2.1: Why Two Lambdas (Not One) for PDF Processing
--------------------------------------------------------

A single Lambda function has a hard 15-minute execution limit. Textract's asynchronous jobs can take longer than that for larger documents, and there's no way to know in advance how long a given job will take — so a Lambda that starts a Textract job and then sits in a polling loop waiting for it to finish risks timing out on exactly the documents most likely to need it.

The standard, correct pattern instead:

1.  **Lambda 1 (S3-triggered):** calls `start_document_text_detection` with a `NotificationChannel` pointing at an SNS topic, then returns immediately. It does not wait for the job to finish.
2.  **Textract** processes the document asynchronously and publishes a completion message to that SNS topic when done.
3.  **Lambda 2 (SNS-triggered):** fires when that message arrives, calls `get_document_text_detection` with the job ID from the SNS message (handling pagination via `NextToken` for longer documents), and continues the pipeline — chunking, embedding, and indexing.

This requires an SNS topic and an IAM role that Textract itself assumes to publish to it, in addition to the Lambda execution role from Step 2. Your instructor guide has the exact setup commands.

-   **ETL for Structured Data:**

    -   Use **AWS Glue Crawler** to automatically catalog and discover schemas in all ingested files.

    -   Use **AWS Glue Transform** to run ETL jobs for:

        -   Data normalization

        -   Schema validation

        -   Systematic loading of CSV/JSON into **Redshift** tables

        -   Populate Redshift with both structured data and vector embeddings

-   **Unified Data Warehouse:**

    -   **AWS Redshift** acts as your consolidated warehouse for SQL-queryable data and vector embeddings.

-   **Visualization & BI:**

    -   Generate at least 2 charts from your Redshift data using matplotlib (Python), saved as image files.

-   **Automation, Security & Control:**

    -   Use **Lambda** for workflow automation and event handling.

    -   Use **BOTO3** for scripting and programmatic control.

    -   Apply **IAM** best practices for security and permissions.

Step 2.5: Build the Required AI Query Layer (Now Bronze, Not Stretch)
------------------------------------------------------------------------

Using **Amazon Bedrock (Claude)**, build a query interface that can:

-   **Route intelligently** — given a natural language question, decide whether it needs OpenSearch (semantic/document search), Redshift (SQL/structured data), or both

-   **Translate to SQL** — for questions requiring Redshift, generate and execute a SQL query using Claude via Bedrock

-   **Generate contextual responses** — for questions requiring OpenSearch, retrieve relevant chunks and generate a grounded answer citing sources

```python
import boto3, json

bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")
account_id = boto3.client("sts").get_caller_identity()["Account"]

# Claude models on Bedrock (including claude-sonnet-4-6) require the
# inference-profile ARN, not the bare model ID. Calling with just
# "anthropic.claude-sonnet-4-6-v1:0" returns a ValidationException.
MODEL_ID = f"arn:aws:bedrock:us-east-1:{account_id}:inference-profile/us.anthropic.claude-sonnet-4-6"

def invoke_claude(prompt: str, max_tokens: int = 500) -> dict:
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}]
    }
    response = bedrock.invoke_model(modelId=MODEL_ID, body=json.dumps(body))
    result = json.loads(response["body"].read())
    return {"text": result["content"][0]["text"],
            "input_tokens": result["usage"]["input_tokens"],
            "output_tokens": result["usage"]["output_tokens"]}
```

> **✅ Check before building anything else:** Run this function once, standalone, with a simple test prompt. Confirm you get a real response before building the rest of the query layer on top of it.

Step 2.6: Build the LangGraph Orchestrator (Required, Not Stretch)
-------------------------------------------------------------------

The routing logic from Step 2.5 must be implemented as a **LangGraph** state graph, not a single classification prompt with an if/else chain in plain Python. This is the actual multi-agent architecture this capstone is building toward — a classification node, a SQL agent node, a retrieval agent node, and a dedicated synthesis node, connected by conditional edges.

```python
from typing import TypedDict, Optional, Literal
from langgraph.graph import StateGraph, END


class AgentState(TypedDict):
    question: str
    route: str                      # "sql", "search", or "both"
    sql_result: Optional[dict]
    search_result: Optional[dict]
    final_answer: Optional[str]


def classify_node(state: AgentState) -> dict:
    route = classify_query(state["question"])  # from Step 2.5
    return {"route": route}


def sql_node(state: AgentState) -> dict:
    sql = generate_sql(state["question"])
    validation = validate_sql(sql)  # from Step 2.75
    if not validation["valid"]:
        return {"sql_result": {"success": False, "error": validation["reason"], "sql": sql}}
    result = execute_sql_safely(sql)  # your Redshift execution logic
    return {"sql_result": result}


def search_node(state: AgentState) -> dict:
    chunks = retrieve(state["question"])
    answer = generate_grounded_answer(state["question"], chunks)
    return {"search_result": {"answer": answer, "sources": [c["source"] for c in chunks]}}


def synthesize_node(state: AgentState) -> dict:
    parts = []
    if state.get("sql_result"):
        parts.append(f"From the data warehouse: {state['sql_result'].get('answer', state['sql_result'])}")
    if state.get("search_result"):
        sr = state["search_result"]
        parts.append(f"From our documentation ({', '.join(sr['sources'])}): {sr['answer']}")

    if len(parts) > 1:
        # Both sources contributed — synthesize one coherent answer citing
        # both, rather than just concatenating two separate answers back to back.
        combined = "\n\n".join(parts)
        result = invoke_claude(
            f"Combine the following into ONE coherent answer, citing both "
            f"sources explicitly:\n\n{combined}\n\nQuestion: {state['question']}"
        )
        return {"final_answer": result["text"]}

    return {"final_answer": parts[0] if parts else "No answer could be generated."}


def route_after_classify(state: AgentState) -> Literal["sql_agent", "search_agent"]:
    # "both" starts with the SQL agent; the graph chains to search_agent next
    return "sql_agent" if state["route"] in ("sql", "both") else "search_agent"


def route_after_sql(state: AgentState) -> Literal["search_agent", "synthesize"]:
    return "search_agent" if state["route"] == "both" else "synthesize"


graph = StateGraph(AgentState)
graph.add_node("classify", classify_node)
graph.add_node("sql_agent", sql_node)
graph.add_node("search_agent", search_node)
graph.add_node("synthesize", synthesize_node)

graph.set_entry_point("classify")
graph.add_conditional_edges("classify", route_after_classify, {
    "sql_agent": "sql_agent",
    "search_agent": "search_agent",
})
graph.add_conditional_edges("sql_agent", route_after_sql, {
    "search_agent": "search_agent",
    "synthesize": "synthesize",
})
graph.add_edge("search_agent", "synthesize")
graph.add_edge("synthesize", END)

orchestrator = graph.compile()

# Run it:
# result = orchestrator.invoke({"question": "...", "route": "", "sql_result": None, "search_result": None, "final_answer": None})
# print(result["final_answer"])
```

> **Why not just chain function calls in plain Python?** You could — but this capstone specifically requires a multi-agent system built with a LangGraph orchestrator, not just working code that happens to route correctly. The state graph makes the routing logic, the data each step depends on, and the synthesis step all explicit and inspectable, which matters more as this system grows (this is also exactly the architecture Day 4 assumed was already in place).

> **✅ Check before building the full test suite:** Run all three routes (`sql`, `search`, `both`) through `orchestrator.invoke(...)` individually and confirm `final_answer` is populated correctly in each case, especially the `both` case — this is where a state-management mistake is most likely to surface (e.g., `sql_result` or `search_result` silently missing by the time `synthesize_node` runs).

Step 2.75: Add a Required SQL Validation Layer
-------------------------------------------------

No Bedrock-generated SQL query may execute against Redshift without passing through validation first:

```python
BLOCKED_KEYWORDS = ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "TRUNCATE", "GRANT", "REVOKE"]

def validate_sql(query: str) -> dict:
    query_upper = query.upper().strip()
    for keyword in BLOCKED_KEYWORDS:
        if f" {keyword} " in f" {query_upper} " or query_upper.startswith(keyword):
            return {"valid": False, "reason": f"Blocked: {keyword} not permitted"}
    if not query_upper.startswith("SELECT"):
        return {"valid": False, "reason": "Only SELECT queries are permitted"}
    return {"valid": True, "reason": "OK"}
```

Test with at least 5 cases including at least one stacked-query attempt (`SELECT ...; DROP TABLE ...;`) before wiring the validator into a live Redshift connection.

Step 2.9: Add Tokenomics Tracking
-------------------------------------

Log token usage for every Bedrock call (classification, SQL generation, and contextual response generation), and produce a cost summary after a 10-query test run.

Step 3: Test and Document
-------------------------

-   Test your pipeline end-to-end by uploading PDFs and structured files and validating data flow to RDS, OpenSearch, and Redshift.

-   Test your AI query layer against both the standard query types and the harder synthesis queries below.

-   Document your architecture, how each service is used, and how to test or extend the pipeline.

### New Harder Queries (Required This Year)

These require your system to route to **both** OpenSearch and Redshift and combine the results into a single answer citing both sources — not two separate answers, and not just whichever source was checked first:

-   "Does our current customer churn rate (from the data warehouse) align with what our documented retention strategy says we should be seeing?"

-   "Based on our data governance policy documents, are any of the currently-ingested datasets missing required metadata fields (check against the Glue Catalog)?"

* * * * *

Must-Have Checklist
-------------------
>🥉 Bronze - complete all must-haves

-   S3 stores raw PDFs, CSVs, and JSONs

-   Two Lambdas handle PDF processing: one triggers on S3 upload and starts an async Textract job via SNS notification; a second triggers on Textract completion and retrieves the results

-   Textract extracts and chunks PDF text

-   **Sentence Transformers generates document embeddings** (Bedrock embedding models — both Titan and Cohere — confirmed denied; see implementation note)

-   Raw text stored in RDS; embeddings in OpenSearch

-   Glue Crawler catalogs and discovers data schemas

-   Glue ETL normalizes, validates, and loads CSV/JSON into Redshift

-   Redshift consolidates structured and vector data

-   Matplotlib charts (2+) visualize Redshift data

-   Lambda and BOTO3 automate flows

-   IAM secures resources

-   **AI query layer implemented: smart routing, NL-to-SQL, and contextual document response generation, all using Bedrock (Claude), via the inference-profile ARN**

-   **Routing and synthesis implemented as a LangGraph state graph** — a classification node, a SQL agent node, a retrieval agent node, and a dedicated synthesis node connected by conditional edges, not a single classification prompt with plain Python if/else branching

-   **SQL validation layer implemented and tested (minimum 5 cases, including a stacked-query test)**

-   **Tokenomics logging across all Bedrock calls, with a 10-query cost summary**

-   **Both new harder synthesis queries answered correctly, citing both sources**

Stretch Goals
-------------
>🥈 Silver - complete 1 stretch goal <br> 🥇 Gold - complete both

**Push further by adding intelligence and unified user experience:**

-   **Enterprise Knowledge API:**

    -   Build a simple API Gateway + Lambda endpoint exposing your query interface for programmatic access, rather than only a CLI or notebook interface — deliver unified endpoints that blend document retrieval with structured data insights, enabling users to query both with natural language

-   **Persistent Conversation Memory:**

    -   Extend your LangGraph state to accept and use conversation history across turns, so a follow-up question ("What's their total budget?" after "How many active projects does Acme Corp have?") resolves correctly without the user repeating context

Tips for Success
----------------

-   **Work in stages:** Build one piece, test it, then connect it to the next.

-   **Automate everything:** Use Lambda, Glue, and BOTO3 to keep manual steps to a minimum.

-   **Confirm infrastructure before AI work:** Make sure your S3, RDS, OpenSearch, Redshift, and Glue pipeline is fully working before building the AI query layer on top of it — debugging infrastructure and AI logic at the same time wastes your capstone time.

-   **Confirm Bedrock access before building the AI layer:** Run a single standalone test call to `invoke_claude()` using the inference-profile ARN pattern above before writing any routing or SQL-generation logic on top of it.

-   **Validate before you execute:** Never let a Bedrock-generated SQL query run against Redshift without passing through your validator first.

-   **Focus on clarity:** Comment your code and document every architectural choice.

-   **Ask for help:** Don't spend too long blocked!

Deliverables
------------

-   Full pipeline codebase and infrastructure setup (Boto3 scripts, Glue jobs, Lambda functions)

-   Working AI query interface supporting SQL, search, and combined routing, implemented as a LangGraph state graph

-   Validation test suite output

-   Tokenomics summary from a 10-query test run

-   README with architecture diagram, setup instructions, and test results for both new harder queries

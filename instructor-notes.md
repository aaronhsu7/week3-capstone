# Unit 3 Capstone — Instructor Guide
## Intelligent Document Search Pipeline (AWS-Native, Bedrock/Claude, Sentence Transformers Embeddings)

> **Environment:** AWS "AI & Data Sandbox" specifically — confirmed working for EC2, S3, RDS, Redshift, Glue, Lambda, OpenSearch, IAM, and Bedrock Claude invocation (both standard and streaming). Bedrock Titan Embeddings is confirmed **denied** — do not build against it.
> **✅ INSTRUCTOR CHECK** — verify before learners continue
> **⚠️ COMMON ISSUE** — problems that come up repeatedly and how to fix them

---

## Before the Capstone Begins

- [ ] Confirm you and all learners are in the **"AI & Data Sandbox – Deloitte"** environment, not any other sandbox
- [ ] Run the three confirmation tests below — every learner, before building anything
- [ ] `boto3` installed: `pip install boto3 sentence-transformers --break-system-packages`

### Confirmation Test 1 — Identity and Core Services

```bash
aws sts get-caller-identity
aws s3 ls
aws bedrock list-foundation-models --region us-east-1
```

### Confirmation Test 2 — Claude Invocation (Standard)

```python
import boto3, json

bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")
account_id = boto3.client("sts").get_caller_identity()["Account"]
model_id = f"arn:aws:bedrock:us-east-1:{account_id}:inference-profile/us.anthropic.claude-sonnet-4-6"

response = bedrock.invoke_model(
    modelId=model_id,
    body=json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 50,
        "messages": [{"role": "user", "content": "Say hello in one sentence."}]
    })
)
print(json.loads(response["body"].read()))
```

> **✅ INSTRUCTOR CHECK:** Every learner gets a real Claude response before proceeding. If anyone gets `ValidationException` mentioning "on-demand throughput isn't supported," they're using the bare model ID instead of the inference-profile ARN — this is the single most common mistake with this capstone.

### Confirmation Test 3 — Confirm Bedrock Embedding Models Are Genuinely Unavailable (Don't Skip This)

Both Bedrock embedding model families have been tested and confirmed denied — this is checked as a pair, not just one model, since the gap is at the embeddings-capability level rather than tied to one specific model:

```python
for model_id, body in [
    ("amazon.titan-embed-text-v2:0", {"inputText": "test"}),
    ("cohere.embed-english-v3", {"texts": ["test"], "input_type": "search_document"}),
]:
    try:
        response = bedrock.invoke_model(modelId=model_id, body=json.dumps(body))
        print(f"{model_id} WORKS — safe to use instead of Sentence Transformers")
    except Exception as e:
        print(f"{model_id} denied (expected): {e}")
```

> **✅ INSTRUCTOR CHECK:** Confirm both fail with `AccessDeniedException` before committing to Sentence Transformers for the whole class. If Whizlabs has since granted either one, that model can be used instead of Sentence Transformers — check this fresh each cohort rather than assuming last time's result still holds; a Whizlabs follow-up request for this exact gap is already in progress as of this writing.

### Confirmation Test 4 — Full Pipeline Verification Script (Run This Before Class)

Before relying on any of the above in front of learners, run the complete pipeline end-to-end using the verification script provided alongside this guide (`full_pipeline_test.py`). This chains every piece together — chunking, Sentence Transformers embedding, Chroma indexing (standing in for OpenSearch locally), semantic retrieval, and Claude-via-Bedrock generation — against a real test document, and prints a real generated answer at the end if everything works.

```bash
pip install boto3 chromadb sentence-transformers --break-system-packages
python3 full_pipeline_test.py
```

**Expected output:** four labeled steps (chunk, embed+index, retrieve, generate), ending with a real Claude-generated answer citing the source document, followed by a `✅ FULL PIPELINE VERIFIED` confirmation line.

> **✅ INSTRUCTOR CHECK:** Run this once yourself before the cohort starts, and encourage each learner to run it once early in the capstone (before building their own pipeline) as their own first checkpoint — catching a broken environment here costs one minute, catching it after building half a pipeline costs much more.

> **⚠️ COMMON ISSUE — Hugging Face rate limiting:** The first run prints a warning: *"You are sending unauthenticated requests to the HF Hub. Please set a HF_TOKEN..."* This doesn't cause a failure on its own, but if an entire cohort runs this simultaneously in the same class session, unauthenticated requests may start hitting rate limits. If this becomes a problem, have learners set a free Hugging Face account token as an environment variable before running:
> ```bash
> export HF_TOKEN=your_huggingface_token_here
> ```

---

## Step 1 — Confirm Core AWS Access

```bash
aws ec2 describe-instances
aws rds describe-db-instances
aws redshift describe-clusters
aws glue get-crawlers
aws opensearch list-domain-names
```

> **✅ INSTRUCTOR CHECK:** Every command returns real data or an empty result, not an error. Resolve any failures before building on top of broken infrastructure access.

---

## Step 2 — S3 and IAM Setup

```bash
aws s3api create-bucket --bucket unit3-capstone-[cohort-id] --region us-east-1
aws s3 cp ./sample-data/policy.pdf s3://unit3-capstone-[cohort-id]/incoming/
aws s3 cp ./sample-data/tickets.csv s3://unit3-capstone-[cohort-id]/incoming/
```

```bash
cat > trust-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "lambda.amazonaws.com"},
    "Action": "sts:AssumeRole"
  }]
}
EOF

aws iam create-role --role-name unit3-lambda-role --assume-role-policy-document file://trust-policy.json
aws iam attach-role-policy --role-name unit3-lambda-role --policy-arn arn:aws:iam::aws:policy/AmazonS3FullAccess
aws iam attach-role-policy --role-name unit3-lambda-role --policy-arn arn:aws:iam::aws:policy/AmazonTextractFullAccess
aws iam attach-role-policy --role-name unit3-lambda-role --policy-arn arn:aws:iam::aws:policy/AmazonRDSFullAccess
aws iam attach-role-policy --role-name unit3-lambda-role --policy-arn arn:aws:iam::aws:policy/AmazonOpenSearchServiceFullAccess
aws iam attach-role-policy --role-name unit3-lambda-role --policy-arn arn:aws:iam::aws:policy/AmazonBedrockFullAccess
aws iam attach-role-policy --role-name unit3-lambda-role --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
```

### SNS Topic and Textract Publish Role (Required for Async Textract — see Step 3)

Textract's asynchronous API needs its own IAM role to publish job-completion notifications to SNS, separate from the Lambda execution role above:

```bash
aws sns create-topic --name unit3-textract-notifications

cat > textract-trust-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "textract.amazonaws.com"},
    "Action": "sts:AssumeRole"
  }]
}
EOF

aws iam create-role --role-name unit3-textract-sns-role --assume-role-policy-document file://textract-trust-policy.json
aws iam attach-role-policy --role-name unit3-textract-sns-role --policy-arn arn:aws:iam::aws:policy/AmazonSNSFullAccess
```

> **✅ INSTRUCTOR CHECK:** Confirm the SNS topic ARN and both role ARNs (`unit3-lambda-role` and `unit3-textract-sns-role`) are noted somewhere accessible — Step 3 needs all three.

---

## Step 3 — Two Lambda Functions: PDF Processing (Textract Async + SNS)

**Why two Lambdas:** a single Lambda has a hard 15-minute execution limit. Textract's synchronous `detect_document_text` API only supports single-page PDFs — real policy and case-study documents are frequently multiple pages, so that API fails outright on realistic input. The asynchronous API (`start_document_text_detection` + `get_document_text_detection`) handles multi-page documents correctly, but a Lambda that starts an async job and then polls in a loop for it to finish risks timing out on exactly the documents most likely to take a while. The standard fix: split into two Lambdas connected by SNS.

### Lambda 1 — Triggered by S3 Upload, Starts the Textract Job

```python
# lambda_1_start_textract.py
import boto3

textract = boto3.client("textract")

SNS_TOPIC_ARN = "arn:aws:sns:us-east-1:<account-id>:unit3-textract-notifications"
TEXTRACT_SNS_ROLE_ARN = "arn:aws:iam::<account-id>:role/unit3-textract-sns-role"


def lambda_handler(event, context):
    bucket = event["Records"][0]["s3"]["bucket"]["name"]
    key = event["Records"][0]["s3"]["object"]["key"]

    if not key.endswith(".pdf"):
        return {"statusCode": 200, "body": "Not a PDF, skipping"}

    response = textract.start_document_text_detection(
        DocumentLocation={"S3Object": {"Bucket": bucket, "Name": key}},
        NotificationChannel={
            "SNSTopicArn": SNS_TOPIC_ARN,
            "RoleArn": TEXTRACT_SNS_ROLE_ARN
        },
        JobTag=key  # carried through to the SNS message, lets Lambda 2 know which document this was
    )

    print(f"Started Textract job {response['JobId']} for {key}")
    return {"statusCode": 200, "jobId": response["JobId"]}
```

This Lambda returns immediately after starting the job — it does not wait for Textract to finish.

### Lambda 2 — Triggered by the SNS Notification, Retrieves Results and Continues the Pipeline

```python
# lambda_2_process_textract_result.py
import boto3
import json

textract = boto3.client("textract")


def lambda_handler(event, context):
    message = json.loads(event["Records"][0]["Sns"]["Message"])
    job_id = message["JobId"]
    status = message["Status"]
    document_key = message.get("JobTag", "unknown-document")

    if status != "SUCCEEDED":
        print(f"Textract job {job_id} for {document_key} did not succeed: {status}")
        return {"statusCode": 200, "body": f"Job status: {status}, skipping"}

    # Retrieve results, handling pagination for longer documents
    response = textract.get_document_text_detection(JobId=job_id)
    lines = [b["Text"] for b in response["Blocks"] if b["BlockType"] == "LINE"]
    next_token = response.get("NextToken")
    while next_token:
        response = textract.get_document_text_detection(JobId=job_id, NextToken=next_token)
        lines.extend(b["Text"] for b in response["Blocks"] if b["BlockType"] == "LINE")
        next_token = response.get("NextToken")

    text = "\n".join(lines)
    chunks = chunk_text(text)
    embeddings = embed_chunks(chunks)  # Sentence Transformers — see Step 4
    # Index to OpenSearch — see Step 5
    # Store raw text to RDS — see Step 6

    print(f"Processed {len(chunks)} chunks from {document_key}")
    return {"statusCode": 200, "chunks_created": len(chunks)}


def chunk_text(text, chunk_size=500, overlap=50):
    words = text.split()
    return [" ".join(words[i:i+chunk_size]) for i in range(0, len(words), chunk_size - overlap) if words[i:i+chunk_size]]
```

Configure Lambda 2's trigger as a subscription to the SNS topic created in Step 2, not an S3 event — Lambda 2 never touches S3 directly, it only reacts to Textract's completion message.

> **✅ INSTRUCTOR CHECK:** Confirm learners test with at least one genuinely multi-page PDF, not just single-page samples — the whole point of this two-Lambda pattern is handling documents the synchronous API can't. A single-page-only test would pass even with the broken synchronous approach, hiding the exact bug this architecture exists to fix.

> **⚠️ COMMON ISSUE:** `sentence-transformers` and its model weights need to be packaged into Lambda 2's deployment (as a Lambda layer, or a container image if the package is too large for a standard zip). This is a real deployment complexity Bedrock embeddings would have avoided — budget extra time for this specifically.

---

## Step 4 — Embeddings via Sentence Transformers (Bedrock Titan Replacement)

```python
# embeddings.py
from sentence_transformers import SentenceTransformer

_model = None

def _get_model():
    global _model
    if _model is None:
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model

def embed_chunks(chunks: list[str]) -> list[list[float]]:
    model = _get_model()
    return model.encode(chunks).tolist()
```

> **Note the dimension:** `all-MiniLM-L6-v2` produces 384-dimensional vectors — this must match your OpenSearch index's `knn_vector` dimension setting exactly (see Step 5). This differs from Titan Embeddings' 1024 dimensions — if any learner copies OpenSearch index configuration from other lessons that assumed Titan, this mismatch will cause silent indexing failures.

---

## Step 5 — OpenSearch Setup

```python
# opensearch_client.py
from opensearchpy import OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth
import boto3

def get_client():
    credentials = boto3.Session().get_credentials()
    auth = AWS4Auth(credentials.access_key, credentials.secret_key, "us-east-1", "es", session_token=credentials.token)
    return OpenSearch(
        hosts=[{"host": "your-opensearch-endpoint", "port": 443}],
        http_auth=auth, use_ssl=True, connection_class=RequestsHttpConnection
    )

def create_index(client):
    index_body = {
        "settings": {"index.knn": True},
        "mappings": {
            "properties": {
                "content": {"type": "text"},
                "source": {"type": "keyword"},
                "embedding": {
                    "type": "knn_vector",
                    "dimension": 384,  # matches all-MiniLM-L6-v2, NOT Titan's 1024
                    "method": {"name": "hnsw", "space_type": "cosinesimil", "engine": "faiss"}
                }
            }
        }
    }
    client.indices.create(index="documents", body=index_body, ignore=400)
```

---

## Step 6 — RDS Setup

```bash
aws rds create-db-instance \
  --db-instance-identifier unit3-capstone-db \
  --db-instance-class db.t3.micro \
  --engine postgres \
  --master-username admin \
  --master-user-password YourSecurePassword123 \
  --allocated-storage 20
```

```sql
CREATE TABLE document_metadata (
    id SERIAL PRIMARY KEY,
    filename TEXT,
    raw_text TEXT,
    chunk_count INTEGER,
    ingested_at TIMESTAMP DEFAULT NOW()
);
```

---

## Step 7 — Glue Crawler + ETL, Redshift

```bash
aws glue create-crawler \
  --name unit3-structured-data-crawler \
  --role unit3-lambda-role \
  --database-name unit3_catalog \
  --targets '{"S3Targets": [{"Path": "s3://unit3-capstone-[cohort-id]/incoming/"}]}'

aws glue start-crawler --name unit3-structured-data-crawler
```

```bash
aws redshift create-cluster \
  --cluster-identifier unit3-warehouse \
  --node-type dc2.large \
  --master-username admin \
  --master-user-password YourSecurePassword123 \
  --cluster-type single-node
```

Write a Glue ETL job that normalizes and loads into Redshift — same real data quality handling emphasis as any ETL step (duplicates, missing values, inconsistent formats).

---

## Step 8 — The AI Query Layer (Bedrock Claude, Correct ARN Pattern)

```python
# query_interface.py
import boto3, json

bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")
account_id = boto3.client("sts").get_caller_identity()["Account"]
MODEL_ID = f"arn:aws:bedrock:us-east-1:{account_id}:inference-profile/us.anthropic.claude-sonnet-4-6"

def invoke_claude(prompt: str, max_tokens: int = 500) -> dict:
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}]
    }
    response = bedrock.invoke_model(modelId=MODEL_ID, body=json.dumps(body))
    result = json.loads(response["body"].read())
    return {
        "text": result["content"][0]["text"],
        "input_tokens": result["usage"]["input_tokens"],
        "output_tokens": result["usage"]["output_tokens"]
    }

def classify_query(question: str) -> str:
    result = invoke_claude(
        "Classify this question as exactly one word: 'sql', 'search', or 'both'.\n"
        "'sql' = questions about numbers, counts, trends, structured data in a data warehouse\n"
        "'search' = questions about document content, policies, explanations\n"
        "'both' = needs both structured data and document context\n\n"
        f"Question: {question}\n\nReply with one word only.",
        max_tokens=10
    )
    route = result["text"].strip().lower().rstrip(".")
    return route if route in ["sql", "search", "both"] else "search"
```

> **⚠️ COMMON ISSUE:** If any learner pastes Bedrock code from an older tutorial or the original 2025 capstone spec verbatim, it will almost certainly use the bare model ID (`anthropic.claude-sonnet-4-6-v1:0`) and fail with `ValidationException`. This is the single highest-frequency bug expected in this capstone — flag it to the whole class up front rather than debugging it individually for each learner who hits it.

`classify_query` above is a building block, not the final routing mechanism — Step 8.5 below wires it into the required LangGraph orchestrator, along with the SQL generation, validation (Step 2.75 of the README), tokenomics logging (Step 2.9), and document retrieval/synthesis pieces.

---

## Step 8.5 — The LangGraph Orchestrator (Required — Not a Stretch Goal This Year)

This is the piece that makes this capstone a genuine multi-agent system rather than a single script with an if/else branch. Walk through this with the whole cohort before learners build their own — the "both" routing case is where most mistakes happen.

```python
# orchestrator.py
from typing import TypedDict, Optional, Literal
from langgraph.graph import StateGraph, END


class AgentState(TypedDict):
    question: str
    route: str                      # "sql", "search", or "both"
    sql_result: Optional[dict]
    search_result: Optional[dict]
    final_answer: Optional[str]


def classify_node(state: AgentState) -> dict:
    route = classify_query(state["question"])
    return {"route": route}


def sql_node(state: AgentState) -> dict:
    sql = generate_sql(state["question"])
    validation = validate_sql(sql)  # Step 2.75
    if not validation["valid"]:
        return {"sql_result": {"success": False, "error": validation["reason"], "sql": sql}}
    result = execute_sql_safely(sql)  # learner's Redshift execution logic
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
        combined = "\n\n".join(parts)
        result = invoke_claude(
            f"Combine the following into ONE coherent answer, citing both "
            f"sources explicitly:\n\n{combined}\n\nQuestion: {state['question']}"
        )
        return {"final_answer": result["text"]}

    return {"final_answer": parts[0] if parts else "No answer could be generated."}


def route_after_classify(state: AgentState) -> Literal["sql_agent", "search_agent"]:
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
```

**Design note for the "both" case:** rather than a true parallel fan-out (both agents running concurrently), this graph deliberately chains SQL → Search → Synthesize sequentially when the route is "both." This is simpler to reason about and debug than parallel branches with concurrent state merging, and for a capstone the correctness and clarity matter more than shaving off latency from running two Bedrock/Redshift calls in parallel. Learners who want the extra challenge of true parallel execution can attempt it as a personal extension, but it is not required.

> **✅ INSTRUCTOR CHECK:** Have learners run all three routes individually through `orchestrator.invoke(...)` — `sql` only, `search` only, and `both` — and print the full resulting state each time. Confirm `sql_result` and `search_result` are populated (or correctly `None`) exactly as expected for each route before moving on to the harder synthesis test queries. The `both` case is where a state-management mistake is most likely to hide — for example, `synthesize_node` running before `search_node` actually finishes.

> **⚠️ COMMON ISSUE:** Learners forget to include `route: ""`, `sql_result: None`, `search_result: None`, and `final_answer: None` in the initial state dict passed to `orchestrator.invoke(...)`, since `TypedDict` doesn't enforce this at runtime the way it looks like it should. Missing keys cause a `KeyError` partway through the graph, not an obvious error message up front — if a learner gets a cryptic `KeyError`, check their initial state dict first.

---

## Step 9 — Visualization (Matplotlib)

QuickSight is not used in this capstone. Generate charts directly from Redshift data using matplotlib:

```python
import matplotlib.pyplot as plt
import psycopg2

conn = psycopg2.connect(host="your-redshift-endpoint", dbname="dev", user="admin", password="YourSecurePassword123", port=5439)
cur = conn.cursor()
cur.execute("SELECT severity, resolution_hours FROM support_tickets")
rows = cur.fetchall()
conn.close()

# Build at least 2 charts from the query results — e.g., a distribution
# histogram and a grouped bar chart by category — save each as a .png file.
```

> **✅ INSTRUCTOR CHECK:** Confirm each learner's charts are generated directly from live Redshift data (not hardcoded sample values) and saved as image files, satisfying the Bronze visualization requirement.

---

## Full Verification Checklist

| Check | Verify |
|---|---|
| Correct sandbox confirmed | `aws sts get-caller-identity` matches AI & Data Sandbox |
| Claude invocation via inference-profile ARN | Confirmation Test 2 passes |
| Bedrock embedding models (Titan + Cohere) re-checked this cohort | Confirmation Test 3 run fresh, not assumed from last time |
| Full pipeline verified end-to-end | `full_pipeline_test.py` (Confirmation Test 4) runs clean and prints a real generated answer |
| S3 upload triggers Lambda 1, which starts an async Textract job via SNS; Lambda 2 fires on completion and extracts text | Lambda 1 and Lambda 2 logs both show activity; test with a genuinely multi-page PDF, not just single-page samples |
| Sentence Transformers embeddings generated and indexed | OpenSearch document count is non-zero, dimension is 384 |
| RDS stores raw text | Query `document_metadata` table |
| Glue Crawler + ETL load Redshift | `aws glue get-tables` and a direct Redshift query both show data |
| Matplotlib charts (2+) generated from real Redshift data | `.png` chart files exist and reflect actual query results |
| AI query layer: routing, NL-to-SQL, document response, all via Claude | All three query types tested, no `ValidationException` |
| Routing and synthesis implemented as a LangGraph state graph | `orchestrator.py` exists; all three routes (`sql`, `search`, `both`) tested individually via `orchestrator.invoke(...)` |
| SQL validator: 5+ tests pass including stacked query | Run test suite |
| Tokenomics logging + 10-query summary | Confirm log file has entries across all three call types |
| Both new harder queries answered correctly | Synthesis cites both sources; stress-test routes correctly through the LangGraph orchestrator, not just a plain Python if/else |
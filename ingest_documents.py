"""
ingest_documents.py -- loads the sample PDFs into the capstone's own
OpenSearch domain: S3 -> Textract (async) -> chunk -> embed -> index.

Adapted from the Document Ingestion Pipeline lab, with two deliberate changes:

1. Embeddings come from onnx_embed.get_embedding(), the exact function the
   deployed Retrieval Agent uses for queries -- so stored vectors and query
   vectors are produced by the same code path, not merely "equivalent" ones.
2. The index uses space_type "innerproduct", not "cosinesimil": FAISS's hnsw
   method doesn't support cosinesimil, and because onnx_embed's output is
   L2-normalized, inner product ranks identically to cosine similarity.

USAGE:
    python ingest_documents.py            # create index if missing, index all docs
    python ingest_documents.py --reset    # drop and recreate the index first
"""
import os
import sys
import time
import boto3
from dotenv import load_dotenv
from onnx_embed import get_embedding
from agents.retrieval_agent import get_opensearch_client
load_dotenv()

REGION = "us-east-1"
INDEX_NAME = os.getenv("OPENSEARCH_INDEX", "documents")
BUCKET = os.getenv("DOCS_BUCKET", "orchestrator-bucket-ah")
SAMPLE_DOCUMENTS = [
    "remote-work-policy.pdf",
    "retail-supply-chain-case-study.pdf",
    "q2-2026-client-report.pdf",
    "cloud-migration-playbook.pdf",
]

# Chunk size 200 words, overlap 40: the sample PDFs are each only a few
# hundred words, so the lab's 500-word chunks would collapse each document
# into a single chunk and retrieval would just return whole documents. 200
# keeps each chunk to roughly one topic (a policy section, a case-study
# result), and 40 words of overlap keeps a sentence that straddles a
# boundary intact in at least one chunk.
CHUNK_SIZE = 200
CHUNK_OVERLAP = 40

INDEX_BODY = {
    "settings": {"index.knn": True},
    "mappings": {
        "properties": {
            "content": {"type": "text"},
            "source": {"type": "keyword"},
            "chunk_number": {"type": "integer"},
            "embedding": {
                "type": "knn_vector",
                "dimension": 384,
                "method": {"name": "hnsw", "space_type": "innerproduct", "engine": "faiss"},
            },
        }
    },
}


def create_index(client, reset: bool = False):
    if reset and client.indices.exists(index=INDEX_NAME):
        client.indices.delete(index=INDEX_NAME)
        print(f"Deleted index '{INDEX_NAME}'")
    if not client.indices.exists(index=INDEX_NAME):
        client.indices.create(index=INDEX_NAME, body=INDEX_BODY)
        print(f"Created index '{INDEX_NAME}'")
    else:
        print(f"Index '{INDEX_NAME}' already exists")


def extract_text_from_s3_pdf(bucket: str, document_key: str) -> str:
    """Async Textract -- the sync API only supports single-page documents."""
    textract = boto3.client("textract", region_name=REGION)
    job_id = textract.start_document_text_detection(
        DocumentLocation={"S3Object": {"Bucket": bucket, "Name": document_key}}
    )["JobId"]

    for _ in range(60):  # ~2 minutes at 2s intervals
        response = textract.get_document_text_detection(JobId=job_id)
        status = response["JobStatus"]
        if status in ("SUCCEEDED", "FAILED"):
            break
        time.sleep(2)
    else:
        raise TimeoutError(f"Textract job {job_id} for {document_key} did not finish in time")

    if status == "FAILED":
        raise RuntimeError(f"Textract failed for {document_key}: {response.get('StatusMessage')}")

    lines = [b["Text"] for b in response["Blocks"] if b["BlockType"] == "LINE"]
    next_token = response.get("NextToken")
    while next_token:
        response = textract.get_document_text_detection(JobId=job_id, NextToken=next_token)
        lines.extend(b["Text"] for b in response["Blocks"] if b["BlockType"] == "LINE")
        next_token = response.get("NextToken")
    return "\n".join(lines)


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    words = text.split()
    chunks = []
    for i in range(0, len(words), chunk_size - overlap):
        chunk = " ".join(words[i:i + chunk_size])
        if chunk:
            chunks.append(chunk)
        if i + chunk_size >= len(words):
            break
    return chunks


def process_document(client, bucket: str, document_key: str) -> int:
    text = extract_text_from_s3_pdf(bucket, document_key)
    chunks = chunk_text(text)
    for i, chunk in enumerate(chunks):
        client.index(
            index=INDEX_NAME,
            id=f"{document_key}-{i}",
            body={
                "content": chunk,
                "embedding": get_embedding(chunk),
                "source": document_key,
                "chunk_number": i,
            },
        )
    print(f"  {document_key}: {len(text.split())} words -> {len(chunks)} chunk(s)")
    return len(chunks)


if __name__ == "__main__":
    client = get_opensearch_client()
    create_index(client, reset="--reset" in sys.argv)

    print(f"Indexing from s3://{BUCKET}/")
    total = sum(process_document(client, BUCKET, doc) for doc in SAMPLE_DOCUMENTS)

    client.indices.refresh(index=INDEX_NAME)
    print(f"\nIndexed {total} chunks. Index count: {client.count(index=INDEX_NAME)['count']}")

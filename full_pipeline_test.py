"""
Full end-to-end verification: chunk -> embed (Sentence Transformers) ->
index (Chroma, standing in for OpenSearch) -> retrieve -> generate answer
(Claude via Bedrock, correct inference-profile ARN pattern).

Run this directly in your own terminal, where you have both real internet
access (for the Sentence Transformers model download) and real AWS
credentials (for the Bedrock call). Neither of those is available in the
sandbox used to build this script, so this is the final confirmation step.
"""
import boto3
import json
import chromadb
from sentence_transformers import SentenceTransformer

# ── Embeddings (Sentence Transformers — Bedrock Titan/Cohere replacement) ──

_embed_model = None

def _get_model():
    global _embed_model
    if _embed_model is None:
        print("Loading Sentence Transformers model (downloads on first run)...")
        _embed_model = SentenceTransformer("all-MiniLM-L6-v2")
        print("Model loaded.")
    return _embed_model


def chunk_text(text: str, chunk_size: int = 40, overlap: int = 5) -> list[str]:
    words = text.split()
    chunks = []
    for i in range(0, len(words), chunk_size - overlap):
        chunk = " ".join(words[i:i + chunk_size])
        if chunk:
            chunks.append(chunk)
    return chunks


def get_collection():
    client = chromadb.PersistentClient(path="./chroma_verification")
    return client.get_or_create_collection("verification_documents")


def index_chunks(chunks: list[str], source: str):
    collection = get_collection()
    model = _get_model()
    embeddings = model.encode(chunks).tolist()
    ids = [f"{source}-{i}" for i in range(len(chunks))]
    metadatas = [{"source": source} for _ in chunks]
    collection.upsert(documents=chunks, embeddings=embeddings, ids=ids, metadatas=metadatas)
    print(f"Indexed {len(chunks)} chunk(s) from {source}")


def retrieve(query: str, top_k: int = 3) -> list[dict]:
    collection = get_collection()
    model = _get_model()
    embedding = model.encode([query]).tolist()
    results = collection.query(query_embeddings=embedding, n_results=top_k)
    return [
        {"content": doc, "source": meta["source"]}
        for doc, meta in zip(results["documents"][0], results["metadatas"][0])
    ]


# ── Generation (Claude via Bedrock, correct inference-profile ARN) ─────────

bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")


def get_model_id() -> str:
    account_id = boto3.client("sts").get_caller_identity()["Account"]
    return f"arn:aws:bedrock:us-east-1:{account_id}:inference-profile/us.anthropic.claude-sonnet-4-6"


def invoke_claude(prompt: str, max_tokens: int = 500) -> dict:
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}]
    }
    response = bedrock.invoke_model(modelId=get_model_id(), body=json.dumps(body))
    result = json.loads(response["body"].read())
    return {
        "text": result["content"][0]["text"],
        "input_tokens": result["usage"]["input_tokens"],
        "output_tokens": result["usage"]["output_tokens"]
    }


def generate_grounded_answer(question: str, chunks: list[dict]) -> str:
    context = "\n\n".join([f"[Source: {c['source']}]\n{c['content']}" for c in chunks])
    prompt = (
        "Answer the question using ONLY the context below. If the answer isn't "
        "present, say so. Cite the source you used.\n\n"
        f"CONTEXT:\n{context}\n\nQUESTION: {question}\n\nANSWER:"
    )
    result = invoke_claude(prompt)
    return result["text"]


# ── Full pipeline test ──────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 70)
    print("STEP 1: Chunk a sample document")
    print("=" * 70)

    sample_text = (
        "Our IT support policy requires critical tickets to be resolved within "
        "24 hours of being opened. High severity tickets should be resolved "
        "within 48 hours. Low severity tickets are addressed on a best-effort "
        "basis with no strict deadline. If a critical ticket is not resolved "
        "within 12 hours, it must be escalated to the on-call engineering lead. "
        "If a critical ticket remains unresolved at the 24-hour mark, it is "
        "automatically escalated to the department director."
    )

    chunks = chunk_text(sample_text)
    print(f"Chunked into {len(chunks)} piece(s)")

    print("\n" + "=" * 70)
    print("STEP 2: Generate embeddings and index (Sentence Transformers + Chroma)")
    print("=" * 70)
    index_chunks(chunks, "it_support_policy.txt")

    print("\n" + "=" * 70)
    print("STEP 3: Retrieve relevant chunks for a question")
    print("=" * 70)
    question = "What is the resolution time commitment for critical tickets?"
    results = retrieve(question)
    print(f"Question: {question}")
    print(f"Retrieved {len(results)} result(s):")
    for r in results:
        print(f"  [{r['source']}] {r['content'][:100]}...")

    print("\n" + "=" * 70)
    print("STEP 4: Generate a grounded answer using Claude via Bedrock")
    print("=" * 70)
    answer = generate_grounded_answer(question, results)
    print(f"Question: {question}")
    print(f"Answer: {answer}")

    print("\n" + "=" * 70)
    print("✅ FULL PIPELINE VERIFIED — if you see a real answer above, every")
    print("   piece (chunking, embeddings, indexing, retrieval, Bedrock/Claude")
    print("   generation) is confirmed working end-to-end in your environment.")
    print("=" * 70)

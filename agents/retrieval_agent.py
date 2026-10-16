"""
agents/retrieval_agent.py -- validated retrieval agent, wrapped to the
standard {success, answer, sources, metadata} interface from the Shared
Tool Registry lab.

Uses onnx_embed.get_embedding() instead of sentence-transformers directly --
see onnx_embed.py's docstring for why (PyTorch's size alone exceeds Lambda's
package limit) and for the verification that confirms this produces
numerically equivalent vectors to the original sentence-transformers model.
"""
import os
import boto3
from opensearchpy import OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth
from onnx_embed import get_embedding
from bedrock_client import invoke_claude
from tool_registry import get_system_prompt, RETRIEVAL_DECLINE
from dotenv import load_dotenv
load_dotenv()

REGION = "us-east-1"


def get_opensearch_client():
    host = os.environ["OPENSEARCH_ENDPOINT"]
    credentials = boto3.Session().get_credentials()
    auth = AWS4Auth(credentials.access_key, credentials.secret_key,
                     REGION, "es", session_token=credentials.token)
    return OpenSearch(
        hosts=[{"host": host, "port": 443}],
        http_auth=auth, use_ssl=True, verify_certs=True,
        connection_class=RequestsHttpConnection,
    )


def retrieve(question: str, top_k: int = 5) -> list[dict]:
    query_vector = get_embedding(question)
    client = get_opensearch_client()
    response = client.search(
        index="documents",
        body={"size": top_k, "query": {"knn": {"embedding": {"vector": query_vector, "k": top_k}}}}
    )
    return [
        {
            "content": hit["_source"]["content"],
            "source": hit["_source"]["source"],
            "chunk_number": hit["_source"].get("chunk_number"),
            "score": hit["_score"],
        }
        for hit in response["hits"]["hits"]
    ]


def generate_cited_answer(question: str, chunks: list[dict]) -> tuple[str, dict]:
    context = "\n\n".join([f"[Source {i+1}: {c['source']}]\n{c['content']}" for i, c in enumerate(chunks)])
    result = invoke_claude(
        f"EXCERPTS:\n{context}\n\nQUESTION: {question}",
        max_tokens=500,
        system=get_system_prompt("retrieval_agent", "retrieval_answer"),
        component="retrieval_answer",
    )
    return result["text"], {"input": result["input_tokens"], "output": result["output_tokens"]}


def check_grounding(answer: str, chunks: list[dict]) -> bool:
    import re
    # Claude cites as "Source 1", "Sources 1 & 3", "Sources 1, 2 and 4" --
    # pull every number out of each citation, not just the first.
    citations = re.findall(r"Sources?\s+(\d+(?:\s*(?:,|&|and)\s*\d+)*)", answer)
    cited_sources = [n for c in citations for n in re.findall(r"\d+", c)]
    if not cited_sources:
        return "cannot find" in answer.lower()
    return all(1 <= int(s) <= len(chunks) for s in cited_sources)


def run_retrieval_agent(question: str) -> dict:
    """
    Standardized tool interface: {success, answer, sources, metadata}.
    sources holds the distinct document names the answer drew on; per-chunk
    scores live in metadata. Never raises -- any failure comes back as
    success=False in the same shape.
    """
    try:
        chunks = retrieve(question)
        if not chunks:
            return {
                "success": False,
                "answer": "No relevant documents found.",
                "sources": [],
                "metadata": {"chunk_count": 0, "chunks": [], "grounded": False, "token_usage": {}},
            }

        answer, usage = generate_cited_answer(question, chunks)
    except Exception as e:
        return {
            "success": False,
            "answer": f"Retrieval agent error: {type(e).__name__}: {e}",
            "sources": [],
            "metadata": {"chunk_count": 0, "chunks": [], "grounded": False, "token_usage": {}},
        }

    # Claude declining is an honest non-answer, not a success -- the
    # synthesizer should not present it as a finding. Only an answer that IS
    # the decline counts; a partial answer that mentions the phrase still
    # answered something.
    found = not answer.strip().strip('"').lower().startswith(RETRIEVAL_DECLINE.lower())
    return {
        "success": found,
        "answer": answer,
        "sources": list(dict.fromkeys(c["source"] for c in chunks)),
        "metadata": {
            "chunk_count": len(chunks),
            "chunks": [
                {"source": c["source"], "chunk_number": c["chunk_number"], "score": round(c["score"], 4)}
                for c in chunks
            ],
            "grounded": check_grounding(answer, chunks),
            "token_usage": {"retrieval_answer": usage},
        },
    }

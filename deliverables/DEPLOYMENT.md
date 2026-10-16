# Step 8: Deploy to Lambda

## Requirement

| Requirement | Evidence | Passes when |
|---|---|---|
| Orchestrator deployed and invoked | `response.json` from `aws lambda invoke` for a question needing both sources, plus the matching CloudWatch log lines | Response shows `"route": "both"`, `"sql_validation": "PASSED"`, an answer combining both sources with attribution, and non-zero `token_usage` |

## Deployment

| Setting | Value |
|---|---|
| Function | `multi-agent-orchestrator-aaronhsu` |
| Runtime | `python3.12`, x86_64. Matches the Python that built the package |
| Package | One zip through S3 (`lambda-deploy-593470989370-aaronhsu`): 135MB zipped, 234MiB unzipped (limit 250MiB) |
| Embeddings | `onnx_embed.py` (ONNX Runtime), not PyTorch |
| Memory / timeout | 1024MB / 120s |
| IAM role | `orchestrator-lambda-role-aaronhsu`: `AWSLambdaBasicExecutionRole` plus an inline `bedrock:InvokeModel` policy |
| Environment | RDS (`orchestrator-db-aaronhsu`) and OpenSearch (`capstone-search-aaronhsu`) settings are environment variables. `.env` is not in the zip |

**Build, upload and deploy:**

```bash
./infra/deploy_lambda.sh aaronhsu
```

The script installs and trims the dependencies, then adds the code and model. It tests the imports in isolation, uploads to S3, and creates or updates the function. It finishes by waiting on `aws lambda wait function-updated-v2`.

## Evidence

**Invoke:**

```bash
aws lambda invoke --function-name multi-agent-orchestrator-aaronhsu \
  --payload file://deliverables/deployment/test_both.json \
  --cli-binary-format raw-in-base64-out --cli-read-timeout 150 response.json
```

The question asked was: *"What pitfalls does our cloud migration playbook warn about, and which of our active projects is a cloud migration?"*

| Check | Result |
|---|---|
| `"route": "both"` | ✅ `both` |
| `"sql_validation": "PASSED"` | ✅ `PASSED` |
| Answer combines both sources with attribution | ✅ Playbook pitfalls (Retrieval Agent: cloud-migration-playbook.pdf) plus the Cloud Migration Roadmap project for Evergreen Energy (SQL Agent) |
| Non-zero `token_usage` | ✅ 5 calls, 3,709 input and 842 output tokens, $0.0238 |

**Files:**
- [deployment/response.json](deployment/response.json): the full Lambda response
- [deployment/cloudwatch_logs.txt](deployment/cloudwatch_logs.txt): the log stream for that invocation
- [deployment/test_both.json](deployment/test_both.json): the request payload

**CloudWatch log lines:**

```
START RequestId: a297d5b7-a574-4eb4-8cfc-1857243f4583 Version: $LATEST
[LOG] query_start: {"trace_id": "16e817e4"}
[TOKENS] trace=16e817e4 classification      in=  583 out=   9 cost=$0.001884
[TOKENS] trace=16e817e4 sql_generation      in=  463 out=  76 cost=$0.002529
[TOKENS] trace=16e817e4 sql_interpretation  in=  302 out=  76 cost=$0.002046
[TOKENS] trace=16e817e4 retrieval_answer    in= 1629 out= 343 cost=$0.010032
[TOKENS] trace=16e817e4 synthesis           in=  732 out= 338 cost=$0.007266
[LOG] query_complete: {"trace_id": "16e817e4", "route": "both", "sql_validation": "PASSED", "retrieval_chunk_count": 5, ...}
END RequestId: a297d5b7-a574-4eb4-8cfc-1857243f4583
REPORT  Duration: 11706.56 ms  Billed Duration: 14538 ms  Memory Size: 1024 MB  Max Memory Used: 455 MB  Init Duration: 2831.41 ms
```

#!/usr/bin/env bash
# deploy_lambda.sh -- build the Lambda zip, upload it to S3, and update the
# function (or create it on first run). Run from unit-3-capstone/.
#
# Trim list differs from the Deployment Patterns lab: the current langchain_core
# imports langchain_protocol, and langgraph_sdk imports websockets and
# google.protobuf, so those are KEPT here. Removing them only fails at import
# time, which is why the build is tested in isolation before upload.
#
# USAGE: ./infra/deploy_lambda.sh [name]      (default name: aaronhsu)
set -euo pipefail

NAME="${1:-aaronhsu}"
REGION="us-east-1"
FUNCTION="multi-agent-orchestrator-${NAME}"
ROLE="orchestrator-lambda-role-${NAME}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET="lambda-deploy-${ACCOUNT_ID}-${NAME}"
ROOT="$(pwd)"
PKG="$ROOT/build/package"
ZIP="$ROOT/build/orchestrator_function.zip"

echo "== 1. Install dependencies"
rm -rf build && mkdir -p "$PKG"
venv/bin/pip install -q --no-compile -t "$PKG" \
  langgraph psycopg2-binary opensearch-py requests-aws4auth python-dotenv onnxruntime tokenizers numpy

echo "== 2. Trim (250MB unzipped limit)"
cd "$PKG"
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find . -maxdepth 1 -name "*.dist-info" -exec rm -rf {} +
find . -type d \( -name tests -o -name test \) -exec rm -rf {} + 2>/dev/null || true
rm -rf huggingface_hub hf_xet zstandard grpc bin
rm -rf numpy/f2py numpy/ma numpy/polynomial numpy/random/_examples numpy/matlib.py numpy/matlib.pyi
rm -rf onnxruntime/datasets onnxruntime/transformers onnxruntime/tools onnxruntime/quantization
find . -name "*.so*" -type f -exec strip --strip-unneeded {} + 2>/dev/null || true
cd "$ROOT"

echo "== 3. Add application code and model (never .env)"
cp bedrock_client.py onnx_embed.py orchestrator.py tokenomics_logger.py lambda_handler.py memory.py \
   tool_registry.py model.onnx tokenizer.json "$PKG/"
mkdir -p "$PKG/agents" && cp agents/__init__.py agents/sql_agent.py agents/sql_review.py \
   agents/retrieval_agent.py "$PKG/agents/"
SIZE=$(du -sb "$PKG" | cut -f1)
echo "   unzipped: $((SIZE / 1048576))MiB of 250MiB"
[[ $SIZE -lt 262144000 ]] || { echo "Package over 250MiB -- aborting"; exit 1; }

echo "== 4. Import test in isolation (Lambda provides boto3)"
venv/bin/pip install -q -t build/test-boto boto3
(cd /tmp && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PKG:$ROOT/build/test-boto" /usr/bin/python3 -S -c \
  "from orchestrator import build_orchestrator; build_orchestrator(); import lambda_handler; print('   imports + graph compile OK')")

echo "== 5. Zip and upload"
(cd "$PKG" && zip -qr9 "$ZIP" . -x "*__pycache__*")
aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null || aws s3 mb "s3://${BUCKET}" --region "$REGION"
aws s3 cp "$ZIP" "s3://${BUCKET}/orchestrator_function.zip" --only-show-errors

echo "== 6. Deploy ${FUNCTION}"
# Environment variables come from .env; written to a temp file and every
# command's output is --query-limited, so secrets never reach the terminal.
ENV_FILE=$(mktemp)
venv/bin/python -c "
import json, sys
from dotenv import dotenv_values
v = dotenv_values('.env')
keys = ['RDS_HOST','RDS_PORT','RDS_DATABASE','RDS_USER','RDS_PASSWORD','OPENSEARCH_ENDPOINT',
        'OPENSEARCH_INDEX','CONFIRMATION_SECRET']
json.dump({'Variables': {k: v[k] for k in keys}}, open(sys.argv[1], 'w'))" "$ENV_FILE"
if aws lambda get-function --function-name "$FUNCTION" >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$FUNCTION" \
    --s3-bucket "$BUCKET" --s3-key orchestrator_function.zip --query "LastUpdateStatus" --output text
  aws lambda wait function-updated-v2 --function-name "$FUNCTION"
  aws lambda update-function-configuration --function-name "$FUNCTION" \
    --environment "file://${ENV_FILE}" --query "LastUpdateStatus" --output text
else
  aws lambda create-function --function-name "$FUNCTION" --runtime python3.12 --architectures x86_64 \
    --handler lambda_handler.handler --code "S3Bucket=${BUCKET},S3Key=orchestrator_function.zip" \
    --role "arn:aws:iam::${ACCOUNT_ID}:role/${ROLE}" --timeout 120 --memory-size 1024 \
    --environment "file://${ENV_FILE}" --query "State" --output text
fi
rm -f "$ENV_FILE"
aws lambda wait function-updated-v2 --function-name "$FUNCTION"
echo "Deployed. Invoke with:"
echo "  aws lambda invoke --function-name $FUNCTION --payload file://deliverables/deployment/test_both.json \\"
echo "    --cli-binary-format raw-in-base64-out --cli-read-timeout 150 response.json"

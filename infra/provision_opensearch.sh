#!/usr/bin/env bash
# provision_opensearch.sh -- creates the capstone's own OpenSearch domain,
# separate from the domains used in the week's labs.
#
# Access policy allows BOTH the Lambda execution role (for the deployed
# orchestrator) and the IAM identity running this script (so ingestion and
# Step 1 checks can run from your own terminal). The lab domains only
# allowed the Lambda role, which is why local access to them failed.
#
# USAGE: ./infra/provision_opensearch.sh [name]     (default name: aaronhsu)
set -euo pipefail

NAME="${1:-aaronhsu}"
REGION="us-east-1"
DOMAIN_NAME="capstone-search-${NAME}"
ROLE_NAME="orchestrator-lambda-role-${NAME}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
CALLER_ARN=$(aws sts get-caller-identity --query Arn --output text)

if aws opensearch describe-domain --domain-name "$DOMAIN_NAME" --region "$REGION" >/dev/null 2>&1; then
  echo "Domain ${DOMAIN_NAME} already exists -- nothing to create."
else
  POLICY_FILE=$(mktemp)
  python3 - "$ACCOUNT_ID" "$ROLE_NAME" "$CALLER_ARN" "$DOMAIN_NAME" "$REGION" > "$POLICY_FILE" <<'PY'
import json, sys
account_id, role_name, caller_arn, domain, region = sys.argv[1:]
policy = {
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Principal": {"AWS": [f"arn:aws:iam::{account_id}:role/{role_name}", caller_arn]},
        "Action": "es:*",
        "Resource": f"arn:aws:es:{region}:{account_id}:domain/{domain}/*",
    }],
}
# sys.stdout.write, not print -- a trailing newline fails --access-policies validation
sys.stdout.write(json.dumps(policy))
PY

  aws opensearch create-domain \
    --region "$REGION" \
    --domain-name "$DOMAIN_NAME" \
    --engine-version OpenSearch_2.11 \
    --cluster-config InstanceType=t3.small.search,InstanceCount=1 \
    --ebs-options EBSEnabled=true,VolumeType=gp3,VolumeSize=10 \
    --access-policies "file://${POLICY_FILE}" \
    --domain-endpoint-options EnforceHTTPS=true \
    --query "DomainStatus.[DomainName,ARN]" --output text
  rm -f "$POLICY_FILE"
  echo "Creation started. This takes 15-20 minutes."
fi

echo "Check status with:"
echo "  aws opensearch describe-domain --domain-name ${DOMAIN_NAME} --query 'DomainStatus.[Processing,Endpoint]' --output text"
echo "When Processing is False and Endpoint is set, put the endpoint in .env as OPENSEARCH_ENDPOINT."

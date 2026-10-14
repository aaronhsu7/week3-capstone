#!/usr/bin/env bash
# aws-teardown-full.sh — end-of-week billable-resource sweep for the
# bootcamp's AWS sandbox account.
#
# Run this at the end of each week so nothing from that week's labs keeps
# billing between class sessions. It is broader than the capstone-specific
# scripts/teardown.sh — this one scans your whole account for the resource
# types the curriculum's labs create:
#
#   S3, Lambda, RDS, Bedrock (Knowledge Bases, Agents, Provisioned
#   Throughput, Custom Models), OpenSearch (classic domains + Serverless
#   collections), DynamoDB, CloudFront, and the classic EC2 silent-cost
#   trio (running instances, unassociated Elastic IPs, NAT Gateways).
#
# Scoped to us-east-1 only — that's the only region this Whizlabs sandbox
# grants access to. If your program ever moves to a sandbox that allows
# other regions, change REGION below (or turn it into a loop again).
#
# WHAT THIS SCRIPT DELIBERATELY DOES NOT TOUCH:
#   - IAM (users, roles, policies). Deleting the wrong role/policy can lock
#     you out of your own sandbox, and IAM resources themselves aren't
#     billable while just sitting there — the actual compute/storage they
#     grant access to is what costs money, and that's what this script
#     targets instead. If a lab has you create IAM roles you want to clean
#     up, do that by hand or via that lab's own teardown instructions.
#   - Anything not in the list above. "Any other billable resource" isn't a
#     thing a script can safely guess at — if a future week's lab
#     introduces a new service, add a section for it here rather than
#     assuming this script already covers it.
#
# Some of the calls below will return "access denied" depending on which
# permissions your sandbox grants that week — that's expected and handled:
# a denied or empty result is treated the same as "nothing to clean up
# here," and the script moves on rather than stopping.
#
# Compatibility note: deliberately written using only plain indexed arrays
# (no `declare -A`), so it runs on the ancient bash 3.2 that ships on macOS
# by default, not just on bash 4+/5+ (Linux, Homebrew bash).
#
# Usage:
#   ./aws-teardown-full.sh          # scan, show what it found, ask to confirm
#   ./aws-teardown-full.sh --yes    # skip the confirmation prompt

set -uo pipefail
# NOT `set -e` — a denied/unavailable service must not abort the whole scan.

REGION="us-east-1"

SKIP_CONFIRM=false
[[ "${1:-}" == "--yes" ]] && SKIP_CONFIRM=true

# Two parallel indexed arrays built during discovery: a human-readable
# description for the report/confirmation prompt, and the exact shell
# command that deletes that one resource. Nothing in DELETE_CMDS runs until
# the confirmation step passes.
REPORT=()
DELETE_CMDS=()

add_found() {
  # $1 = human description for the report, $2 = shell command to delete it
  REPORT+=("$1")
  DELETE_CMDS+=("$2")
}

echo "Scanning $REGION for billable resources..."
echo ""

# --- S3 (global bucket namespace, not region-scoped) ---
for b in $(aws s3api list-buckets --query 'Buckets[].Name' --output text 2>/dev/null); do
  add_found "S3 bucket: $b" "aws s3 rm s3://$b --recursive && aws s3api delete-bucket --bucket $b"
done

# --- CloudFront (global) — disable only; see note in the delete pass ---
for id in $(aws cloudfront list-distributions --query 'DistributionList.Items[].Id' --output text 2>/dev/null); do
  add_found "CloudFront distribution: $id (will be disabled, not deleted — see note below)" "disable_cloudfront $id"
done

# --- Everything else, scoped to $REGION ---

for fn in $(aws lambda list-functions --region "$REGION" --query 'Functions[].FunctionName' --output text 2>/dev/null); do
  add_found "Lambda: $fn" \
    "aws lambda delete-function-url-config --function-name $fn --region $REGION >/dev/null 2>&1; aws lambda delete-function --function-name $fn --region $REGION"
done

for db in $(aws rds describe-db-instances --region "$REGION" --query 'DBInstances[].DBInstanceIdentifier' --output text 2>/dev/null); do
  add_found "RDS instance: $db" \
    "aws rds delete-db-instance --db-instance-identifier $db --skip-final-snapshot --region $REGION"
done

for cluster in $(aws rds describe-db-clusters --region "$REGION" --query 'DBClusters[].DBClusterIdentifier' --output text 2>/dev/null); do
  add_found "RDS cluster: $cluster (delete its instances first, listed separately above)" \
    "aws rds delete-db-cluster --db-cluster-identifier $cluster --skip-final-snapshot --region $REGION"
done

for domain in $(aws opensearch list-domain-names --region "$REGION" --query 'DomainNames[].DomainName' --output text 2>/dev/null); do
  add_found "OpenSearch domain: $domain" \
    "aws opensearch delete-domain --domain-name $domain --region $REGION"
done

for coll in $(aws opensearchserverless list-collections --region "$REGION" --query 'collectionSummaries[].id' --output text 2>/dev/null); do
  add_found "OpenSearch Serverless collection: $coll" \
    "aws opensearchserverless delete-collection --id $coll --region $REGION"
done

for kb in $(aws bedrock-agent list-knowledge-bases --region "$REGION" --query 'knowledgeBaseSummaries[].knowledgeBaseId' --output text 2>/dev/null); do
  add_found "Bedrock Knowledge Base: $kb" \
    "aws bedrock-agent delete-knowledge-base --knowledge-base-id $kb --region $REGION"
done

for agent in $(aws bedrock-agent list-agents --region "$REGION" --query 'agentSummaries[].agentId' --output text 2>/dev/null); do
  add_found "Bedrock Agent: $agent" \
    "aws bedrock-agent delete-agent --agent-id $agent --skip-resource-in-use-check --region $REGION"
done

# Provisioned Throughput bills hourly whether or not it's being used — the
# single most important Bedrock item to catch.
for arn in $(aws bedrock list-provisioned-model-throughputs --region "$REGION" --query 'provisionedModelSummaries[].provisionedModelArn' --output text 2>/dev/null); do
  add_found "Bedrock Provisioned Throughput: $arn" \
    "aws bedrock delete-provisioned-model-throughput --provisioned-model-id $arn --region $REGION"
done

for arn in $(aws bedrock list-custom-models --region "$REGION" --query 'modelSummaries[].modelArn' --output text 2>/dev/null); do
  add_found "Bedrock Custom Model: $arn" \
    "aws bedrock delete-custom-model --model-identifier $arn --region $REGION"
done

for table in $(aws dynamodb list-tables --region "$REGION" --query 'TableNames' --output text 2>/dev/null); do
  add_found "DynamoDB table: $table" \
    "aws dynamodb delete-table --table-name $table --region $REGION"
done

for instance in $(aws ec2 describe-instances --region "$REGION" \
      --filters "Name=instance-state-name,Values=running,stopped,stopping,pending" \
      --query 'Reservations[].Instances[].InstanceId' --output text 2>/dev/null); do
  add_found "EC2 instance: $instance" \
    "aws ec2 terminate-instances --instance-ids $instance --region $REGION"
done

# Only unassociated EIPs — one attached to a running instance isn't the
# silent-cost case this is meant to catch, and releasing it out from under
# a running instance would be actively harmful.
for eip in $(aws ec2 describe-addresses --region "$REGION" \
      --query 'Addresses[?AssociationId==`null`].AllocationId' --output text 2>/dev/null); do
  add_found "Unassociated Elastic IP: $eip" \
    "aws ec2 release-address --allocation-id $eip --region $REGION"
done

for nat in $(aws ec2 describe-nat-gateways --region "$REGION" \
      --filter "Name=state,Values=available" \
      --query 'NatGateways[].NatGatewayId' --output text 2>/dev/null); do
  add_found "NAT Gateway: $nat (~\$0.045/hour just for existing — one of the most expensive idle resources)" \
    "aws ec2 delete-nat-gateway --nat-gateway-id $nat --region $REGION"
done

# ---------------------------------------------------------------------------
# Report + confirm
# ---------------------------------------------------------------------------

echo "Found ${#REPORT[@]} resource(s):"
if [[ "${#REPORT[@]}" -eq 0 ]]; then
  echo "  (nothing — you're clean, or everything found so far was already torn down)"
  echo ""
  echo "Done."
  exit 0
fi

printf '  - %s\n' "${REPORT[@]}"
echo ""
echo "IAM (users/roles/policies) is intentionally NOT touched by this script — see the comment at the top of the file for why."
echo ""

if ! $SKIP_CONFIRM; then
  read -r -p "Delete everything listed above? [y/N] " reply
  case "$reply" in
    [yY] | [yY][eE][sS]) ;;
    *)
      echo "Aborted — nothing was deleted."
      exit 0
      ;;
  esac
fi

# ---------------------------------------------------------------------------
# Delete pass — just runs the commands built up during discovery.
# ---------------------------------------------------------------------------

disable_cloudfront() {
  local id="$1"
  echo "Disabling distribution $id..."
  local etag config disabled_config
  etag=$(aws cloudfront get-distribution-config --id "$id" --query 'ETag' --output text 2>/dev/null)
  config=$(aws cloudfront get-distribution-config --id "$id" --query 'DistributionConfig' --output json 2>/dev/null)
  if [[ -n "$etag" && -n "$config" ]]; then
    disabled_config=$(echo "$config" | python3 -c "import json,sys; d=json.load(sys.stdin); d['Enabled']=False; print(json.dumps(d))")
    echo "$disabled_config" > "/tmp/cf-${id}.json"
    if aws cloudfront update-distribution --id "$id" --if-match "$etag" --distribution-config "file:///tmp/cf-${id}.json" >/dev/null 2>&1; then
      echo "  disabled. CloudFront distributions take ~15 min to fully deploy a change and can't be"
      echo "  deleted until they finish — come back later and run:"
      echo "    aws cloudfront delete-distribution --id $id --if-match <new-etag-from-get-distribution-config>"
    else
      echo "  couldn't disable $id — check manually in the console."
    fi
    rm -f "/tmp/cf-${id}.json"
  fi
}

echo ""
for i in "${!DELETE_CMDS[@]}"; do
  echo "-- ${REPORT[$i]}"
  eval "${DELETE_CMDS[$i]}" 2>&1 || echo "  (that one failed — see error above, or it may already be gone)"
done

echo ""
echo "Teardown pass complete."
echo ""
echo "A few things worth knowing:"
echo "  - RDS/OpenSearch/Bedrock deletes are async — they take minutes to actually"
echo "    finish. Re-run this script later to confirm they're really gone."
echo "  - RDS clusters need their member instances deleted first — if a cluster"
echo "    delete failed above, re-run once its instances finish deleting."
echo "  - CloudFront distributions were disabled, not deleted — you'll need to"
echo "    come back once they finish deploying (see the note printed above)."
echo "  - IAM roles/policies from this week's labs were NOT touched. Check the IAM"
echo "    console by hand if you want those cleaned up too."

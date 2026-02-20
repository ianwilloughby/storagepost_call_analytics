#!/bin/bash
# Enable DynamoDB Streams on existing tables before deploying
# Usage: bash scripts/enable-streams.sh
#   Env vars (optional):
#     CALLS_TABLE_NAME     — defaults to "calls"
#     SCORECARDS_TABLE_NAME — defaults to "scorecards"
#     AWS_DEFAULT_REGION   — defaults to "us-east-1"
set -e

CALLS_TABLE=${CALLS_TABLE_NAME:-calls}
SCORECARDS_TABLE=${SCORECARDS_TABLE_NAME:-scorecards}
REGION=${AWS_DEFAULT_REGION:-us-east-1}

echo "Enabling DynamoDB Streams on: $CALLS_TABLE"
aws dynamodb update-table \
  --table-name "$CALLS_TABLE" \
  --stream-specification StreamEnabled=true,StreamViewType=NEW_AND_OLD_IMAGES \
  --region "$REGION" \
  --no-cli-pager

echo "Enabling DynamoDB Streams on: $SCORECARDS_TABLE"
aws dynamodb update-table \
  --table-name "$SCORECARDS_TABLE" \
  --stream-specification StreamEnabled=true,StreamViewType=NEW_AND_OLD_IMAGES \
  --region "$REGION" \
  --no-cli-pager

echo "Streams enabled. Waiting 10s for propagation..."
sleep 10

# Verify
echo "Calls table stream status:"
aws dynamodb describe-table --table-name "$CALLS_TABLE" \
  --query "Table.StreamSpecification" --region "$REGION"

echo "Scorecards table stream status:"
aws dynamodb describe-table --table-name "$SCORECARDS_TABLE" \
  --query "Table.StreamSpecification" --region "$REGION"

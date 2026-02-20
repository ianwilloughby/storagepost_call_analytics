#!/bin/bash
# Trigger Glue crawlers and wait for completion
# Usage: bash scripts/run-crawlers.sh
#   Env vars (optional):
#     TF_VAR_project_name  — defaults to "post-call-analytics"
#     AWS_DEFAULT_REGION   — defaults to "us-east-1"
set -e

PROJECT=${TF_VAR_project_name:-post-call-analytics}
REGION=${AWS_DEFAULT_REGION:-us-east-1}

for CRAWLER in "${PROJECT}-calls-crawler" "${PROJECT}-scorecards-crawler"; do
  echo "Starting crawler: $CRAWLER"
  aws glue start-crawler --name "$CRAWLER" --region "$REGION" --no-cli-pager

  echo "Waiting for $CRAWLER to complete..."
  while true; do
    STATE=$(aws glue get-crawler --name "$CRAWLER" --region "$REGION" \
      --query "Crawler.State" --output text)
    echo "  State: $STATE"
    if [ "$STATE" = "READY" ]; then
      break
    fi
    sleep 10
  done
  echo "$CRAWLER complete."
done

echo "Running MSCK REPAIR TABLE to load partitions..."
WORKGROUP="${PROJECT}"
DB="post_call_analytics"

for TABLE in calls scorecards; do
  EXEC_ID=$(aws athena start-query-execution \
    --query-string "MSCK REPAIR TABLE ${DB}.${TABLE}" \
    --work-group "$WORKGROUP" \
    --region "$REGION" \
    --query "QueryExecutionId" --output text)
  echo "  Repair $TABLE: $EXEC_ID — waiting..."
  sleep 5
done

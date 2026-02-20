# API Backend — Deployment & Operations (Terraform)

## Deployment Order

Terraform handles dependency ordering automatically, but you must complete these prerequisites manually before `terraform apply`:

```bash
# ── 0. Start in the dev container ──────────────────────────────────────────
# VS Code → Cmd+Shift+P → "Dev Containers: Reopen in Container"

# ── 1. Enable DynamoDB Streams on existing tables ──────────────────────────
bash scripts/enable-streams.sh

# ── 2. Build Lambda packages ───────────────────────────────────────────────
bash lambdas/build.sh
# Produces: terraform/lambda_packages/{stream_processor,athena_executor,api_handler}.zip

# ── 3. Build the frontend ──────────────────────────────────────────────────
cd frontend
cp .env.example .env
# Leave .env blank for now — Terraform outputs will fill these after first apply

npm install && npm run build
cd ..

# ── 4. Configure Terraform ─────────────────────────────────────────────────
cd terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars:
#   calls_table_name           = "calls"        ← your actual table name
#   scorecards_table_name      = "scorecards"   ← your actual table name
#   existing_transcribe_bucket = "your-bucket"  ← your Transcribe output bucket
#   aws_region                 = "us-east-1"

# ── 5. Initialize and apply ────────────────────────────────────────────────
terraform init
terraform plan   # Review what will be created
terraform apply  # Type 'yes' to confirm

# ── 6. Capture outputs and rebuild frontend with real values ───────────────
API_URL=$(terraform output -raw api_url)
POOL_ID=$(terraform output -raw cognito_user_pool_id)
CLIENT_ID=$(terraform output -raw cognito_client_id)

cat > ../frontend/.env <<EOF
VITE_API_URL=$API_URL
VITE_COGNITO_USER_POOL_ID=$POOL_ID
VITE_COGNITO_CLIENT_ID=$CLIENT_ID
EOF

cd ../frontend && npm run build

# ── 7. Redeploy frontend with real env values ──────────────────────────────
cd ../terraform && terraform apply   # Uploads new dist/ to S3

# ── 8. Run historical backfill ─────────────────────────────────────────────
cd ../lambdas/stream_processor
python backfill.py --table calls
python backfill.py --table scorecards

# ── 9. Run Glue crawlers to build Athena schema ────────────────────────────
bash ../../scripts/run-crawlers.sh

# ── 10. Create first user ──────────────────────────────────────────────────
bash ../../scripts/create-user.sh admin@yourcompany.com
```

---

## What Terraform Creates vs. What Already Exists

| Resource | Action | Notes |
|----------|--------|-------|
| DynamoDB `calls` table | `data` source — read-only reference | Never modified |
| DynamoDB `scorecards` table | `data` source — read-only reference | Never modified |
| Existing S3 Transcribe bucket | `data` source — read-only reference | Never modified |
| S3 analytics bucket | Created | Holds Parquet data |
| S3 Athena results bucket | Created | Auto-deleted after 7 days |
| S3 frontend bucket | Created | Private, CloudFront OAC only |
| Glue database + crawlers | Created | `post_call_analytics` |
| Athena workgroup | Created | 1 GB scan limit enforced |
| Lambda: stream_processor | Created | DynamoDB stream → S3 |
| Lambda: athena_executor | Created | Bedrock action group |
| Lambda: api_handler | Created | API Gateway handler |
| Bedrock Agent + alias | Created | Claude 3.5 Sonnet |
| Cognito User Pool | Created | Invite-only |
| API Gateway | Created | Cognito auth on all routes |
| CloudFront distribution | Created | HTTPS, OAC to S3 |

---

## Testing

### Test 1: Verify stream processor is running

```bash
# Check event source mapping status
aws lambda list-event-source-mappings \
  --function-name post-call-analytics-stream-processor \
  --query "EventSourceMappings[*].{Source:EventSourceArn,State:State,LastResult:LastProcessingResult}"

# Tail Lambda logs
aws logs tail /aws/lambda/post-call-analytics-stream-processor --follow
```

### Test 2: Verify Athena data is populated

```bash
# Check S3 for data files
aws s3 ls s3://$(terraform -chdir=terraform output -raw analytics_bucket_name)/calls/ --recursive | head -10

# Run a test query
EXEC_ID=$(aws athena start-query-execution \
  --query-string "SELECT direction, COUNT(*) as cnt FROM post_call_analytics.calls GROUP BY direction" \
  --work-group post-call-analytics \
  --query-execution-context Database=post_call_analytics \
  --query "QueryExecutionId" --output text)

sleep 5

aws athena get-query-results --query-execution-id "$EXEC_ID" \
  --query "ResultSet.Rows[*].Data[*].VarCharValue" --output table
```

### Test 3: Test Athena Executor Lambda directly

```bash
aws lambda invoke \
  --function-name post-call-analytics-athena-executor \
  --payload '{
    "actionGroup": "AthenaQueryExecutor",
    "function": "execute_sql_query",
    "parameters": [
      {"name": "sql_query", "value": "SELECT COUNT(*) as total_calls FROM post_call_analytics.calls"}
    ]
  }' \
  --cli-binary-format raw-in-base64-out \
  /tmp/athena-test-response.json && cat /tmp/athena-test-response.json | jq .
```

### Test 4: Test the full API (requires a Cognito token)

The easiest way to get a token during testing is from the browser's DevTools after logging into the frontend — copy the `id_token` from the Cognito callback or from `localStorage` in the app.

```bash
TOKEN="eyJraWQiOi..."   # paste your id token here

API_URL=$(cd terraform && terraform output -raw api_url)

curl -X POST "${API_URL}chat" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question": "How many total calls are in the database?"}' | jq .
```

---

## Updating Infrastructure

### Updating a Lambda function

```bash
# Rebuild just the changed Lambda
bash lambdas/build.sh

# Apply — Terraform detects the zip hash changed and redeploys
cd terraform && terraform apply -target=module.data_pipeline.aws_lambda_function.stream_processor
```

### Updating the Bedrock Agent instruction

```bash
# Edit the file
nano lambdas/athena_executor/agent_instruction.txt

# Apply — Terraform will update the agent resource
cd terraform && terraform apply -target=module.bedrock_agent.aws_bedrockagent_agent.main

# Prepare the agent (required after instruction changes)
AGENT_ID=$(terraform output -raw bedrock_agent_id)
aws bedrock-agent prepare-agent --agent-id "$AGENT_ID"
```

### Updating the frontend

```bash
cd frontend && npm run build
cd ../terraform && terraform apply -target=module.frontend
```

---

## Destroying Infrastructure

Terraform can cleanly remove everything it created. The existing DynamoDB tables and Transcribe bucket are `data` sources and will **not** be touched.

```bash
cd terraform
terraform destroy

# Verify existing tables are untouched
aws dynamodb describe-table --table-name calls --query "Table.TableStatus"
```

---

## Monitoring

### CloudWatch Dashboards (create manually or via Terraform)

Key metrics to watch:

| Metric | Threshold | Action |
|--------|-----------|--------|
| Stream Processor Errors | > 5 in 5 min | Check DLQ, fix and redrive |
| Athena Executor Duration p95 | > 45s | Query may be too broad — check agent instruction |
| API Gateway 5xx | > 2% of requests | Check API Handler logs |
| Bedrock Agent throttling | Any | Check Bedrock service limits |

### Dead Letter Queue

Stream processing failures land in the SQS DLQ. Monitor it:

```bash
DLQ_URL=$(aws sqs get-queue-url \
  --queue-name post-call-analytics-stream-processor-dlq \
  --query "QueueUrl" --output text)

aws sqs get-queue-attributes \
  --queue-url "$DLQ_URL" \
  --attribute-names ApproximateNumberOfMessages
```

To redrive failed messages after fixing the Lambda:

```bash
# Start DLQ redrive
aws sqs start-message-move-task \
  --source-arn "$(aws sqs get-queue-attributes --queue-url $DLQ_URL --attribute-names QueueArn --query Attributes.QueueArn --output text)"
```

---

## Cost Estimate

At moderate volume (~10,000 calls/month):

| Service | Estimated Monthly Cost |
|---------|----------------------|
| Athena | $0.50–$2.00 |
| S3 (all buckets) | $0.25 |
| Lambda (all functions) | < $1.00 |
| Bedrock (Claude 3.5 Sonnet) | $1–5 per 100 queries |
| Cognito | Free (< 50K MAUs) |
| CloudFront | < $1.00 |
| Glue crawlers | ~$0.03/day |
| **Total** | **~$5–15/month** |

---

## Troubleshooting

**`terraform apply` fails on Bedrock Agent resource:**
The `aws_bedrockagent_agent` resource requires Bedrock to be enabled in your account and region. Check: AWS Console → Amazon Bedrock → Model access → Enable Claude 3.5 Sonnet.

**Stream Processor not triggering after `terraform apply`:**
Streams must be enabled before the event source mapping is created. Run `bash scripts/enable-streams.sh` first, then re-run `terraform apply`.

**Athena queries return zero results after backfill:**
Run the Glue crawlers (`bash scripts/run-crawlers.sh`) and then check for partitions: `SHOW PARTITIONS post_call_analytics.calls` in the Athena console.

**CloudFront returns 403:**
The S3 bucket policy OAC config requires CloudFront to fully deploy first (can take ~5 minutes). Wait and retry.

**Frontend auth loop / redirect issues:**
Make sure `VITE_COGNITO_USER_POOL_ID` and `VITE_COGNITO_CLIENT_ID` in `.env` match the Terraform outputs exactly, and that you rebuilt the frontend after setting these.
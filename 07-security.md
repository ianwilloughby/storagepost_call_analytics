# Security Controls

## Authentication & Authorization

### Cognito User Pool (Identity)
- **No self-service signup** — `self_sign_up_enabled=False`. Users can only be added by an admin via the console or CLI.
- **Email-only login** — passwords are never stored in plaintext (Cognito uses SRP)
- **MFA optional** — TOTP (authenticator app) can be enabled per user; recommended for all users
- **Password policy** — 12+ chars, uppercase, digits, symbols required
- **Token validity** — ID and Access tokens expire after 8 hours; Refresh tokens after 30 days
- **Account recovery** — email only, no SMS (reduces SIM-swap risk)

**Adding a user (admin CLI command):**
```bash
# Create user (they receive an email with temp password)
aws cognito-idp admin-create-user \
  --user-pool-id us-east-1_XXXXXXXXX \
  --username analyst@yourcompany.com \
  --user-attributes Name=email,Value=analyst@yourcompany.com Name=email_verified,Value=true \
  --temporary-password "TempPass123!" \
  --message-action SUPPRESS

# Optionally assign to a group (for future RBAC)
aws cognito-idp admin-add-user-to-group \
  --user-pool-id us-east-1_XXXXXXXXX \
  --username analyst@yourcompany.com \
  --group-name analysts
```

### API Gateway Authorization
- All routes use `CognitoUserPoolsAuthorizer`
- Every request must include: `Authorization: Bearer <id_token>`
- API Gateway validates the JWT signature against Cognito's JWKS endpoint automatically
- No valid token → 401 before the request reaches any Lambda
- Throttling: 50 RPS sustained, 20 RPS burst per stage — prevents abuse

### Frontend (React)
- Hosted on CloudFront — no direct S3 access
- CloudFront uses Origin Access Control (OAC) — S3 bucket has no public access
- Amplify handles token refresh automatically — tokens are stored in memory (not localStorage) when using `signIn` with SRP

---

## Data Security

### S3 Buckets
All buckets have:
- `BlockPublicAccess.BLOCK_ALL` — no ACL overrides possible
- Server-side encryption with S3-managed keys (SSE-S3)
- No bucket policies granting public read

| Bucket | Access |
|--------|--------|
| Analytics data (Parquet) | Stream Processor Lambda (write), Athena/Glue (read via IAM role) |
| Athena results | Athena Executor Lambda (read/write), auto-deleted after 7 days |
| Frontend (React) | CloudFront OAC only |

### DynamoDB
- Existing tables — no changes to access patterns
- Stream Processor Lambda has `dynamodb:GetRecords` on the stream ARN only, not the table itself
- No direct DynamoDB access from the analytics platform — all reads go through Athena

### Athena
- Dedicated workgroup `post-call-analytics` with enforced configuration
- Query results encrypted with SSE-S3
- **1 GB per-query scan limit** — prevents runaway scans from poorly formed AI-generated queries
- Athena Executor Lambda is the only identity that can query this workgroup

---

## IAM Least Privilege

Each Lambda has a unique role with only the permissions it needs:

| Lambda | Permissions |
|--------|-------------|
| Stream Processor | DynamoDB stream read, S3 PutObject (analytics bucket), Transcribe GetJob, S3 GetObject (transcribe bucket) |
| Athena Executor | Athena query execution (one workgroup), Glue read (catalog), S3 read/write (analytics + results buckets) |
| API Handler | `bedrock:InvokeAgent` for one specific agent+alias ARN |

No Lambda has `*` on any resource or service.

---

## Network Security

This architecture is intentionally serverless with no VPC required. All services are managed:
- Lambda → Bedrock: AWS private network via service endpoint
- Lambda → Athena: AWS private network
- Lambda → S3: AWS private network via VPC endpoint (if in VPC) or internet gateway (not needed here)
- CloudFront → S3: AWS internal network via OAC

If you want to add a VPC for defense-in-depth (not required but possible):
- Deploy Lambdas into a private subnet
- Add VPC endpoints for S3, DynamoDB, Bedrock, Athena
- No NAT Gateway needed if all endpoints are configured

---

## Logging & Audit

| Service | What's Logged |
|---------|---------------|
| API Gateway | All requests including auth status, response codes, latency |
| Lambda (all) | CloudWatch Logs, 30-day retention |
| Athena | All queries logged to CloudWatch via workgroup config |
| Bedrock | Model invocations logged (enable in Bedrock console → Model invocation logging) |
| CloudTrail | All AWS API calls (recommended: enable org-level trail) |

**Enable Bedrock invocation logging:**
```bash
aws bedrock put-model-invocation-logging-configuration \
  --logging-config '{"cloudWatchConfig":{"logGroupName":"/aws/bedrock/invocations","roleArn":"arn:aws:iam::ACCOUNT:role/BedrockLoggingRole"}}'
```

---

## Secrets Management

- No hardcoded credentials anywhere
- All config stored in SSM Parameter Store (Standard tier — free)
- Cognito client secret is not used (SRP auth doesn't require it)
- Platform28 API key (in existing system) — if needed, store in Secrets Manager, not SSM

---

## Security Checklist Before Going Live

- [ ] Confirm all S3 buckets block public access
- [ ] Confirm no Lambda roles have `*` on any resource
- [ ] Enable CloudTrail in the account if not already enabled
- [ ] Enable Bedrock model invocation logging
- [ ] Enable MFA for all Cognito users
- [ ] Restrict CloudFront CORS to your actual domain (replace `CORS.ALL_ORIGINS` with your CF domain)
- [ ] Set Cognito app client allowed callback URLs to your CloudFront URL
- [ ] Run `aws iam generate-credential-report` and review any unused IAM keys
- [ ] Enable AWS Config rules: `s3-bucket-public-read-prohibited`, `restricted-ssh`, `iam-no-inline-policy-check`
- [ ] Review Athena query history weekly for any unexpected queries

---

## RBAC (Phase 2 — Optional)

For multi-tenant use (e.g., clients seeing only their own data), add Cognito groups and filter queries:

1. Add a `tenant_id` custom attribute to Cognito users
2. API Handler Lambda reads `tenant_id` from the JWT claims
3. Inject `AND tenant_id = {user_tenant_id}` into every Athena query via the Bedrock Agent system prompt or a Lambda-level query wrapper
4. This prevents any user from querying another tenant's data even if they craft a question that would generate such a query

```python
# In api_handler/handler.py, extract from JWT:
tenant_id = event.get("requestContext", {}).get("authorizer", {}).get("claims", {}).get("custom:tenant_id", "")

# Pass to agent as session state or prepend to the question:
question_with_context = f"[User tenant_id: {tenant_id}. Always filter queries to this tenant_id.] {question}"
```

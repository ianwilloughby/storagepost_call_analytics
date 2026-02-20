# Post-Call Analytics Platform — Claude Code Instructions

## Project Purpose
Build a serverless AWS analytics platform on top of existing DynamoDB tables (`calls` and `scorecards`).
The platform syncs data to S3 Parquet, queries it with Athena, and exposes a natural language interface
via a Bedrock Agent backed by a React frontend protected by Cognito.

## Architecture Reference
Read `01-architecture.md` before writing any code. It contains the authoritative data flow,
schema definitions, and design decisions.

## Agent Team Task Breakdown

When building or modifying this project, spawn the following subagents to work in parallel:

### Agent 1 — Terraform Infrastructure
**Scope:** Everything in `terraform/`
**Reads first:** `02-infrastructure.md`
**Creates:**
- `terraform/main.tf`
- `terraform/variables.tf`
- `terraform/outputs.tf`
- `terraform/terraform.tfvars.example`
- `terraform/modules/data_pipeline/` (main.tf, variables.tf, outputs.tf)
- `terraform/modules/bedrock_agent/` (main.tf, variables.tf, outputs.tf)
- `terraform/modules/api/` (main.tf, variables.tf, outputs.tf)
- `terraform/modules/frontend/` (main.tf, variables.tf, outputs.tf)

**Key constraints:**
- Use `data` sources for existing DynamoDB tables — never `resource` blocks for them
- All S3 buckets must have `block_public_access` fully enabled
- All IAM policies must be least privilege — no `*` on Action or Resource
- Use `filebase64sha256(var.lambda_package_path)` for Lambda source_code_hash
- Terraform version >= 1.7, AWS provider ~> 5.40

### Agent 2 — Lambda Functions
**Scope:** Everything in `lambdas/`
**Reads first:** `03-data-pipeline.md`, `04-bedrock-agent.md`
**Creates:**
- `lambdas/stream_processor/handler.py`
- `lambdas/stream_processor/backfill.py`
- `lambdas/stream_processor/requirements.txt`
- `lambdas/athena_executor/handler.py`
- `lambdas/athena_executor/agent_instruction.txt`
- `lambdas/athena_executor/requirements.txt`
- `lambdas/api_handler/handler.py`
- `lambdas/api_handler/requirements.txt`
- `lambdas/build.sh`

**Key constraints:**
- Python 3.12, boto3 only (no external HTTP libraries)
- Stream processor must handle both `calls` and `scorecards` table events from one function
- Athena executor must reject any query not starting with SELECT or WITH
- All handlers must have structured logging with `logger.info/error`

### Agent 3 — Frontend
**Scope:** Everything in `frontend/`
**Reads first:** `06-frontend.md`
**Creates:**
- `frontend/src/main.tsx`
- `frontend/src/App.tsx`
- `frontend/src/aws-config.ts`
- `frontend/src/pages/LoginPage.tsx`
- `frontend/src/pages/ChatPage.tsx`
- `frontend/src/pages/ReportsPage.tsx`
- `frontend/src/components/Layout.tsx`
- `frontend/package.json`
- `frontend/vite.config.ts`
- `frontend/tailwind.config.js`
- `frontend/tsconfig.json`
- `frontend/.env.example`

**Key constraints:**
- React 18 + TypeScript + Vite
- AWS Amplify v6 for Cognito auth
- Tailwind CSS for styling (no other CSS libraries)
- Tokens must never be stored in localStorage — use Amplify's memory storage
- All API calls must include the `Authorization: Bearer <id_token>` header

### Agent 4 — Dev Container & Scripts
**Scope:** `.devcontainer/`, `scripts/`
**Reads first:** `00-devcontainer.md`
**Creates:**
- `.devcontainer/devcontainer.json`
- `.devcontainer/Dockerfile`
- `.devcontainer/post-create.sh`
- `scripts/enable-streams.sh`
- `scripts/run-crawlers.sh`
- `scripts/create-user.sh`

**Key constraints:**
- Terraform 1.7.5 in Dockerfile (pinned version)
- Mount `~/.aws` from host into container
- post-create.sh must be idempotent (safe to run multiple times)
- All scripts must have `set -e` and usage examples in comments

## Agent Coordination Rules

1. **Agent 1 runs in parallel with Agents 2, 3, 4.** None of them depend on each other at build time.
2. **Agent 1 must not start `terraform apply`** — it only creates files. Deployment is a human step.
3. **Agents must not modify files outside their scope.** Agent 2 does not touch `terraform/`. Agent 3 does not touch `lambdas/`.
4. **If an agent finds a conflict between docs and these instructions, the docs take precedence.**

## File Dependencies (Build Order for Humans)

After all agents complete:
```
1. scripts/enable-streams.sh          ← run first (requires existing AWS tables)
2. lambdas/build.sh                   ← package Lambdas
3. frontend: npm install && npm run build
4. terraform init && terraform plan && terraform apply
5. Rebuild frontend with Terraform outputs → terraform apply again
6. lambdas/stream_processor/backfill.py --table calls
7. lambdas/stream_processor/backfill.py --table scorecards
8. scripts/run-crawlers.sh
9. scripts/create-user.sh admin@yourcompany.com
```

## Security Non-Negotiables

- Never hardcode AWS credentials, account IDs, or secrets in any file
- All S3 bucket resources must include explicit `aws_s3_bucket_public_access_block` with all four flags `true`
- Cognito must have `allow_admin_create_user_only = true` (no self-signup)
- API Gateway must use `COGNITO_USER_POOLS` authorization on every method except OPTIONS
- Athena executor Lambda must validate SELECT-only queries before calling Athena

## Testing After Build

Each agent should create a `tests/` folder in its scope:
- `lambdas/tests/test_stream_processor.py` — unit tests for the flatten functions
- `lambdas/tests/test_athena_executor.py` — unit tests for SQL validation and response formatting
- `frontend/src/__tests__/` — component tests for LoginPage and ChatPage

## Common Patterns

**Logging standard:**
```python
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.info(f"Processing {len(records)} records from {table_name}")
```

**Terraform resource naming:**
All resources use the pattern: `${var.project_name}-{resource-description}`
Example: `post-call-analytics-stream-processor`, `post-call-analytics-athena-executor`

**Lambda env vars:**
Terraform injects all config as Lambda environment variables. Do not call SSM at runtime.
# Claude Code Agent Team

## Overview

Claude Code's agent team feature lets Claude spawn parallel subagents to work on independent parts of the codebase simultaneously. For this project, that means the Terraform modules, Lambda functions, and frontend can all be built concurrently rather than sequentially — cutting build time significantly.

The agent team is coordinated by a `CLAUDE.md` file at the project root. When you open the project in Claude Code and give it a high-level instruction, it reads `CLAUDE.md` first to understand the project structure, task breakdown, and which agents should own which files.

---

## `CLAUDE.md` (Place at project root)

```markdown
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
1. scripts/enable-streams.sh     ← run first (requires existing AWS tables)
2. lambdas/build.sh              ← package Lambdas
3. frontend: npm install && npm run build
4. terraform init && terraform plan && terraform apply
5. Rebuild frontend with Terraform outputs → terraform apply again
6. lambdas/stream_processor/backfill.py
7. scripts/run-crawlers.sh
8. scripts/create-user.sh
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

**Reading SSM Parameters in Lambda (don't use — use env vars instead):**
Terraform injects all config as Lambda env vars. Do not call SSM at runtime.

**Logging standard:**
```python
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.info(f"Processing {len(records)} records from {table_name}")
```

**Terraform resource naming:**
All resources use the pattern: `${var.project_name}-{resource-description}`
Example: `post-call-analytics-stream-processor`, `post-call-analytics-athena-executor`
```

---

## How to Use the Agent Team in Claude Code

### Starting a full build

Open the project in Claude Code and type:

```
Build the post-call analytics platform from the markdown specs. Use the agent team 
to work on the Terraform infrastructure, Lambda functions, frontend, and dev container 
files in parallel. Follow the task breakdown in CLAUDE.md exactly.
```

Claude Code will:
1. Read `CLAUDE.md`
2. Read the relevant spec files for each domain
3. Spawn 4 parallel subagents
4. Each subagent creates its files independently
5. Report back when all agents complete

### Starting a partial build

```
Using the agent team, rebuild only the Terraform modules and the Lambda functions.
Do not touch the frontend or dev container files.
```

### Adding a new report type

```
Add a new report type called "queue_performance" to the analytics platform. 
It should show call volume, average duration, and resolution rate by queue name.
Update the API handler, the agent instruction, and the frontend ReportsPage.
```

### Debugging a specific component

```
The stream processor Lambda is not correctly inferring answer_type for short calls.
Look at lambdas/stream_processor/handler.py and fix the infer_answer_type function.
Write a unit test that covers the edge cases.
```

---

## settings.json for Claude Code

If you have `settings.json` in your Claude Code configuration with agent team enabled, you're already set. The `CLAUDE.md` file does the rest. There's nothing else to configure — Claude Code picks up `CLAUDE.md` automatically when it exists at the project root.

Your existing `settings.json` entry should look something like:

```json
{
  "claudeCode": {
    "agentTeam": {
      "enabled": true,
      "maxParallelAgents": 4
    }
  }
}
```

If `maxParallelAgents` isn't set, Claude Code defaults to whatever your plan allows. The 4 agents in this project's CLAUDE.md map exactly to the 4 parallel work streams.

---

## Agent Team vs. Single Agent — When to Use Which

| Task | Use Agent Team? |
|------|----------------|
| Full initial build | Yes — 4 streams, all independent |
| Adding a new Lambda | No — single agent, one file |
| Refactoring Terraform modules | Yes — modules are independent |
| Fixing a bug in one Lambda | No — focused, single agent |
| Adding a new frontend page + API endpoint + Terraform output | Yes — 3 independent streams |
| Updating the Bedrock agent instruction | No — single file, no parallelism benefit |

The rule of thumb: if the task touches 2+ independent directories (`terraform/`, `lambdas/`, `frontend/`, `scripts/`), the agent team pays off. If it's scoped to one area, a single agent is faster to coordinate.
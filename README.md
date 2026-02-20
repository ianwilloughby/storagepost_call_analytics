# Post-Call Analytics Platform

A serverless AWS platform that enables natural language querying and reporting over contact center call data stored in DynamoDB and S3 transcripts.

## What Changed From Original Spec

| Area | Original | This Version |
|------|----------|--------------|
| IaC | AWS CDK (Python) | Terraform |
| Dev Environment | Local toolchain | Docker Dev Container |
| DynamoDB | Creates new tables | References your existing tables |
| Build Orchestration | Manual | Claude Code Agent Team |

## Project Structure

```
post-call-analytics/
├── .devcontainer/
│   ├── devcontainer.json        ← VS Code / Claude Code dev container config
│   └── Dockerfile               ← Container with Terraform, Python, Node, AWS CLI
│
├── CLAUDE.md                    ← Claude Code agent team instructions
│
├── README.md                    ← You are here
├── 00-devcontainer.md           ← Dev container setup guide
├── 01-architecture.md           ← Architecture and design decisions (unchanged)
├── 02-infrastructure.md         ← Terraform modules and configuration
├── 03-data-pipeline.md          ← Stream Processor Lambda (unchanged)
├── 04-bedrock-agent.md          ← Bedrock Agent + Action Group (unchanged)
├── 05-api-backend.md            ← Deployment, testing, monitoring
├── 06-frontend.md               ← React chat UI (unchanged)
├── 07-security.md               ← Security controls (unchanged)
├── 08-claude-agents.md          ← Claude Code agent team setup
│
├── terraform/
│   ├── main.tf                  ← Root module, provider config
│   ├── variables.tf             ← Input variables (table names, bucket names, etc.)
│   ├── outputs.tf               ← Exported values (API URL, Cognito IDs, etc.)
│   ├── terraform.tfvars.example ← Template — copy to terraform.tfvars
│   └── modules/
│       ├── data_pipeline/       ← S3, Glue, Athena, Stream Processor Lambda
│       ├── bedrock_agent/       ← Bedrock Agent, Action Group Lambda
│       ├── api/                 ← Cognito, API Gateway, API Handler Lambda
│       └── frontend/            ← S3, CloudFront
│
├── lambdas/
│   ├── stream_processor/        ← DynamoDB Streams → S3 flattener
│   ├── athena_executor/         ← Bedrock Agent action group
│   ├── api_handler/             ← API Gateway handler
│   └── build.sh                 ← Zips all lambdas for Terraform deployment
│
└── frontend/
    ├── src/
    └── package.json
```

## Prerequisites

All tools are provided in the dev container — you do not need to install anything locally except Docker and VS Code (or use GitHub Codespaces).

- Docker Desktop
- VS Code with Dev Containers extension (`ms-vscode-remote.remote-containers`)
- AWS credentials available locally (mounted into the container)

## Quick Start

```bash
# 1. Open the project in the dev container
# In VS Code: Cmd+Shift+P → "Dev Containers: Reopen in Container"
# Or with Claude Code: it will detect .devcontainer/ automatically

# 2. Verify tools (all pre-installed in container)
terraform --version   # >= 1.7
python --version      # 3.12
aws --version         # 2.x
node --version        # 20.x

# 3. Configure AWS credentials (if not already mounted)
aws configure

# 4. Enable DynamoDB Streams on your existing tables (one-time)
bash scripts/enable-streams.sh

# 5. Configure Terraform variables
cd terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your existing table names and bucket

# 6. Build Lambda packages
cd ../lambdas && bash build.sh

# 7. Deploy infrastructure
cd ../terraform
terraform init
terraform plan
terraform apply

# 8. Run historical backfill
cd ../lambdas/stream_processor
python backfill.py --table calls
python backfill.py --table scorecards

# 9. Trigger Glue crawlers
bash ../scripts/run-crawlers.sh

# 10. Build and deploy frontend
cd ../../frontend
npm install && npm run build
cd ../terraform && terraform apply  # Uploads dist/ to S3

# 11. Create first user
bash ../scripts/create-user.sh admin@yourcompany.com
```

## Existing Resources (Do Not Recreate)

Terraform uses `data` sources to reference these — it will never modify or delete them:

| Resource | Name | Notes |
|----------|------|-------|
| DynamoDB table | `calls` | Partition key: `callId` |
| DynamoDB table | `scorecards` | Partition key: `guid` |
| S3 bucket | *(your transcribe bucket)* | Set in `terraform.tfvars` |

## Environment Variables / Terraform Variables

All configuration lives in `terraform/terraform.tfvars` (gitignored):

| Variable | Description |
|----------|-------------|
| `aws_region` | AWS region (e.g., `us-east-1`) |
| `calls_table_name` | Your existing DynamoDB calls table name |
| `scorecards_table_name` | Your existing DynamoDB scorecards table name |
| `existing_transcribe_bucket` | S3 bucket where Transcribe output lives |
| `project_name` | Prefix for all created resources (default: `post-call-analytics`) |
| `environment` | `dev` or `prod` |

## Claude Code Agent Team

This project is configured for Claude Code's agent team feature. See `08-claude-agents.md` and `CLAUDE.md` for details. Claude Code will automatically use parallel subagents to build the Terraform modules, Lambda functions, and frontend concurrently.

To start a build with the agent team:
```
Tell Claude Code: "Build the post-call analytics platform following CLAUDE.md"
```
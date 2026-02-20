# Dev Container Setup

## Overview

The project uses a Docker dev container so every developer (and Claude Code) has an identical environment with all tools pre-installed. No local Terraform, Python, or Node.js installation needed.

## Files to Create

### `.devcontainer/devcontainer.json`

```json
{
  "name": "Post-Call Analytics",
  "build": {
    "dockerfile": "Dockerfile",
    "context": ".."
  },

  "features": {
    "ghcr.io/devcontainers/features/aws-cli:1": {
      "version": "latest"
    },
    "ghcr.io/devcontainers/features/python:1": {
      "version": "3.12"
    },
    "ghcr.io/devcontainers/features/node:1": {
      "version": "20"
    }
  },

  "mounts": [
    // Mount local AWS credentials into the container
    "source=${localEnv:HOME}/.aws,target=/root/.aws,type=bind,consistency=cached"
  ],

  "forwardPorts": [3000],

  "postCreateCommand": "bash .devcontainer/post-create.sh",

  "customizations": {
    "vscode": {
      "extensions": [
        "hashicorp.terraform",
        "ms-python.python",
        "amazonwebservices.aws-toolkit-vscode",
        "ms-vscode.vscode-json"
      ],
      "settings": {
        "terminal.integrated.defaultProfile.linux": "bash",
        "python.defaultInterpreterPath": "/usr/local/bin/python3",
        "editor.formatOnSave": true,
        "[terraform]": {
          "editor.defaultFormatter": "hashicorp.terraform",
          "editor.formatOnSave": true
        }
      }
    }
  },

  "remoteUser": "root"
}
```

### `.devcontainer/Dockerfile`

```dockerfile
FROM mcr.microsoft.com/devcontainers/base:ubuntu-22.04

# ── System packages ────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y \
    curl \
    unzip \
    zip \
    git \
    jq \
    make \
    wget \
    software-properties-common \
    && rm -rf /var/lib/apt/lists/*

# ── Terraform ──────────────────────────────────────────────────────────────────
ARG TERRAFORM_VERSION=1.7.5
RUN wget -q https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip \
    && unzip terraform_${TERRAFORM_VERSION}_linux_amd64.zip \
    && mv terraform /usr/local/bin/ \
    && rm terraform_${TERRAFORM_VERSION}_linux_amd64.zip

# Verify
RUN terraform --version

# ── Python dependencies ────────────────────────────────────────────────────────
COPY lambdas/stream_processor/requirements.txt /tmp/req-stream.txt
COPY lambdas/athena_executor/requirements.txt /tmp/req-athena.txt
COPY lambdas/api_handler/requirements.txt /tmp/req-api.txt

RUN pip3 install --no-cache-dir \
    -r /tmp/req-stream.txt \
    -r /tmp/req-athena.txt \
    -r /tmp/req-api.txt \
    boto3 \
    pytest \
    black \
    ruff

# ── Node.js global packages ────────────────────────────────────────────────────
# Node is installed via devcontainer feature; add frontend tools here
RUN npm install -g vite typescript

# ── Helper scripts in PATH ────────────────────────────────────────────────────
COPY scripts/ /usr/local/bin/project-scripts/
RUN chmod +x /usr/local/bin/project-scripts/*.sh

# ── Working directory ─────────────────────────────────────────────────────────
WORKDIR /workspace
```

### `.devcontainer/post-create.sh`

This runs once after the container is created (installs frontend deps, etc.):

```bash
#!/bin/bash
set -e

echo "=== Post-create setup ==="

# Install frontend dependencies
if [ -f "/workspace/frontend/package.json" ]; then
  echo "Installing frontend dependencies..."
  cd /workspace/frontend && npm install
fi

# Install Python lambda dependencies locally for IDE support
echo "Installing Python dependencies..."
pip3 install -r /workspace/lambdas/stream_processor/requirements.txt
pip3 install -r /workspace/lambdas/athena_executor/requirements.txt
pip3 install -r /workspace/lambdas/api_handler/requirements.txt

# Initialize Terraform (won't fail if no .tfvars yet)
echo "Initializing Terraform..."
if [ -f "/workspace/terraform/main.tf" ]; then
  cd /workspace/terraform && terraform init || true
fi

echo "=== Dev container ready ==="
echo ""
echo "Next steps:"
echo "  1. Copy terraform/terraform.tfvars.example → terraform/terraform.tfvars and fill in values"
echo "  2. Run: bash scripts/enable-streams.sh"
echo "  3. Run: bash lambdas/build.sh"
echo "  4. Run: cd terraform && terraform apply"
```

---

## Supporting Scripts

These go in `scripts/` and are added to the container PATH.

### `scripts/enable-streams.sh`

```bash
#!/bin/bash
# Enable DynamoDB Streams on existing tables before deploying
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
```

### `scripts/run-crawlers.sh`

```bash
#!/bin/bash
# Trigger Glue crawlers and wait for completion
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
```

### `scripts/create-user.sh`

```bash
#!/bin/bash
# Create a Cognito user. Usage: create-user.sh user@example.com
set -e

EMAIL=${1:?"Usage: create-user.sh <email>"}
REGION=${AWS_DEFAULT_REGION:-us-east-1}

# Read user pool ID from Terraform output
POOL_ID=$(cd /workspace/terraform && terraform output -raw cognito_user_pool_id 2>/dev/null)

if [ -z "$POOL_ID" ]; then
  echo "Error: Could not read cognito_user_pool_id from Terraform output."
  echo "Make sure terraform apply has been run."
  exit 1
fi

echo "Creating user $EMAIL in pool $POOL_ID..."
aws cognito-idp admin-create-user \
  --user-pool-id "$POOL_ID" \
  --username "$EMAIL" \
  --user-attributes \
    Name=email,Value="$EMAIL" \
    Name=email_verified,Value=true \
  --temporary-password "TempPass123!" \
  --message-action SUPPRESS \
  --region "$REGION" \
  --no-cli-pager

echo "User created. They will be prompted to set a new password on first login."
echo "Temporary password: TempPass123!"
```

### `lambdas/build.sh`

```bash
#!/bin/bash
# Package each Lambda function into a zip for Terraform deployment
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="$SCRIPT_DIR/../terraform/lambda_packages"
mkdir -p "$OUTPUT_DIR"

FUNCTIONS=("stream_processor" "athena_executor" "api_handler")

for FUNC in "${FUNCTIONS[@]}"; do
  echo "Building $FUNC..."
  FUNC_DIR="$SCRIPT_DIR/$FUNC"
  TMP_DIR=$(mktemp -d)

  # Copy source files
  cp -r "$FUNC_DIR"/*.py "$TMP_DIR/" 2>/dev/null || true

  # Install dependencies
  if [ -f "$FUNC_DIR/requirements.txt" ]; then
    pip3 install -r "$FUNC_DIR/requirements.txt" -t "$TMP_DIR/" --quiet
  fi

  # Zip
  OUTPUT="$OUTPUT_DIR/${FUNC}.zip"
  (cd "$TMP_DIR" && zip -r "$OUTPUT" . -q)
  rm -rf "$TMP_DIR"

  echo "  → $OUTPUT ($(du -sh "$OUTPUT" | cut -f1))"
done

echo "All Lambda packages built in $OUTPUT_DIR/"
```

---

## Using GitHub Codespaces

If you prefer Codespaces over local Docker:

1. Push the repo to GitHub
2. Click **Code → Codespaces → Create codespace on main**
3. The `.devcontainer/` config is detected automatically
4. Mount AWS credentials via Codespaces secrets:
   - Add `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION` as Codespace secrets
   - Update `devcontainer.json` to use env vars instead of the `~/.aws` mount:
   ```json
   "remoteEnv": {
     "AWS_ACCESS_KEY_ID": "${localEnv:AWS_ACCESS_KEY_ID}",
     "AWS_SECRET_ACCESS_KEY": "${localEnv:AWS_SECRET_ACCESS_KEY}",
     "AWS_DEFAULT_REGION": "${localEnv:AWS_DEFAULT_REGION}"
   }
   ```
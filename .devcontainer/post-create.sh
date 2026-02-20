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
pip3 install boto3 pytest black ruff

# Install Node.js global packages
echo "Installing global Node.js packages..."
npm install -g vite typescript

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

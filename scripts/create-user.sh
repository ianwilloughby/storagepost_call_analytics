#!/bin/bash
# Create a Cognito user. Usage: bash scripts/create-user.sh user@example.com
#   Env vars (optional):
#     AWS_DEFAULT_REGION — defaults to "us-east-1"
set -e

EMAIL=${1:?"Usage: create-user.sh <email>"}
REGION=${AWS_DEFAULT_REGION:-us-east-1}

# Read user pool ID from Terraform output
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POOL_ID=$(cd "$SCRIPT_DIR/../terraform" && terraform output -raw cognito_user_pool_id 2>/dev/null)

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

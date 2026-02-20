# Infrastructure — Terraform

## Overview

All infrastructure is defined as Terraform HCL organized into four modules. Existing DynamoDB tables are referenced with `data` sources and never modified or destroyed by Terraform.

## Directory Structure

```
terraform/
├── main.tf                  ← Root: provider, backend, module calls
├── variables.tf             ← Input variables
├── outputs.tf               ← Exported values
├── terraform.tfvars.example ← Template (copy → terraform.tfvars, gitignore it)
├── lambda_packages/         ← Built by lambdas/build.sh (gitignored)
└── modules/
    ├── data_pipeline/
    │   ├── main.tf          ← S3 buckets, Glue, Athena, Stream Processor Lambda
    │   ├── variables.tf
    │   └── outputs.tf
    ├── bedrock_agent/
    │   ├── main.tf          ← Bedrock Agent, Athena Executor Lambda
    │   ├── variables.tf
    │   └── outputs.tf
    ├── api/
    │   ├── main.tf          ← Cognito, API Gateway, API Handler Lambda
    │   ├── variables.tf
    │   └── outputs.tf
    └── frontend/
        ├── main.tf          ← S3 + CloudFront
        ├── variables.tf
        └── outputs.tf
```

---

## Root Module

### `terraform/main.tf`

```hcl
terraform {
  required_version = ">= 1.7.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  # Recommended: use S3 backend for team use
  # backend "s3" {
  #   bucket         = "your-terraform-state-bucket"
  #   key            = "post-call-analytics/terraform.tfstate"
  #   region         = "us-east-1"
  #   encrypt        = true
  #   dynamodb_table = "terraform-state-lock"
  # }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# ── Reference existing DynamoDB tables (data sources — never modified) ─────────

data "aws_dynamodb_table" "calls" {
  name = var.calls_table_name
}

data "aws_dynamodb_table" "scorecards" {
  name = var.scorecards_table_name
}

data "aws_s3_bucket" "existing_transcribe" {
  bucket = var.existing_transcribe_bucket
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# ── Modules ───────────────────────────────────────────────────────────────────

module "data_pipeline" {
  source = "./modules/data_pipeline"

  project_name                = var.project_name
  environment                 = var.environment
  aws_account_id              = data.aws_caller_identity.current.account_id
  aws_region                  = var.aws_region
  calls_table_arn             = data.aws_dynamodb_table.calls.arn
  calls_stream_arn            = data.aws_dynamodb_table.calls.stream_arn
  scorecards_table_arn        = data.aws_dynamodb_table.scorecards.arn
  scorecards_stream_arn       = data.aws_dynamodb_table.scorecards.stream_arn
  existing_transcribe_bucket  = var.existing_transcribe_bucket
  existing_transcribe_bucket_arn = data.aws_s3_bucket.existing_transcribe.arn
  lambda_package_path         = "${path.root}/lambda_packages/stream_processor.zip"
}

module "bedrock_agent" {
  source = "./modules/bedrock_agent"

  project_name            = var.project_name
  environment             = var.environment
  aws_account_id          = data.aws_caller_identity.current.account_id
  aws_region              = var.aws_region
  analytics_bucket_arn    = module.data_pipeline.analytics_bucket_arn
  analytics_bucket_name   = module.data_pipeline.analytics_bucket_name
  athena_results_bucket_arn  = module.data_pipeline.athena_results_bucket_arn
  athena_results_bucket_name = module.data_pipeline.athena_results_bucket_name
  athena_workgroup_name   = module.data_pipeline.athena_workgroup_name
  lambda_package_path     = "${path.root}/lambda_packages/athena_executor.zip"
}

module "api" {
  source = "./modules/api"

  project_name       = var.project_name
  environment        = var.environment
  aws_account_id     = data.aws_caller_identity.current.account_id
  aws_region         = var.aws_region
  bedrock_agent_id   = module.bedrock_agent.agent_id
  bedrock_alias_id   = module.bedrock_agent.agent_alias_id
  lambda_package_path = "${path.root}/lambda_packages/api_handler.zip"
}

module "frontend" {
  source = "./modules/frontend"

  project_name    = var.project_name
  environment     = var.environment
  api_url         = module.api.api_url
  frontend_dist   = "${path.root}/../frontend/dist"
}
```

### `terraform/variables.tf`

```hcl
variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Prefix for all created resources"
  type        = string
  default     = "post-call-analytics"
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "prod"
  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "environment must be 'dev' or 'prod'."
  }
}

variable "calls_table_name" {
  description = "Name of your existing DynamoDB calls table"
  type        = string
}

variable "scorecards_table_name" {
  description = "Name of your existing DynamoDB scorecards table"
  type        = string
}

variable "existing_transcribe_bucket" {
  description = "Name of S3 bucket where Amazon Transcribe outputs are stored"
  type        = string
}
```

### `terraform/outputs.tf`

```hcl
output "analytics_bucket_name" {
  description = "S3 bucket containing Parquet analytics data"
  value       = module.data_pipeline.analytics_bucket_name
}

output "api_url" {
  description = "API Gateway base URL"
  value       = module.api.api_url
}

output "cloudfront_url" {
  description = "CloudFront distribution URL for the frontend"
  value       = module.frontend.cloudfront_url
}

output "cognito_user_pool_id" {
  description = "Cognito User Pool ID"
  value       = module.api.cognito_user_pool_id
}

output "cognito_client_id" {
  description = "Cognito App Client ID"
  value       = module.api.cognito_client_id
}

output "bedrock_agent_id" {
  description = "Bedrock Agent ID"
  value       = module.bedrock_agent.agent_id
}
```

### `terraform/terraform.tfvars.example`

```hcl
aws_region                 = "us-east-1"
project_name               = "post-call-analytics"
environment                = "prod"
calls_table_name           = "calls"
scorecards_table_name      = "scorecards"
existing_transcribe_bucket = "your-transcribe-output-bucket-name"
```

---

## Module: data_pipeline

### `terraform/modules/data_pipeline/main.tf`

```hcl
# ── S3: Analytics Data Bucket ─────────────────────────────────────────────────
resource "aws_s3_bucket" "analytics" {
  bucket = "${var.project_name}-data-${var.aws_account_id}-${var.aws_region}"
}

resource "aws_s3_bucket_server_side_encryption_configuration" "analytics" {
  bucket = aws_s3_bucket.analytics.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "analytics" {
  bucket                  = aws_s3_bucket.analytics.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "analytics" {
  bucket = aws_s3_bucket.analytics.id
  rule {
    id     = "intelligent-tiering"
    status = "Enabled"
    transition {
      days          = 90
      storage_class = "INTELLIGENT_TIERING"
    }
  }
}

# ── S3: Athena Results Bucket ─────────────────────────────────────────────────
resource "aws_s3_bucket" "athena_results" {
  bucket = "${var.project_name}-athena-results-${var.aws_account_id}-${var.aws_region}"
}

resource "aws_s3_bucket_server_side_encryption_configuration" "athena_results" {
  bucket = aws_s3_bucket.athena_results.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "athena_results" {
  bucket                  = aws_s3_bucket.athena_results.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "athena_results" {
  bucket = aws_s3_bucket.athena_results.id
  rule {
    id     = "delete-old-results"
    status = "Enabled"
    expiration { days = 7 }
  }
}

# ── Glue Database ─────────────────────────────────────────────────────────────
resource "aws_glue_catalog_database" "main" {
  name        = "post_call_analytics"
  description = "Post-call analytics data from DynamoDB"
}

# ── Glue Crawler IAM ──────────────────────────────────────────────────────────
resource "aws_iam_role" "glue_crawler" {
  name = "${var.project_name}-glue-crawler-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "glue.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "glue_service" {
  role       = aws_iam_role.glue_crawler.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole"
}

resource "aws_iam_role_policy" "glue_s3" {
  name = "glue-s3-access"
  role = aws_iam_role.glue_crawler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject", "s3:ListBucket"]
      Resource = [aws_s3_bucket.analytics.arn, "${aws_s3_bucket.analytics.arn}/*"]
    }]
  })
}

# ── Glue Crawlers ─────────────────────────────────────────────────────────────
resource "aws_glue_crawler" "calls" {
  name          = "${var.project_name}-calls-crawler"
  role          = aws_iam_role.glue_crawler.arn
  database_name = aws_glue_catalog_database.main.name
  schedule      = "cron(0 * * * ? *)"   # Hourly

  s3_target {
    path = "s3://${aws_s3_bucket.analytics.bucket}/calls/"
  }

  configuration = jsonencode({
    Version = 1.0
    CrawlerOutput = {
      Partitions = { AddOrUpdateBehavior = "InheritFromTable" }
      Tables     = { AddOrUpdateBehavior = "MergeNewColumns" }
    }
  })
}

resource "aws_glue_crawler" "scorecards" {
  name          = "${var.project_name}-scorecards-crawler"
  role          = aws_iam_role.glue_crawler.arn
  database_name = aws_glue_catalog_database.main.name
  schedule      = "cron(0 * * * ? *)"

  s3_target {
    path = "s3://${aws_s3_bucket.analytics.bucket}/scorecards/"
  }
}

# ── Athena Workgroup ──────────────────────────────────────────────────────────
resource "aws_athena_workgroup" "main" {
  name        = var.project_name
  description = "Post-call analytics workgroup"
  force_destroy = true

  configuration {
    enforce_workgroup_configuration    = true
    bytes_scanned_cutoff_per_query     = 1073741824  # 1 GB

    result_configuration {
      output_location = "s3://${aws_s3_bucket.athena_results.bucket}/results/"
      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }
  }
}

# ── Lambda IAM: Stream Processor ──────────────────────────────────────────────
resource "aws_iam_role" "stream_processor" {
  name = "${var.project_name}-stream-processor-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "stream_processor_basic" {
  role       = aws_iam_role.stream_processor.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "stream_processor" {
  name = "stream-processor-policy"
  role = aws_iam_role.stream_processor.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetRecords",
          "dynamodb:GetShardIterator",
          "dynamodb:DescribeStream",
          "dynamodb:ListStreams"
        ]
        Resource = [var.calls_stream_arn, var.scorecards_stream_arn]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${aws_s3_bucket.analytics.arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["transcribe:GetTranscriptionJob"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${var.existing_transcribe_bucket_arn}/*"
      }
    ]
  })
}

# ── Lambda: Stream Processor ──────────────────────────────────────────────────
resource "aws_lambda_function" "stream_processor" {
  function_name = "${var.project_name}-stream-processor"
  role          = aws_iam_role.stream_processor.arn
  handler       = "handler.lambda_handler"
  runtime       = "python3.12"
  timeout       = 300
  memory_size   = 512
  filename      = var.lambda_package_path
  source_code_hash = filebase64sha256(var.lambda_package_path)

  environment {
    variables = {
      ANALYTICS_BUCKET   = aws_s3_bucket.analytics.bucket
      TRANSCRIBE_BUCKET  = var.existing_transcribe_bucket
    }
  }
}

resource "aws_cloudwatch_log_group" "stream_processor" {
  name              = "/aws/lambda/${aws_lambda_function.stream_processor.function_name}"
  retention_in_days = 30
}

# ── Event Source Mappings: DynamoDB Streams → Lambda ─────────────────────────
resource "aws_lambda_event_source_mapping" "calls_stream" {
  event_source_arn          = var.calls_stream_arn
  function_name             = aws_lambda_function.stream_processor.arn
  starting_position         = "TRIM_HORIZON"
  batch_size                = 100
  bisect_batch_on_function_error = true

  destination_config {
    on_failure {
      destination_arn = aws_sqs_queue.dlq.arn
    }
  }
}

resource "aws_lambda_event_source_mapping" "scorecards_stream" {
  event_source_arn          = var.scorecards_stream_arn
  function_name             = aws_lambda_function.stream_processor.arn
  starting_position         = "TRIM_HORIZON"
  batch_size                = 100
  bisect_batch_on_function_error = true

  destination_config {
    on_failure {
      destination_arn = aws_sqs_queue.dlq.arn
    }
  }
}

# ── Dead Letter Queue ─────────────────────────────────────────────────────────
resource "aws_sqs_queue" "dlq" {
  name                      = "${var.project_name}-stream-processor-dlq"
  message_retention_seconds = 1209600  # 14 days
}

resource "aws_iam_role_policy" "stream_processor_sqs" {
  name = "stream-processor-sqs"
  role = aws_iam_role.stream_processor.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["sqs:SendMessage"]
      Resource = aws_sqs_queue.dlq.arn
    }]
  })
}
```

### `terraform/modules/data_pipeline/variables.tf`

```hcl
variable "project_name"                  { type = string }
variable "environment"                   { type = string }
variable "aws_account_id"                { type = string }
variable "aws_region"                    { type = string }
variable "calls_table_arn"               { type = string }
variable "calls_stream_arn"              { type = string }
variable "scorecards_table_arn"          { type = string }
variable "scorecards_stream_arn"         { type = string }
variable "existing_transcribe_bucket"    { type = string }
variable "existing_transcribe_bucket_arn" { type = string }
variable "lambda_package_path"           { type = string }
```

### `terraform/modules/data_pipeline/outputs.tf`

```hcl
output "analytics_bucket_name"       { value = aws_s3_bucket.analytics.bucket }
output "analytics_bucket_arn"        { value = aws_s3_bucket.analytics.arn }
output "athena_results_bucket_name"  { value = aws_s3_bucket.athena_results.bucket }
output "athena_results_bucket_arn"   { value = aws_s3_bucket.athena_results.arn }
output "athena_workgroup_name"       { value = aws_athena_workgroup.main.name }
output "glue_database_name"          { value = aws_glue_catalog_database.main.name }
```

---

## Module: bedrock_agent

### `terraform/modules/bedrock_agent/main.tf`

```hcl
# ── Lambda IAM: Athena Executor ───────────────────────────────────────────────
resource "aws_iam_role" "athena_executor" {
  name = "${var.project_name}-athena-executor-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "athena_executor_basic" {
  role       = aws_iam_role.athena_executor.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "athena_executor" {
  name = "athena-executor-policy"
  role = aws_iam_role.athena_executor.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "athena:StartQueryExecution",
          "athena:GetQueryExecution",
          "athena:GetQueryResults",
          "athena:StopQueryExecution"
        ]
        Resource = "arn:aws:athena:${var.aws_region}:${var.aws_account_id}:workgroup/${var.athena_workgroup_name}"
      },
      {
        Effect   = "Allow"
        Action   = ["glue:GetTable", "glue:GetTables", "glue:GetDatabase", "glue:GetPartitions"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
        Resource = [
          var.analytics_bucket_arn,
          "${var.analytics_bucket_arn}/*",
          var.athena_results_bucket_arn,
          "${var.athena_results_bucket_arn}/*"
        ]
      }
    ]
  })
}

# ── Lambda: Athena Executor ───────────────────────────────────────────────────
resource "aws_lambda_function" "athena_executor" {
  function_name    = "${var.project_name}-athena-executor"
  role             = aws_iam_role.athena_executor.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  filename         = var.lambda_package_path
  source_code_hash = filebase64sha256(var.lambda_package_path)

  environment {
    variables = {
      ATHENA_WORKGROUP     = var.athena_workgroup_name
      ATHENA_DATABASE      = "post_call_analytics"
      ATHENA_RESULTS_BUCKET = var.athena_results_bucket_name
    }
  }
}

resource "aws_cloudwatch_log_group" "athena_executor" {
  name              = "/aws/lambda/${aws_lambda_function.athena_executor.function_name}"
  retention_in_days = 30
}

# Allow Bedrock service to invoke this Lambda
resource "aws_lambda_permission" "bedrock" {
  statement_id  = "AllowBedrockInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.athena_executor.function_name
  principal     = "bedrock.amazonaws.com"
  source_account = var.aws_account_id
}

# ── IAM Role: Bedrock Agent ───────────────────────────────────────────────────
resource "aws_iam_role" "bedrock_agent" {
  name = "${var.project_name}-bedrock-agent-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "bedrock_agent" {
  name = "bedrock-agent-policy"
  role = aws_iam_role.bedrock_agent.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel"]
        Resource = "arn:aws:bedrock:${var.aws_region}::foundation-model/anthropic.claude-3-5-sonnet-20241022-v2:0"
      },
      {
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = aws_lambda_function.athena_executor.arn
      }
    ]
  })
}

# ── Bedrock Agent ─────────────────────────────────────────────────────────────
# Load instruction from the file used in 04-bedrock-agent.md
locals {
  agent_instruction = file("${path.module}/../../../lambdas/athena_executor/agent_instruction.txt")
}

resource "aws_bedrockagent_agent" "main" {
  agent_name              = "${var.project_name}-agent"
  description             = "Answers natural language questions about call center performance"
  agent_resource_role_arn = aws_iam_role.bedrock_agent.arn
  foundation_model        = "anthropic.claude-3-5-sonnet-20241022-v2:0"
  instruction             = local.agent_instruction
  idle_session_ttl_in_seconds = 1800
}

resource "aws_bedrockagent_agent_action_group" "athena_query" {
  agent_id          = aws_bedrockagent_agent.main.agent_id
  agent_version     = "DRAFT"
  action_group_name = "AthenaQueryExecutor"
  description       = "Execute SQL queries against Athena to retrieve call analytics data"
  skip_resource_in_use_check = true

  action_group_executor {
    lambda = aws_lambda_function.athena_executor.arn
  }

  function_schema {
    member_functions {
      functions {
        name        = "execute_sql_query"
        description = "Execute a SQL query against the post_call_analytics Athena database and return results"
        parameters = {
          sql_query = {
            type        = "string"
            description = "The SQL query to execute. Must be valid Presto/Athena SQL."
            required    = true
          }
        }
      }
    }
  }
}

resource "aws_bedrockagent_agent_alias" "live" {
  agent_id         = aws_bedrockagent_agent.main.agent_id
  agent_alias_name = "live"
  description      = "Production alias"
}
```

### `terraform/modules/bedrock_agent/variables.tf`

```hcl
variable "project_name"               { type = string }
variable "environment"                { type = string }
variable "aws_account_id"             { type = string }
variable "aws_region"                 { type = string }
variable "analytics_bucket_arn"       { type = string }
variable "analytics_bucket_name"      { type = string }
variable "athena_results_bucket_arn"  { type = string }
variable "athena_results_bucket_name" { type = string }
variable "athena_workgroup_name"      { type = string }
variable "lambda_package_path"        { type = string }
```

### `terraform/modules/bedrock_agent/outputs.tf`

```hcl
output "agent_id"        { value = aws_bedrockagent_agent.main.agent_id }
output "agent_alias_id"  { value = aws_bedrockagent_agent_alias.live.agent_alias_id }
```

---

## Module: api

### `terraform/modules/api/main.tf`

```hcl
# ── Cognito User Pool ─────────────────────────────────────────────────────────
resource "aws_cognito_user_pool" "main" {
  name = "${var.project_name}-users"

  # No self-service signup — invite only
  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  password_policy {
    minimum_length                   = 12
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 7
  }

  mfa_configuration = "OPTIONAL"

  software_token_mfa_configuration {
    enabled = true
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }
}

resource "aws_cognito_user_pool_client" "web" {
  name         = "web-client"
  user_pool_id = aws_cognito_user_pool.main.id

  explicit_auth_flows = [
    "ALLOW_USER_SRP_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH"
  ]

  prevent_user_existence_errors = "ENABLED"
  access_token_validity         = 8    # hours
  id_token_validity             = 8    # hours
  refresh_token_validity        = 30   # days

  token_validity_units {
    access_token  = "hours"
    id_token      = "hours"
    refresh_token = "days"
  }
}

# ── Lambda IAM: API Handler ───────────────────────────────────────────────────
resource "aws_iam_role" "api_handler" {
  name = "${var.project_name}-api-handler-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "api_handler_basic" {
  role       = aws_iam_role.api_handler.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "api_handler" {
  name = "api-handler-policy"
  role = aws_iam_role.api_handler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["bedrock:InvokeAgent"]
      Resource = [
        "arn:aws:bedrock:${var.aws_region}:${var.aws_account_id}:agent/${var.bedrock_agent_id}",
        "arn:aws:bedrock:${var.aws_region}:${var.aws_account_id}:agent-alias/${var.bedrock_agent_id}/${var.bedrock_alias_id}"
      ]
    }]
  })
}

# ── Lambda: API Handler ───────────────────────────────────────────────────────
resource "aws_lambda_function" "api_handler" {
  function_name    = "${var.project_name}-api-handler"
  role             = aws_iam_role.api_handler.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  timeout          = 120
  memory_size      = 256
  filename         = var.lambda_package_path
  source_code_hash = filebase64sha256(var.lambda_package_path)

  environment {
    variables = {
      AGENT_ID       = var.bedrock_agent_id
      AGENT_ALIAS_ID = var.bedrock_alias_id
    }
  }
}

resource "aws_cloudwatch_log_group" "api_handler" {
  name              = "/aws/lambda/${aws_lambda_function.api_handler.function_name}"
  retention_in_days = 30
}

# ── API Gateway ───────────────────────────────────────────────────────────────
resource "aws_api_gateway_rest_api" "main" {
  name        = "${var.project_name}-api"
  description = "Post-call analytics API"
}

resource "aws_api_gateway_authorizer" "cognito" {
  name            = "CognitoAuthorizer"
  rest_api_id     = aws_api_gateway_rest_api.main.id
  type            = "COGNITO_USER_POOLS"
  provider_arns   = [aws_cognito_user_pool.main.arn]
  identity_source = "method.request.header.Authorization"
}

# Lambda integration
resource "aws_api_gateway_integration" "lambda" {
  for_each = {
    "POST_chat"    = aws_api_gateway_resource.chat.id
    "POST_report"  = aws_api_gateway_resource.report.id
    "GET_reports"  = aws_api_gateway_resource.reports.id
  }
  # ... (full resource/method/integration config)
  # See 05-api-backend.md for complete resource definitions
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = each.value
  http_method             = split("_", each.key)[0]
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.api_handler.invoke_arn
}

# Resources
resource "aws_api_gateway_resource" "chat" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "chat"
}

resource "aws_api_gateway_resource" "report" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "report"
}

resource "aws_api_gateway_resource" "reports" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "reports"
}

# Methods with Cognito auth
resource "aws_api_gateway_method" "chat_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.chat.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_method" "report_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.report.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_method" "reports_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.reports.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_deployment" "main" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  depends_on  = [
    aws_api_gateway_method.chat_post,
    aws_api_gateway_method.report_post,
    aws_api_gateway_method.reports_get,
  ]
  triggers = {
    redeployment = sha1(jsonencode(aws_api_gateway_rest_api.main.body))
  }
  lifecycle { create_before_destroy = true }
}

resource "aws_api_gateway_stage" "v1" {
  deployment_id = aws_api_gateway_deployment.main.id
  rest_api_id   = aws_api_gateway_rest_api.main.id
  stage_name    = "v1"

  default_route_settings {
    throttling_rate_limit  = 50
    throttling_burst_limit = 20
  }
}

resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api_handler.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}
```

### `terraform/modules/api/variables.tf`

```hcl
variable "project_name"        { type = string }
variable "environment"         { type = string }
variable "aws_account_id"      { type = string }
variable "aws_region"          { type = string }
variable "bedrock_agent_id"    { type = string }
variable "bedrock_alias_id"    { type = string }
variable "lambda_package_path" { type = string }
```

### `terraform/modules/api/outputs.tf`

```hcl
output "api_url"              { value = "${aws_api_gateway_stage.v1.invoke_url}" }
output "cognito_user_pool_id" { value = aws_cognito_user_pool.main.id }
output "cognito_client_id"    { value = aws_cognito_user_pool_client.web.id }
```

---

## Module: frontend

### `terraform/modules/frontend/main.tf`

```hcl
resource "aws_s3_bucket" "frontend" {
  bucket = "${var.project_name}-frontend-${random_id.suffix.hex}"
}

resource "random_id" "suffix" { byte_length = 4 }

resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket                  = aws_s3_bucket.frontend.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_cloudfront_origin_access_control" "main" {
  name                              = "${var.project_name}-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "main" {
  enabled             = true
  default_root_object = "index.html"
  price_class         = "PriceClass_100"

  origin {
    domain_name              = aws_s3_bucket.frontend.bucket_regional_domain_name
    origin_id                = "s3-frontend"
    origin_access_control_id = aws_cloudfront_origin_access_control.main.id
  }

  default_cache_behavior {
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "s3-frontend"
    viewer_protocol_policy = "redirect-to-https"
    compress               = true

    forwarded_values {
      query_string = false
      cookies { forward = "none" }
    }
  }

  custom_error_response {
    error_code         = 404
    response_code      = 200
    response_page_path = "/index.html"
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

# S3 bucket policy to allow CloudFront OAC
resource "aws_s3_bucket_policy" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.frontend.arn}/*"
      Condition = {
        StringEquals = {
          "AWS:SourceArn" = aws_cloudfront_distribution.main.arn
        }
      }
    }]
  })
}

# Upload built frontend files
resource "aws_s3_object" "frontend_files" {
  for_each = fileset(var.frontend_dist, "**")
  bucket   = aws_s3_bucket.frontend.id
  key      = each.value
  source   = "${var.frontend_dist}/${each.value}"
  etag     = filemd5("${var.frontend_dist}/${each.value}")

  content_type = lookup({
    "html" = "text/html",
    "js"   = "application/javascript",
    "css"  = "text/css",
    "json" = "application/json",
    "png"  = "image/png",
    "svg"  = "image/svg+xml",
    "ico"  = "image/x-icon",
  }, split(".", each.value)[length(split(".", each.value)) - 1], "application/octet-stream")
}
```

### `terraform/modules/frontend/variables.tf`

```hcl
variable "project_name"  { type = string }
variable "environment"   { type = string }
variable "api_url"        { type = string }
variable "frontend_dist"  { type = string }
```

### `terraform/modules/frontend/outputs.tf`

```hcl
output "cloudfront_url" {
  value = "https://${aws_cloudfront_distribution.main.domain_name}"
}
```
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

  project_name                   = var.project_name
  environment                    = var.environment
  aws_account_id                 = data.aws_caller_identity.current.account_id
  aws_region                     = var.aws_region
  calls_table_arn                = data.aws_dynamodb_table.calls.arn
  calls_stream_arn               = data.aws_dynamodb_table.calls.stream_arn
  scorecards_table_arn           = data.aws_dynamodb_table.scorecards.arn
  scorecards_stream_arn          = data.aws_dynamodb_table.scorecards.stream_arn
  existing_transcribe_bucket     = var.existing_transcribe_bucket
  existing_transcribe_bucket_arn = data.aws_s3_bucket.existing_transcribe.arn
  lambda_package_path            = "${path.root}/lambda_packages/stream_processor.zip"
}

module "bedrock_agent" {
  source = "./modules/bedrock_agent"

  project_name               = var.project_name
  environment                = var.environment
  aws_account_id             = data.aws_caller_identity.current.account_id
  aws_region                 = var.aws_region
  analytics_bucket_arn       = module.data_pipeline.analytics_bucket_arn
  analytics_bucket_name      = module.data_pipeline.analytics_bucket_name
  athena_results_bucket_arn  = module.data_pipeline.athena_results_bucket_arn
  athena_results_bucket_name = module.data_pipeline.athena_results_bucket_name
  athena_workgroup_name      = module.data_pipeline.athena_workgroup_name
  lambda_package_path        = "${path.root}/lambda_packages/athena_executor.zip"
}

module "api" {
  source = "./modules/api"

  project_name        = var.project_name
  environment         = var.environment
  aws_account_id      = data.aws_caller_identity.current.account_id
  aws_region          = var.aws_region
  bedrock_agent_id    = module.bedrock_agent.agent_id
  bedrock_alias_id    = module.bedrock_agent.agent_alias_id
  lambda_package_path = "${path.root}/lambda_packages/api_handler.zip"
  jobs_bucket_name    = module.data_pipeline.analytics_bucket_name
  jobs_bucket_arn     = module.data_pipeline.analytics_bucket_arn
}

module "frontend" {
  source = "./modules/frontend"

  project_name  = var.project_name
  environment   = var.environment
  api_url       = module.api.api_url
  frontend_dist = "${path.root}/../frontend/dist"
}

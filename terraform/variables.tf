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

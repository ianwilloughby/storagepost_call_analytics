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

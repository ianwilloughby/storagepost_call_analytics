output "api_url"              { value = aws_api_gateway_stage.v1.invoke_url }
output "cognito_user_pool_id" { value = aws_cognito_user_pool.main.id }
output "cognito_client_id"    { value = aws_cognito_user_pool_client.web.id }

output "analytics_bucket_name"      { value = aws_s3_bucket.analytics.bucket }
output "analytics_bucket_arn"       { value = aws_s3_bucket.analytics.arn }
output "athena_results_bucket_name" { value = aws_s3_bucket.athena_results.bucket }
output "athena_results_bucket_arn"  { value = aws_s3_bucket.athena_results.arn }
output "athena_workgroup_name"      { value = aws_athena_workgroup.main.name }
output "glue_database_name"         { value = aws_glue_catalog_database.main.name }

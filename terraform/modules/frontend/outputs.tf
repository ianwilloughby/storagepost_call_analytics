output "cloudfront_url" {
  value = "https://${aws_cloudfront_distribution.main.domain_name}"
}

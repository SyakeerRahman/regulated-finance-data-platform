output "function_url" {
  description = "POST a transaction here."
  value       = aws_lambda_function_url.scorer.function_url
}

output "log_group" {
  description = "Where the function writes its logs."
  value       = aws_cloudwatch_log_group.scorer.name
}

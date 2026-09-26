terraform {
  required_version = ">= 1.9"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region
}

locals {
  archive = "${path.module}/../build/scorer.zip"
}

resource "aws_iam_role" "scorer" {
  name = "${var.name}-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

# Write logs and nothing else. The function reads no bucket, no database and no secret, because
# the caller sends the account history in the request.
resource "aws_iam_role_policy_attachment" "logs" {
  role       = aws_iam_role.scorer.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_cloudwatch_log_group" "scorer" {
  name = "/aws/lambda/${var.name}"
  # A log group with no retention keeps every line for ever and quietly leaves the free tier.
  retention_in_days = 14
}

resource "aws_lambda_function" "scorer" {
  function_name = var.name
  role          = aws_iam_role.scorer.arn
  handler       = "lambda_fn.handler.handler"
  runtime       = "python3.12"
  architectures = ["arm64"]

  filename = local.archive
  # The hash, not a timestamp. A rebuilt zip with the same contents does not force a deployment.
  source_code_hash = filebase64sha256(local.archive)

  # The package carries no numpy and no XGBoost, so 256 MB and 10 seconds are generous.
  memory_size = 256
  timeout     = 10

  depends_on = [
    aws_iam_role_policy_attachment.logs,
    aws_cloudwatch_log_group.scorer,
  ]
}

# A Function URL, not API Gateway. It is part of Lambda, so it adds no second service and no
# second bill, and the free tier for Lambda requests does not expire after 12 months.
resource "aws_lambda_function_url" "scorer" {
  function_name      = aws_lambda_function.scorer.function_name
  authorization_type = var.public ? "NONE" : "AWS_IAM"
}

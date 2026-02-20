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
        Effect = "Allow"
        Action = ["glue:GetTable", "glue:GetTables", "glue:GetDatabase", "glue:GetPartitions"]
        Resource = [
          "arn:aws:glue:${var.aws_region}:${var.aws_account_id}:catalog",
          "arn:aws:glue:${var.aws_region}:${var.aws_account_id}:database/post_call_analytics",
          "arn:aws:glue:${var.aws_region}:${var.aws_account_id}:table/post_call_analytics/*"
        ]
      },
      {
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject", "s3:ListBucket", "s3:GetBucketLocation"]
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
      ATHENA_WORKGROUP      = var.athena_workgroup_name
      ATHENA_DATABASE       = "post_call_analytics"
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
  statement_id   = "AllowBedrockInvoke"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.athena_executor.function_name
  principal      = "bedrock.amazonaws.com"
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
        Resource = "arn:aws:bedrock:${var.aws_region}:${var.aws_account_id}:inference-profile/us.anthropic.claude-3-5-sonnet-20241022-v2:0"
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel"]
        Resource = "arn:aws:bedrock:*::foundation-model/anthropic.claude-3-5-sonnet-20241022-v2:0"
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
locals {
  agent_instruction = file("${path.module}/../../../lambdas/athena_executor/agent_instruction.txt")
}

resource "aws_bedrockagent_agent" "main" {
  agent_name                  = "${var.project_name}-agent"
  description                 = "Answers natural language questions about call center performance"
  agent_resource_role_arn     = aws_iam_role.bedrock_agent.arn
  foundation_model            = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
  instruction                 = local.agent_instruction
  idle_session_ttl_in_seconds = 1800
}

resource "aws_bedrockagent_agent_action_group" "athena_query" {
  agent_id                   = aws_bedrockagent_agent.main.agent_id
  agent_version              = "DRAFT"
  action_group_name          = "AthenaQueryExecutor"
  description                = "Execute SQL queries against Athena to retrieve call analytics data"
  skip_resource_in_use_check = true

  action_group_executor {
    lambda = aws_lambda_function.athena_executor.arn
  }

  function_schema {
    member_functions {
      functions {
        name        = "execute_sql_query"
        description = "Execute a SQL query against the post_call_analytics Athena database and return results"
        parameters {
          map_block_key = "sql_query"
          type          = "string"
          description   = "The SQL query to execute. Must be valid Presto/Athena SQL."
          required      = true
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

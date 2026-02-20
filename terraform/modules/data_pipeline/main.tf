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
    filter {}
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
    filter {}
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

  s3_target {
    path = "s3://${aws_s3_bucket.analytics.bucket}/calls/"
  }

  schema_change_policy {
    update_behavior = "LOG"
    delete_behavior = "LOG"
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

  s3_target {
    path = "s3://${aws_s3_bucket.analytics.bucket}/scorecards/"
  }

  schema_change_policy {
    update_behavior = "LOG"
    delete_behavior = "LOG"
  }

  configuration = jsonencode({
    Version = 1.0
    CrawlerOutput = {
      Partitions = { AddOrUpdateBehavior = "InheritFromTable" }
      Tables     = { AddOrUpdateBehavior = "MergeNewColumns" }
    }
  })
}

# ── Athena Workgroup ──────────────────────────────────────────────────────────
resource "aws_athena_workgroup" "main" {
  name          = var.project_name
  description   = "Post-call analytics workgroup"
  force_destroy = true

  configuration {
    enforce_workgroup_configuration = true
    bytes_scanned_cutoff_per_query  = 1073741824 # 1 GB

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
        Resource = "arn:aws:transcribe:${var.aws_region}:${var.aws_account_id}:transcription-job/*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${var.existing_transcribe_bucket_arn}/*"
      },
      {
        Effect = "Allow"
        Action = [
          "glue:BatchCreatePartition",
          "glue:GetTable"
        ]
        Resource = [
          "arn:aws:glue:${var.aws_region}:${var.aws_account_id}:catalog",
          "arn:aws:glue:${var.aws_region}:${var.aws_account_id}:database/${aws_glue_catalog_database.main.name}",
          "arn:aws:glue:${var.aws_region}:${var.aws_account_id}:table/${aws_glue_catalog_database.main.name}/*"
        ]
      }
    ]
  })
}

# ── Lambda: Stream Processor ──────────────────────────────────────────────────
resource "aws_lambda_function" "stream_processor" {
  function_name    = "${var.project_name}-stream-processor"
  role             = aws_iam_role.stream_processor.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  timeout          = 300
  memory_size      = 512
  filename         = var.lambda_package_path
  source_code_hash = filebase64sha256(var.lambda_package_path)

  environment {
    variables = {
      ANALYTICS_BUCKET      = aws_s3_bucket.analytics.bucket
      TRANSCRIBE_BUCKET     = var.existing_transcribe_bucket
      TRANSCRIPT_KEY_PREFIX = "parsedFiles/"
      GLUE_DATABASE         = aws_glue_catalog_database.main.name
    }
  }
}

resource "aws_cloudwatch_log_group" "stream_processor" {
  name              = "/aws/lambda/${aws_lambda_function.stream_processor.function_name}"
  retention_in_days = 30
}

# ── Event Source Mappings: DynamoDB Streams → Lambda ─────────────────────────
resource "aws_lambda_event_source_mapping" "calls_stream" {
  event_source_arn               = var.calls_stream_arn
  function_name                  = aws_lambda_function.stream_processor.arn
  starting_position              = "TRIM_HORIZON"
  batch_size                     = 100
  bisect_batch_on_function_error = true

  destination_config {
    on_failure {
      destination_arn = aws_sqs_queue.dlq.arn
    }
  }
}

resource "aws_lambda_event_source_mapping" "scorecards_stream" {
  event_source_arn               = var.scorecards_stream_arn
  function_name                  = aws_lambda_function.stream_processor.arn
  starting_position              = "TRIM_HORIZON"
  batch_size                     = 100
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
  message_retention_seconds = 1209600 # 14 days
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

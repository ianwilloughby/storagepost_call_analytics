# Data Pipeline — DynamoDB Streams → S3 → Glue → Athena

## Lambda: Stream Processor

This Lambda flattens DynamoDB records, enriches them with call duration and answer type, and writes Parquet-compatible JSON to S3.

**File: `lambdas/stream_processor/handler.py`**

```python
import json
import os
import re
import boto3
import logging
from datetime import datetime, timezone
from decimal import Decimal

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")
transcribe = boto3.client("transcribe")

ANALYTICS_BUCKET = os.environ["ANALYTICS_BUCKET"]
TRANSCRIBE_BUCKET = os.environ["TRANSCRIBE_BUCKET"]


def lambda_handler(event, context):
    calls_records = []
    scorecards_records = []

    for record in event["Records"]:
        if record["eventName"] == "REMOVE":
            continue  # Don't sync deletions to analytics layer

        table_arn = record["eventSourceARN"]
        new_image = record["dynamodb"].get("NewImage", {})

        if not new_image:
            continue

        if "calls" in table_arn.lower():
            flat = flatten_call_record(new_image)
            if flat:
                calls_records.append(flat)
        elif "scorecards" in table_arn.lower():
            flat = flatten_scorecard_record(new_image)
            if flat:
                scorecards_records.append(flat)

    write_to_s3(calls_records, "calls")
    write_to_s3(scorecards_records, "scorecards")

    return {"statusCode": 200, "processed": len(calls_records) + len(scorecards_records)}


def deserialize_dynamodb_value(value: dict):
    """Convert DynamoDB typed value to Python native."""
    if "S" in value:
        return value["S"]
    elif "N" in value:
        v = value["N"]
        return int(v) if "." not in v else float(v)
    elif "BOOL" in value:
        return value["BOOL"]
    elif "NULL" in value:
        return None
    elif "M" in value:
        return {k: deserialize_dynamodb_value(v) for k, v in value["M"].items()}
    elif "L" in value:
        return [deserialize_dynamodb_value(i) for i in value["L"]]
    elif "SS" in value:
        return list(value["SS"])
    elif "NS" in value:
        return [float(n) for n in value["NS"]]
    return None


def flatten_call_record(image: dict) -> dict | None:
    """Flatten a DynamoDB calls record to a flat dict for Athena."""
    try:
        call_id = image.get("callId", {}).get("S", "")
        if not call_id:
            return None

        ts_raw = image.get("callTimestampUTC", {}).get("S", "")
        payload = deserialize_dynamodb_value(image.get("payload", {"M": {}}))

        if not isinstance(payload, dict):
            payload = {}

        # Parse timestamp
        ts = None
        year = month = day = "unknown"
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            year = ts.strftime("%Y")
            month = ts.strftime("%m")
            day = ts.strftime("%d")
        except Exception:
            pass

        # Enrich: get call duration from Transcribe
        file_name = payload.get("file_name", "")
        call_duration_seconds, transcript_s3_key = get_transcribe_metadata(file_name, call_id)
        answer_type = infer_answer_type(call_duration_seconds, transcript_s3_key)

        return {
            "call_id": call_id,
            "call_timestamp_utc": ts_raw,
            "agent_id": payload.get("agentId", ""),
            "agent_name": payload.get("agentName", ""),
            "allocation": payload.get("allocation", ""),
            "direction": payload.get("direction", ""),
            "file_name": file_name,
            "first_or_follow_up": payload.get("firstOrFollowUp", ""),
            "medium": payload.get("medium", ""),
            "program": payload.get("program", ""),
            "queue_id": str(payload.get("queueId", "")),
            "queue_name": payload.get("queueName", ""),
            "session_id": payload.get("sessionId", ""),
            "site_id": payload.get("siteId", 0),
            "site_name": payload.get("siteName", ""),
            "tenant_id": payload.get("tenantId", 0),
            "s3_bucket": payload.get("s3_bucket", ""),
            "call_duration_seconds": call_duration_seconds,
            "answer_type": answer_type,
            "transcript_s3_key": transcript_s3_key,
            # Partition fields
            "year": year,
            "month": month,
            "day": day,
        }
    except Exception as e:
        logger.error(f"Error flattening call record: {e}")
        return None


def flatten_scorecard_record(image: dict) -> dict | None:
    """Flatten a DynamoDB scorecards record."""
    try:
        guid = image.get("guid", {}).get("S", "")
        if not guid:
            return None

        dt_raw = image.get("datetime", {}).get("S", "")

        # Parse datetime from format "2025-10-08T14-34-19"
        year = month = day = "unknown"
        try:
            dt_clean = dt_raw[:10]  # "2025-10-08"
            year, month, day = dt_clean.split("-")
        except Exception:
            pass

        scores_raw = deserialize_dynamodb_value(image.get("scores", {"M": {}}))
        scores = scores_raw if isinstance(scores_raw, dict) else {}

        def score_val(key):
            return scores.get(key, {}).get("score", 0) if isinstance(scores.get(key), dict) else 0

        def evidence_val(key):
            return scores.get(key, {}).get("evidence", "") if isinstance(scores.get(key), dict) else ""

        ingested_at = image.get("ingestedAt", {}).get("S", "")

        return {
            "guid": guid,
            "datetime": dt_raw,
            "agent": image.get("agent", {}).get("S", ""),
            "call_type": image.get("callType", {}).get("S", ""),
            "ingested_at": ingested_at,
            "notes": image.get("notes", {}).get("S", ""),
            "outcome": image.get("outcome", {}).get("S", ""),
            "overall_score": float(image.get("overallScore", {}).get("N", 0)),
            "primary_intent": image.get("primaryIntent", {}).get("S", ""),
            "resolution_reason": image.get("resolutionReason", {}).get("S", ""),
            "summary": image.get("summary", {}).get("S", ""),
            "secondary_intent": image.get("secondaryIntent", {}).get("S", ""),
            "score_ask_for_payment": score_val("askForPayment"),
            "score_confirm_location": score_val("confirmLocation"),
            "score_features_advantages": score_val("featuresAdvantagesBenefits"),
            "score_handle_objections": score_val("handleObjections"),
            "score_size_recommendation": score_val("sizeRecommendation"),
            "score_urgency": score_val("urgency"),
            "evidence_ask_for_payment": evidence_val("askForPayment"),
            "evidence_confirm_location": evidence_val("confirmLocation"),
            "evidence_features_advantages": evidence_val("featuresAdvantagesBenefits"),
            "evidence_handle_objections": evidence_val("handleObjections"),
            "evidence_size_recommendation": evidence_val("sizeRecommendation"),
            "evidence_urgency": evidence_val("urgency"),
            "year": year,
            "month": month,
            "day": day,
        }
    except Exception as e:
        logger.error(f"Error flattening scorecard record: {e}")
        return None


def get_transcribe_metadata(file_name: str, call_id: str) -> tuple[int, str]:
    """
    Try to get call duration from Transcribe job output in S3.
    Returns (duration_seconds, transcript_s3_key).
    """
    try:
        # Transcribe output is stored with a key pattern based on the job name
        # Adjust the key pattern to match your existing Transcribe pipeline
        transcript_key = f"transcripts/{call_id}.json"
        
        response = s3.get_object(Bucket=TRANSCRIBE_BUCKET, Key=transcript_key)
        transcript_data = json.loads(response["Body"].read())
        
        results = transcript_data.get("results", {})
        duration = float(results.get("audio_duration", 0))
        
        return int(duration), transcript_key
    except Exception:
        return 0, ""


def infer_answer_type(duration_seconds: int, transcript_s3_key: str) -> str:
    """
    Infer whether an outbound call was answered by a human, went to voicemail,
    or was not answered.
    """
    if duration_seconds == 0:
        return "Unknown"
    if duration_seconds < 10:
        return "NoAnswer"
    
    if not transcript_s3_key:
        return "Unknown"

    try:
        response = s3.get_object(Bucket=TRANSCRIBE_BUCKET, Key=transcript_s3_key)
        transcript_data = json.loads(response["Body"].read())
        
        items = transcript_data.get("results", {}).get("items", [])
        speakers = set()
        for item in items:
            speaker = item.get("speaker_label", "")
            if speaker:
                speakers.add(speaker)
        
        if len(speakers) >= 2:
            return "Human"
        elif len(speakers) == 1:
            return "Voicemail"
        else:
            return "Unknown"
    except Exception:
        # If duration > 30s but we can't check transcript, assume human
        return "Human" if duration_seconds > 30 else "Unknown"


def write_to_s3(records: list, table_name: str):
    """Write records to S3 as line-delimited JSON, partitioned by date."""
    if not records:
        return

    # Group by partition
    partitions: dict[str, list] = {}
    for record in records:
        year = record.get("year", "unknown")
        month = record.get("month", "unknown")
        day = record.get("day", "unknown")
        key = (year, month, day)
        partitions.setdefault(key, []).append(record)

    now_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")

    for (year, month, day), recs in partitions.items():
        s3_key = (
            f"{table_name}/year={year}/month={month}/day={day}/"
            f"{now_ts}.json"
        )
        body = "\n".join(json.dumps(r, default=str) for r in recs)
        s3.put_object(
            Bucket=ANALYTICS_BUCKET,
            Key=s3_key,
            Body=body.encode("utf-8"),
            ContentType="application/json",
        )
        logger.info(f"Wrote {len(recs)} {table_name} records to s3://{ANALYTICS_BUCKET}/{s3_key}")
```

---

## Historical Backfill Script

Run this once to seed S3 with all existing DynamoDB data before the stream catches up.

**File: `lambdas/stream_processor/backfill.py`**

```python
#!/usr/bin/env python3
"""
Backfill script: scans entire DynamoDB table and writes to S3.
Run once after enabling Streams.

Usage:
  python backfill.py --table calls
  python backfill.py --table scorecards
"""
import argparse
import json
import os
import boto3
from handler import flatten_call_record, flatten_scorecard_record, write_to_s3

dynamodb = boto3.client("dynamodb")

os.environ["ANALYTICS_BUCKET"] = "post-call-analytics-YOUR-ACCOUNT-YOUR-REGION"
os.environ["TRANSCRIBE_BUCKET"] = "YOUR-EXISTING-TRANSCRIBE-BUCKET"

ANALYTICS_BUCKET = os.environ["ANALYTICS_BUCKET"]


def backfill(table_name: str):
    print(f"Starting backfill for table: {table_name}")
    total = 0
    paginator = dynamodb.get_paginator("scan")

    for page in paginator.paginate(TableName=table_name):
        records = []
        for item in page["Items"]:
            if "calls" in table_name.lower():
                flat = flatten_call_record(item)
            else:
                flat = flatten_scorecard_record(item)
            if flat:
                records.append(flat)

        write_to_s3(records, "calls" if "calls" in table_name.lower() else "scorecards")
        total += len(records)
        print(f"  Processed {total} records so far...")

    print(f"Backfill complete. Total records: {total}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--table", required=True)
    args = parser.parse_args()
    backfill(args.table)
```

---

## Glue Crawler Setup

After the first S3 data is written, create Glue crawlers to build the schema. This can be done via CDK or manually in the console.

**Add to DataPipelineStack:**

```python
from aws_cdk import aws_glue as glue, aws_iam as iam

# IAM role for Glue crawler
crawler_role = iam.Role(
    self, "GlueCrawlerRole",
    assumed_by=iam.ServicePrincipal("glue.amazonaws.com"),
    managed_policies=[
        iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSGlueServiceRole")
    ],
    inline_policies={
        "CrawlerS3Access": iam.PolicyDocument(statements=[
            iam.PolicyStatement(
                actions=["s3:GetObject", "s3:ListBucket"],
                resources=[
                    self.analytics_bucket.bucket_arn,
                    f"{self.analytics_bucket.bucket_arn}/*"
                ]
            )
        ])
    }
)

# Crawler for calls table
glue.CfnCrawler(
    self, "CallsCrawler",
    name="post-call-analytics-calls-crawler",
    role=crawler_role.role_arn,
    database_name="post_call_analytics",
    targets=glue.CfnCrawler.TargetsProperty(
        s3_targets=[glue.CfnCrawler.S3TargetProperty(
            path=f"s3://{self.analytics_bucket.bucket_name}/calls/"
        )]
    ),
    schedule=glue.CfnCrawler.ScheduleProperty(
        schedule_expression="cron(0 * * * ? *)"  # Hourly
    ),
    configuration=json.dumps({
        "Version": 1.0,
        "CrawlerOutput": {
            "Partitions": {"AddOrUpdateBehavior": "InheritFromTable"},
            "Tables": {"AddOrUpdateBehavior": "MergeNewColumns"}
        }
    })
)

# Crawler for scorecards table (same pattern, different path)
```

---

## Athena Table DDL

Run these once in the Athena console after the Glue crawler has created the tables, or use them to manually create the tables.

```sql
-- Repair partitions after backfill
MSCK REPAIR TABLE post_call_analytics.calls;
MSCK REPAIR TABLE post_call_analytics.scorecards;

-- Test query
SELECT
    direction,
    answer_type,
    COUNT(*) as call_count,
    AVG(call_duration_seconds) as avg_duration_seconds,
    SUM(CASE WHEN call_duration_seconds > 60 THEN 1 ELSE 0 END) as calls_over_one_minute
FROM post_call_analytics.calls
WHERE year = '2026' AND month = '02' AND day = '11'
GROUP BY direction, answer_type
ORDER BY direction, answer_type;
```

---

## Enable DynamoDB Streams (Run Before Deploying CDK)

Run these AWS CLI commands against your existing tables:

```bash
# Enable streams on calls table
aws dynamodb update-table \
  --table-name calls \
  --stream-specification StreamEnabled=true,StreamViewType=NEW_AND_OLD_IMAGES \
  --region us-east-1

# Enable streams on scorecards table
aws dynamodb update-table \
  --table-name scorecards \
  --stream-specification StreamEnabled=true,StreamViewType=NEW_AND_OLD_IMAGES \
  --region us-east-1

# Verify
aws dynamodb describe-table --table-name calls --query "Table.StreamSpecification"
```

#!/usr/bin/env python3
"""
Parallel backfill script: scans entire DynamoDB table and writes to S3.
Uses thread pool to fetch transcripts in parallel for much faster processing.

Usage:
  export ANALYTICS_BUCKET=post-call-analytics-data-ACCOUNT-REGION
  export TRANSCRIBE_BUCKET=pca-outputbucket-ipl3kszkd6wk
  export TRANSCRIPT_KEY_PREFIX=parsedFiles/
  python backfill_parallel.py --table callrecords-xxx --workers 50
"""
import argparse
import os
import sys
import json
import boto3
import logging
import threading
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

# Import flatten functions from handler
from handler import (
    flatten_scorecard_record,
    deserialize_dynamodb_value,
    ANALYTICS_BUCKET,
    TRANSCRIBE_BUCKET,
    TRANSCRIPT_KEY_PREFIX,
)

s3 = boto3.client("s3")
dynamodb = boto3.client("dynamodb")

# Thread-local storage for S3 clients
_thread_local = threading.local()

def _get_s3():
    if not hasattr(_thread_local, "s3"):
        _thread_local.s3 = boto3.client("s3")
    return _thread_local.s3

if not ANALYTICS_BUCKET:
    print("ERROR: Set ANALYTICS_BUCKET environment variable")
    sys.exit(1)
if not TRANSCRIBE_BUCKET:
    print("ERROR: Set TRANSCRIBE_BUCKET environment variable")
    sys.exit(1)


def flatten_call_with_thread_s3(item):
    """Flatten a single call record using thread-local S3 client for transcript fetch."""
    try:
        call_id = item.get("callId", {}).get("S", "")
        if not call_id:
            return None

        ts_raw = item.get("callTimestampUTC", {}).get("S", "")
        payload = deserialize_dynamodb_value(item.get("payload", {"M": {}}))
        if not isinstance(payload, dict):
            payload = {}

        # Parse timestamp
        year = month = day = "unknown"
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            year = ts.strftime("%Y")
            month = ts.strftime("%m")
            day = ts.strftime("%d")
        except Exception:
            pass

        # Enrich: get call duration from Transcribe using thread-local S3
        file_name = payload.get("file_name", "")
        call_duration_seconds = 0
        transcript_s3_key = ""
        num_speakers = 0

        if file_name:
            try:
                thread_s3 = _get_s3()
                transcript_key = f"{TRANSCRIPT_KEY_PREFIX}{file_name}.json"
                response = thread_s3.get_object(Bucket=TRANSCRIBE_BUCKET, Key=transcript_key)
                transcript_data = json.loads(response["Body"].read())
                ca = transcript_data.get("ConversationAnalytics", {})
                call_duration_seconds = int(float(ca.get("Duration", 0)))
                transcript_s3_key = transcript_key
                speaker_labels = ca.get("SpeakerLabels", [])
                speakers = set(l.get("Speaker", "") for l in speaker_labels if l.get("Speaker"))
                num_speakers = len(speakers)
            except Exception:
                pass

        # Infer answer type
        if call_duration_seconds == 0:
            answer_type = "Unknown"
        elif call_duration_seconds < 10:
            answer_type = "NoAnswer"
        elif num_speakers >= 2:
            answer_type = "Human"
        elif num_speakers == 1:
            answer_type = "Voicemail"
        elif call_duration_seconds > 30:
            answer_type = "Human"
        else:
            answer_type = "Unknown"

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
            "year": year,
            "month": month,
            "day": day,
        }
    except Exception as e:
        logger.error(f"Error processing record: {e}")
        return None


def write_to_s3_batch(records, table_name):
    """Write records to S3 as line-delimited JSON, partitioned by date."""
    if not records:
        return

    partitions = {}
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
        body = "\n".join(json.dumps({k: v for k, v in r.items() if k not in ("year", "month", "day")}, default=str) for r in recs)
        s3.put_object(
            Bucket=ANALYTICS_BUCKET,
            Key=s3_key,
            Body=body.encode("utf-8"),
            ContentType="application/json",
        )


def backfill(table_name: str, workers: int):
    is_calls = "calls" in table_name.lower() or "callrecords" in table_name.lower()
    target = "calls" if is_calls else "scorecards"
    logger.info(f"Starting parallel backfill for {table_name} with {workers} workers")

    total = 0
    total_with_duration = 0
    paginator = dynamodb.get_paginator("scan")

    for page in paginator.paginate(TableName=table_name):
        items = page["Items"]

        if is_calls:
            # Parallel flatten (each does S3 GET for transcript)
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(flatten_call_with_thread_s3, item): item for item in items}
                records = []
                for future in as_completed(futures):
                    result = future.result()
                    if result:
                        records.append(result)
                        if result.get("call_duration_seconds", 0) > 0:
                            total_with_duration += 1
        else:
            records = []
            for item in items:
                flat = flatten_scorecard_record(item)
                if flat:
                    records.append(flat)

        write_to_s3_batch(records, target)
        total += len(records)
        logger.info(f"  Processed {total} records ({total_with_duration} with duration > 0)")

    logger.info(f"Backfill complete. Total: {total}, with duration: {total_with_duration}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--table", required=True, help="DynamoDB table name")
    parser.add_argument("--workers", type=int, default=50, help="Thread pool size")
    args = parser.parse_args()
    backfill(args.table, args.workers)

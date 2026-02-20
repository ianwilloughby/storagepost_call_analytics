#!/usr/bin/env python3
"""
Backfill script: scans entire DynamoDB table and writes to S3.
Run once after enabling Streams.

Usage:
  python backfill.py --table calls
  python backfill.py --table scorecards

Environment variables (set before running):
  ANALYTICS_BUCKET  — S3 bucket for analytics data
  TRANSCRIBE_BUCKET — S3 bucket with Transcribe outputs
"""
import argparse
import os
import boto3
from handler import flatten_call_record, flatten_scorecard_record, write_to_s3

dynamodb = boto3.client("dynamodb")

# These must be set before running
if "ANALYTICS_BUCKET" not in os.environ:
    print("ERROR: Set ANALYTICS_BUCKET environment variable before running.")
    print("  export ANALYTICS_BUCKET=post-call-analytics-data-ACCOUNT-REGION")
    exit(1)

if "TRANSCRIBE_BUCKET" not in os.environ:
    print("ERROR: Set TRANSCRIBE_BUCKET environment variable before running.")
    print("  export TRANSCRIBE_BUCKET=pca-outputbucket-ipl3kszkd6wk")
    exit(1)


def backfill(table_name: str):
    print(f"Starting backfill for table: {table_name}")
    total = 0
    paginator = dynamodb.get_paginator("scan")

    for page in paginator.paginate(TableName=table_name):
        records = []
        for item in page["Items"]:
            if "calls" in table_name.lower() or "callrecords" in table_name.lower():
                flat = flatten_call_record(item)
            else:
                flat = flatten_scorecard_record(item)
            if flat:
                records.append(flat)

        target = "calls" if ("calls" in table_name.lower() or "callrecords" in table_name.lower()) else "scorecards"
        write_to_s3(records, target)
        total += len(records)
        print(f"  Processed {total} records so far...")

    print(f"Backfill complete. Total records: {total}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill DynamoDB table to S3 analytics")
    parser.add_argument("--table", required=True, help="DynamoDB table name to scan")
    args = parser.parse_args()
    backfill(args.table)

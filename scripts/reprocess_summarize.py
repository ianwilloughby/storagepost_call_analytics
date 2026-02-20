#!/usr/bin/env python3
"""
Reprocess transcripts through ProcessSummarize + FinalProcessing.

Since the OpenAI API keys were broken from 2025-10-12 onward, scorecards and
summaries were not generated properly.  This script:

  1. Lists parsedFiles/ in the PCA output bucket with DATETIME >= cutoff
  2. Copies each back to interimResults/ (so ProcessSummarize can read it)
  3. Invokes the ProcessSummarize Lambda (generates summary + scorecard, sends SQS)
  4. Writes the scorecard + intent data to the DynamoDB `scorecards` table
  5. Invokes the FinalProcessing Lambda (moves interimResults → parsedFiles)

Usage:
    python3 scripts/reprocess_summarize.py --dry-run          # list files only
    python3 scripts/reprocess_summarize.py                    # run all
    python3 scripts/reprocess_summarize.py --workers 3        # concurrency
    python3 scripts/reprocess_summarize.py --limit 10         # first N only
    python3 scripts/reprocess_summarize.py --cutoff 2025-11-01  # custom date
"""

import argparse
import boto3
import json
import re
import sys
import time
import logging
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Config ──────────────────────────────────────────────────────────────────
OUTPUT_BUCKET = "pca-outputbucket-ipl3kszkd6wk"
INPUT_BUCKET = "pca-inputbucket-covuuisoatgv"
PARSED_PREFIX = "parsedFiles/"
INTERIM_PREFIX = "interimResults/"
SUMMARIZE_LAMBDA = "PCA-PCAServer-A6UZQTVJTG84-PCA-UKDHQ4W-SFSummarize-drmWxYH3zMgh"
FINAL_LAMBDA = "PCA-PCAServer-A6UZQTVJTG84-PCA-U-SFFinalProcessing-D0ffqppsNN5N"
SCORECARDS_TABLE = "scorecards"
DEFAULT_CUTOFF = "2025-10-12"
REGION = "us-east-1"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def list_parsed_files(s3, cutoff: str, limit: int | None = None):
    """Yield parsedFiles keys whose embedded DATETIME >= cutoff."""
    paginator = s3.get_paginator("list_objects_v2")
    count = 0
    for page in paginator.paginate(Bucket=OUTPUT_BUCKET, Prefix=PARSED_PREFIX):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            m = re.search(r"_DATETIME_(\d{4}-\d{2}-\d{2})", key)
            if m and m.group(1) >= cutoff:
                count += 1
                yield key
                if limit and count >= limit:
                    return


def build_event(parsed_key: str) -> dict:
    """Construct the Step Function-style event payload from a parsedFiles key."""
    filename = parsed_key.removeprefix(PARSED_PREFIX)  # e.g. uuid_CUST_..._DATETIME_..wav.json
    wav_filename = filename.removesuffix(".json")       # drop the .json → .wav
    interim_key = INTERIM_PREFIX + filename

    return {
        "bucket": INPUT_BUCKET,
        "key": f"originalAudio/{wav_filename}",
        "inputType": "audio",
        "summarize": "true",
        "jobName": wav_filename,
        "apiMode": "analytics",
        "transcribeStatus": "COMPLETED",
        "interimResultsFile": interim_key,
        "telephony": "none",
    }


def extract_metadata(job_name: str) -> dict:
    """Extract guid, agent, datetime, queue from the job name."""
    meta = {}
    m = re.search(r"_GUID_([^_]+)", job_name)
    meta["guid"] = m.group(1) if m else "unknown"
    m = re.search(r"_AGENT_([^_]+)", job_name)
    meta["agent"] = m.group(1) if m else "unknown"
    m = re.search(r"_DATETIME_([^_]+)", job_name)
    dt = m.group(1) if m else "unknown"
    meta["datetime"] = dt.removesuffix(".wav")
    m = re.search(r"_QUEUE_(\d+)", job_name)
    meta["queue"] = m.group(1) if m else "unknown"
    return meta


def write_scorecard_to_dynamodb(dynamodb, payload: dict, job_name: str):
    """Write scorecard + intent data to the scorecards DynamoDB table."""
    scorecard = payload.get("scorecard")
    if not scorecard:
        return False

    meta = extract_metadata(job_name)
    behavior = scorecard.get("behavior") or {}
    intent = scorecard.get("intent") or {}

    # Build the scores map from behavior scorecard
    raw_scores = behavior.get("scores", {})
    scores_map = {}
    overall_total = 0
    score_count = 0
    for category, vals in raw_scores.items():
        if isinstance(vals, dict) and "score" in vals:
            score_val = vals["score"]
            evidence_val = vals.get("evidence", "")
            scores_map[category] = {
                "M": {
                    "score": {"N": str(score_val)},
                    "evidence": {"S": str(evidence_val)},
                }
            }
            overall_total += int(score_val)
            score_count += 1

    overall_score = round(overall_total / score_count, 2) if score_count else 0

    item = {
        "guid": {"S": meta["guid"]},
        "datetime": {"S": meta["datetime"]},
        "agent": {"S": behavior.get("agent", meta["agent"])},
        "callType": {"S": behavior.get("callType", "Unknown")},
        "overallScore": {"N": str(overall_score)},
        "ingestedAt": {"S": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]},
    }

    if scores_map:
        item["scores"] = {"M": scores_map}
    if behavior.get("notes"):
        item["notes"] = {"S": behavior["notes"]}

    # Intent fields
    if intent.get("primaryIntent"):
        item["primaryIntent"] = {"S": intent["primaryIntent"]}
    if intent.get("secondaryIntent"):
        item["secondaryIntent"] = {"S": intent["secondaryIntent"]}
    if intent.get("outcome"):
        item["outcome"] = {"S": intent["outcome"]}
    if intent.get("resolutionReason"):
        item["resolutionReason"] = {"S": intent["resolutionReason"]}
    if intent.get("summary"):
        item["summary"] = {"S": intent["summary"]}

    dynamodb.put_item(TableName=SCORECARDS_TABLE, Item=item)
    return True


def reprocess_one(s3, lambda_client, dynamodb, parsed_key: str, idx: int, total: int) -> dict:
    """Reprocess a single transcript: copy → summarize → write scorecard → final."""
    filename = parsed_key.removeprefix(PARSED_PREFIX)
    interim_key = INTERIM_PREFIX + filename
    event = build_event(parsed_key)
    result = {"key": parsed_key, "status": "unknown", "error": None}

    try:
        # Step 1: Copy parsedFile → interimResults
        log.info("[%d/%d] Copying %s → %s", idx, total, parsed_key, interim_key)
        s3.copy_object(
            Bucket=OUTPUT_BUCKET,
            CopySource={"Bucket": OUTPUT_BUCKET, "Key": parsed_key},
            Key=interim_key,
        )

        # Step 2: Invoke ProcessSummarize
        log.info("[%d/%d] Invoking ProcessSummarize for %s", idx, total, filename)
        resp = lambda_client.invoke(
            FunctionName=SUMMARIZE_LAMBDA,
            InvocationType="RequestResponse",
            Payload=json.dumps(event).encode(),
        )
        payload = json.loads(resp["Payload"].read())
        if resp.get("FunctionError"):
            raise RuntimeError(f"ProcessSummarize error: {payload}")
        log.info("[%d/%d] ProcessSummarize OK – guid=%s", idx, total, payload.get("guid", "?"))

        # Step 3: Write scorecard to DynamoDB
        wrote = write_scorecard_to_dynamodb(dynamodb, payload, event["jobName"])
        if wrote:
            log.info("[%d/%d] Scorecard written to DynamoDB for guid=%s", idx, total, payload.get("guid", "?"))
        else:
            log.info("[%d/%d] No scorecard data to write (non-605 queue?)", idx, total)

        # Step 4: Invoke FinalProcessing (pass through the enriched event)
        log.info("[%d/%d] Invoking FinalProcessing for %s", idx, total, filename)
        resp2 = lambda_client.invoke(
            FunctionName=FINAL_LAMBDA,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload).encode(),
        )
        payload2 = json.loads(resp2["Payload"].read())
        if resp2.get("FunctionError"):
            raise RuntimeError(f"FinalProcessing error: {payload2}")

        result["status"] = "success"
        result["guid"] = payload.get("guid")
        log.info("[%d/%d] ✓ Done: %s", idx, total, filename)

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        log.error("[%d/%d] ✗ FAILED %s: %s", idx, total, filename, e)

    return result


def main():
    parser = argparse.ArgumentParser(description="Reprocess transcripts through ProcessSummarize")
    parser.add_argument("--cutoff", default=DEFAULT_CUTOFF, help="YYYY-MM-DD cutoff date (default: 2025-10-12)")
    parser.add_argument("--dry-run", action="store_true", help="List files without processing")
    parser.add_argument("--workers", type=int, default=2, help="Parallel workers (default: 2)")
    parser.add_argument("--limit", type=int, default=None, help="Process only first N files")
    args = parser.parse_args()

    s3 = boto3.client("s3", region_name=REGION)
    lambda_client = boto3.client("lambda", region_name=REGION)
    dynamodb = boto3.client("dynamodb", region_name=REGION)

    log.info("Listing parsedFiles with DATETIME >= %s ...", args.cutoff)
    keys = list(list_parsed_files(s3, args.cutoff, args.limit))
    log.info("Found %d files to reprocess", len(keys))

    if not keys:
        log.info("Nothing to do.")
        return

    if args.dry_run:
        for k in keys:
            print(k)
        log.info("Dry run complete. %d files would be reprocessed.", len(keys))
        return

    log.info("Starting reprocessing with %d workers...", args.workers)
    succeeded = 0
    failed = 0
    errors = []

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(reprocess_one, s3, lambda_client, dynamodb, key, i + 1, len(keys)): key
            for i, key in enumerate(keys)
        }
        for future in as_completed(futures):
            result = future.result()
            if result["status"] == "success":
                succeeded += 1
            else:
                failed += 1
                errors.append(result)

    log.info("=" * 60)
    log.info("COMPLETE: %d succeeded, %d failed out of %d total", succeeded, failed, len(keys))
    if errors:
        log.error("Failed files:")
        for e in errors:
            log.error("  %s: %s", e["key"], e["error"])


if __name__ == "__main__":
    main()

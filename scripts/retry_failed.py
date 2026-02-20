#!/usr/bin/env python3
"""
Retry only the files that failed in the main reprocess run.

Parses /tmp/reprocess.log for FAILED lines, extracts filenames, and re-runs
them through the same reprocess pipeline.

Usage:
    python3 scripts/retry_failed.py --dry-run                 # list failed files
    python3 scripts/retry_failed.py                           # retry all failures
    python3 scripts/retry_failed.py --workers 10 --limit 100  # limited retry
    python3 scripts/retry_failed.py --log /path/to/other.log  # custom log file
"""

import argparse
import re
import sys
import os

# Add parent dir so we can import from reprocess_summarize
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from reprocess_summarize import (
    OUTPUT_BUCKET, PARSED_PREFIX, REGION,
    reprocess_one, log,
)
import boto3
from concurrent.futures import ThreadPoolExecutor, as_completed

DEFAULT_LOG = "/tmp/reprocess.log"


def extract_failed_filenames(log_path: str) -> list[str]:
    """Parse the reprocess log and extract filenames from FAILED lines."""
    failed = []
    pattern = re.compile(r"FAILED (.+?\.json):")
    with open(log_path) as f:
        for line in f:
            if "FAILED" not in line:
                continue
            m = pattern.search(line)
            if m:
                filename = m.group(1).strip()
                failed.append(filename)
    return failed


def main():
    parser = argparse.ArgumentParser(description="Retry failed reprocessing files")
    parser.add_argument("--log", default=DEFAULT_LOG, help=f"Path to reprocess log (default: {DEFAULT_LOG})")
    parser.add_argument("--dry-run", action="store_true", help="List failed files without processing")
    parser.add_argument("--workers", type=int, default=10, help="Parallel workers (default: 10)")
    parser.add_argument("--limit", type=int, default=None, help="Retry only first N failures")
    args = parser.parse_args()

    log.info("Parsing failed files from %s ...", args.log)
    failed_filenames = extract_failed_filenames(args.log)

    # Deduplicate (a file could have failed multiple times if log was appended)
    failed_filenames = list(dict.fromkeys(failed_filenames))

    if args.limit:
        failed_filenames = failed_filenames[: args.limit]

    log.info("Found %d unique failed files to retry", len(failed_filenames))

    if not failed_filenames:
        log.info("Nothing to retry.")
        return

    # Convert filenames back to full S3 keys
    keys = [PARSED_PREFIX + fn for fn in failed_filenames]

    if args.dry_run:
        for k in keys:
            print(k)
        log.info("Dry run complete. %d files would be retried.", len(keys))
        return

    s3 = boto3.client("s3", region_name=REGION)
    lambda_client = boto3.client("lambda", region_name=REGION)
    dynamodb = boto3.client("dynamodb", region_name=REGION)

    log.info("Starting retry with %d workers...", args.workers)
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
    log.info("RETRY COMPLETE: %d succeeded, %d failed out of %d total", succeeded, failed, len(keys))
    if errors:
        log.error("Still-failed files:")
        for e in errors:
            log.error("  %s: %s", e["key"], e["error"])


if __name__ == "__main__":
    main()

import json
import os
import boto3
import logging
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")
glue = boto3.client("glue")

ANALYTICS_BUCKET = os.environ.get("ANALYTICS_BUCKET", "")
TRANSCRIBE_BUCKET = os.environ.get("TRANSCRIBE_BUCKET", "")
TRANSCRIPT_KEY_PREFIX = os.environ.get("TRANSCRIPT_KEY_PREFIX", "parsedFiles/")
GLUE_DATABASE = os.environ.get("GLUE_DATABASE", "post_call_analytics")


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

        if "calls" in table_arn.lower() or "callrecords" in table_arn.lower():
            flat = flatten_call_record(new_image)
            if flat:
                calls_records.append(flat)
        elif "scorecards" in table_arn.lower():
            flat = flatten_scorecard_record(new_image)
            if flat:
                scorecards_records.append(flat)

    write_to_s3(calls_records, "calls")
    write_to_s3(scorecards_records, "scorecards")

    logger.info(f"Processed {len(calls_records)} calls, {len(scorecards_records)} scorecards")
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
        call_duration_seconds, transcript_s3_key, num_speakers = get_transcribe_metadata(file_name, call_id)
        answer_type = infer_answer_type(call_duration_seconds, num_speakers)

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


def get_transcribe_metadata(file_name: str, call_id: str) -> tuple[int, str, int]:
    """
    Try to get call duration and speaker count from Transcribe job output in S3.
    The transcripts are stored as: {TRANSCRIPT_KEY_PREFIX}{file_name}.json
    where file_name is the recording filename from the payload.
    Returns (duration_seconds, transcript_s3_key, num_speakers).
    """
    if not file_name:
        return 0, "", 0

    try:
        transcript_key = f"{TRANSCRIPT_KEY_PREFIX}{file_name}.json"

        response = s3.get_object(Bucket=TRANSCRIBE_BUCKET, Key=transcript_key)
        transcript_data = json.loads(response["Body"].read())

        ca = transcript_data.get("ConversationAnalytics", {})
        duration = float(ca.get("Duration", 0))

        speaker_labels = ca.get("SpeakerLabels", [])
        speakers = set()
        for label in speaker_labels:
            speaker = label.get("Speaker", "")
            if speaker:
                speakers.add(speaker)

        return int(duration), transcript_key, len(speakers)
    except Exception:
        return 0, "", 0


def infer_answer_type(duration_seconds: int, num_speakers: int) -> str:
    """
    Infer whether an outbound call was answered by a human, went to voicemail,
    or was not answered.
    """
    if duration_seconds == 0:
        return "Unknown"
    if duration_seconds < 10:
        return "NoAnswer"

    if num_speakers >= 2:
        return "Human"
    elif num_speakers == 1:
        return "Voicemail"
    else:
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
        # Strip partition keys from records — they're in the S3 path
        clean_recs = [{k: v for k, v in r.items() if k not in ("year", "month", "day")} for r in recs]
        body = "\n".join(json.dumps(r, default=str) for r in clean_recs)
        s3.put_object(
            Bucket=ANALYTICS_BUCKET,
            Key=s3_key,
            Body=body.encode("utf-8"),
            ContentType="application/json",
        )
        logger.info(f"Wrote {len(recs)} {table_name} records to s3://{ANALYTICS_BUCKET}/{s3_key}")

        # Auto-register partition in Glue if it doesn't exist yet
        ensure_partition(table_name, year, month, day)


def ensure_partition(table_name: str, year: str, month: str, day: str):
    """Register a partition in Glue catalog if it doesn't already exist."""
    try:
        table_resp = glue.get_table(DatabaseName=GLUE_DATABASE, Name=table_name)
        sd = table_resp["Table"]["StorageDescriptor"]
        partition_sd = {
            "Columns": sd["Columns"],
            "InputFormat": sd["InputFormat"],
            "OutputFormat": sd["OutputFormat"],
            "SerdeInfo": sd["SerdeInfo"],
            "Location": f"s3://{ANALYTICS_BUCKET}/{table_name}/year={year}/month={month}/day={day}/",
        }
        glue.batch_create_partition(
            DatabaseName=GLUE_DATABASE,
            TableName=table_name,
            PartitionInputList=[{
                "Values": [year, month, day],
                "StorageDescriptor": partition_sd,
            }],
        )
        logger.info(f"Registered partition {table_name}/year={year}/month={month}/day={day}")
    except glue.exceptions.AlreadyExistsException:
        pass  # Partition already registered
    except Exception as e:
        # batch_create_partition returns errors in the response, not exceptions
        # for already-existing partitions. Log but don't fail.
        error_msg = str(e)
        if "AlreadyExists" in error_msg:
            pass
        else:
            logger.warning(f"Failed to register partition {table_name}/{year}/{month}/{day}: {e}")

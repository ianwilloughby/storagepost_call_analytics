import json
import os
import uuid
import time
import boto3
from botocore.config import Config
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

bedrock_agent_runtime = boto3.client(
    "bedrock-agent-runtime",
    config=Config(read_timeout=120)
)
s3 = boto3.client("s3")
lambda_client = boto3.client("lambda")

AGENT_ID = os.environ["AGENT_ID"]
AGENT_ALIAS_ID = os.environ["AGENT_ALIAS_ID"]
JOBS_BUCKET = os.environ.get("JOBS_BUCKET", "")
FUNCTION_NAME = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "")


def lambda_handler(event, context):
    """Routes API Gateway requests or processes async jobs."""
    # Async job processing (invoked by self)
    if "async_job" in event:
        return process_async_job(event["async_job"])

    method = event.get("httpMethod", "")
    path = event.get("path", "")

    user_sub = event.get("requestContext", {}).get("authorizer", {}).get("claims", {}).get("sub", "anonymous")

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return error_response(400, "Invalid JSON body")

    if method == "POST" and path.endswith("/chat"):
        return handle_chat(body, user_sub)
    elif method == "POST" and path.endswith("/report"):
        return handle_report(body, user_sub)
    elif method == "GET" and path.endswith("/chat"):
        return handle_job_status(event)
    elif method == "GET" and path.endswith("/report"):
        return handle_job_status(event)
    elif method == "GET" and path.endswith("/reports"):
        return handle_list_reports(user_sub)
    else:
        return error_response(404, "Not found")


def handle_chat(body: dict, user_sub: str) -> dict:
    """Start an async chat job and return job ID immediately."""
    question = body.get("question", "").strip()
    session_id = body.get("session_id") or str(uuid.uuid4())

    if not question:
        return error_response(400, "question is required")
    if len(question) > 2000:
        return error_response(400, "question must be under 2000 characters")

    job_id = str(uuid.uuid4())
    logger.info(f"User {user_sub} asked: {question[:100]}... job_id={job_id}")

    # Store pending status
    store_job(job_id, {"status": "processing"})

    # Invoke self asynchronously
    lambda_client.invoke(
        FunctionName=FUNCTION_NAME,
        InvocationType="Event",
        Payload=json.dumps({
            "async_job": {
                "job_id": job_id,
                "type": "chat",
                "question": question,
                "session_id": session_id,
                "user_sub": user_sub,
            }
        }),
    )

    return success_response({"job_id": job_id, "status": "processing"})


def handle_report(body: dict, user_sub: str) -> dict:
    """Start an async report job and return job ID immediately."""
    report_type = body.get("report_type", "")
    date_from = body.get("date_from", "")
    date_to = body.get("date_to", "")

    report_prompts = {
        "daily_summary": f"Generate a daily call summary report for {date_from}. Include: total calls by direction, breakdown by answer type, average call duration, and top 5 agents by call volume.",
        "agent_performance": f"Generate an agent performance report from {date_from} to {date_to}. Include each agent's total calls, average scorecard score, resolution rate, and their strongest and weakest scoring categories. If scorecard data is not available for the requested dates, check what date range has scorecard data and use that instead, noting the actual dates used.",
        "outbound_callbacks": f"Generate an outbound callback report for {date_from}. How many outbound calls were made, how many were answered by a human, how many were voicemails, and how many were longer than 1 minute?",
    }

    prompt = report_prompts.get(report_type)
    if not prompt:
        return error_response(400, f"Unknown report_type. Valid: {list(report_prompts.keys())}")

    job_id = str(uuid.uuid4())
    logger.info(f"User {user_sub} report={report_type} job_id={job_id}")

    store_job(job_id, {"status": "processing"})

    lambda_client.invoke(
        FunctionName=FUNCTION_NAME,
        InvocationType="Event",
        Payload=json.dumps({
            "async_job": {
                "job_id": job_id,
                "type": "report",
                "prompt": prompt,
                "report_type": report_type,
                "date_from": date_from,
                "date_to": date_to,
                "user_sub": user_sub,
            }
        }),
    )

    return success_response({"job_id": job_id, "status": "processing"})


def process_async_job(job: dict):
    """Run the Bedrock agent call and store the result in S3."""
    job_id = job["job_id"]
    job_type = job["type"]

    try:
        if job_type == "chat":
            session_id = job["session_id"]
            response = bedrock_agent_runtime.invoke_agent(
                agentId=AGENT_ID,
                agentAliasId=AGENT_ALIAS_ID,
                sessionId=session_id,
                inputText=job["question"],
                enableTrace=False,
            )
            answer = ""
            for event in response["completion"]:
                chunk = event.get("chunk", {})
                answer += chunk.get("bytes", b"").decode("utf-8")

            store_job(job_id, {
                "status": "completed",
                "answer": answer,
                "session_id": session_id,
            })
        elif job_type == "report":
            session_id = str(uuid.uuid4())
            response = bedrock_agent_runtime.invoke_agent(
                agentId=AGENT_ID,
                agentAliasId=AGENT_ALIAS_ID,
                sessionId=session_id,
                inputText=job["prompt"],
            )
            report_text = ""
            for event in response["completion"]:
                chunk = event.get("chunk", {})
                report_text += chunk.get("bytes", b"").decode("utf-8")

            store_job(job_id, {
                "status": "completed",
                "report_type": job["report_type"],
                "date_from": job["date_from"],
                "date_to": job["date_to"],
                "report": report_text,
                "session_id": session_id,
            })
    except Exception as e:
        logger.error(f"Async job {job_id} error: {e}")
        store_job(job_id, {"status": "error", "error": str(e)})


def handle_job_status(event: dict) -> dict:
    """Poll for async job result."""
    params = event.get("queryStringParameters") or {}
    job_id = params.get("job_id", "")

    if not job_id:
        return error_response(400, "job_id query parameter is required")

    # Validate job_id format (UUID)
    try:
        uuid.UUID(job_id)
    except ValueError:
        return error_response(400, "Invalid job_id format")

    try:
        result = s3.get_object(Bucket=JOBS_BUCKET, Key=f"jobs/{job_id}.json")
        data = json.loads(result["Body"].read().decode("utf-8"))
        return success_response(data)
    except s3.exceptions.NoSuchKey:
        return error_response(404, "Job not found")
    except Exception as e:
        logger.error(f"Job status error: {e}")
        return error_response(500, "Failed to check job status")


def store_job(job_id: str, data: dict):
    """Store job result in S3."""
    s3.put_object(
        Bucket=JOBS_BUCKET,
        Key=f"jobs/{job_id}.json",
        Body=json.dumps(data),
        ContentType="application/json",
    )


def handle_list_reports(user_sub: str) -> dict:
    """Placeholder — in Phase 2, return saved reports from DynamoDB."""
    return success_response({"reports": [], "message": "Report history coming in Phase 2"})


def success_response(body: dict) -> dict:
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }


def error_response(status: int, message: str) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps({"error": message}),
    }

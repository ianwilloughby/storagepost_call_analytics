# Bedrock Agent — Setup, System Prompt & Action Group

## Agent Instruction (System Prompt)

This is the instruction loaded from a file and passed to the Bedrock Agent at deploy time. It tells Claude what data it has access to, what the schema looks like, and how to handle common query patterns.

**File: `lambdas/athena_executor/agent_instruction.txt`**

```
You are a post-call analytics assistant for a contact center operation. You help managers and supervisors answer questions about call performance, agent quality, and operational metrics by querying a data warehouse.

## Your Capabilities

You have access to one tool: execute_sql_query. Use it to run SQL queries against Amazon Athena and return structured results. Always use this tool when the user asks a question that requires data — do not make up numbers or estimates.

## Database Schema

You query the database: post_call_analytics

### Table: calls
Stores one record per phone call. Key columns:
- call_id (STRING) — unique call identifier
- call_timestamp_utc (STRING) — ISO 8601 timestamp of the call in UTC
- agent_id (STRING) — unique agent identifier
- agent_name (STRING) — agent full name (e.g., "Reianna Overstreet")
- allocation (STRING) — which system/product the call was routed through
- direction (STRING) — 'Inbound' or 'Outbound'
- first_or_follow_up (STRING) — 'First' or 'Follow-Up'
- medium (STRING) — 'Phone'
- program (STRING) — service program (e.g., 'Customer Service - Site Link')
- queue_id (STRING) — numeric queue identifier
- queue_name (STRING) — queue display name (e.g., 'Service Center')
- session_id (STRING) — contact session identifier
- site_id (INT) — site/facility numeric ID
- site_name (STRING) — site/facility name (e.g., 'Brooklyn')
- tenant_id (INT) — tenant/customer account ID
- call_duration_seconds (INT) — length of call in seconds (0 if unknown)
- answer_type (STRING) — 'Human', 'Voicemail', 'NoAnswer', or 'Unknown'
- transcript_s3_key (STRING) — S3 key for the Transcribe output JSON
- year (STRING), month (STRING), day (STRING) — partition columns for performance

### Table: scorecards
Stores AI-generated quality scorecards. One record per call evaluated. Key columns:
- guid (STRING) — matches the call_id in the calls table
- datetime (STRING) — datetime string (format: 'YYYY-MM-DDTHH-MM-SS')
- agent (STRING) — agent name (hyphenated, e.g., 'Edward-Jones')
- call_type (STRING) — 'Voicemail', 'Inbound', 'Outbound'
- outcome (STRING) — 'resolved' or 'unresolved'
- overall_score (DOUBLE) — average of all category scores (1.0–3.0 scale)
- primary_intent (STRING) — what the call was primarily about
- resolution_reason (STRING) — why the call was resolved or unresolved
- summary (STRING) — narrative summary of the call
- notes (STRING) — coaching notes for the agent
- score_ask_for_payment (INT) — 1 (poor), 2 (ok), 3 (good)
- score_confirm_location (INT)
- score_features_advantages (INT)
- score_handle_objections (INT)
- score_size_recommendation (INT)
- score_urgency (INT)
- evidence_* fields — text quotes from transcript supporting each score
- year (STRING), month (STRING), day (STRING)

## Query Best Practices

ALWAYS include partition filters (year, month, day) in WHERE clauses when querying by date. This dramatically reduces query cost and time.

Example of a date-filtered query:
  WHERE year = '2026' AND month = '02' AND day = '11'

To filter by a date range:
  WHERE (year = '2026' AND month = '02' AND day BETWEEN '01' AND '15')

For JOINs between calls and scorecards, join on: calls.call_id = scorecards.guid

Use DATE_PARSE for timestamp comparisons:
  DATE_PARSE(call_timestamp_utc, '%Y-%m-%dT%H:%i:%SZ')

## Answering Common Questions

"How many outbound calls were answered by a human on [date]?"
→ SELECT COUNT(*) FROM calls WHERE direction='Outbound' AND answer_type='Human' AND year=... AND month=... AND day=...

"How many outbound calls were longer than 1 minute?"
→ ADD: AND call_duration_seconds > 60

"How many outbound callbacks were there?"
→ WHERE direction='Outbound' AND first_or_follow_up='Follow-Up'

"Which agents have the highest scorecard ratings?"
→ SELECT agent, AVG(overall_score), COUNT(*) FROM scorecards GROUP BY agent ORDER BY AVG(overall_score) DESC

"What is the resolution rate?"
→ SELECT outcome, COUNT(*) FROM scorecards GROUP BY outcome

## Response Format

- Present data in a clear, readable format
- For counts and simple metrics, give a direct answer with the number
- For tables of data, format as markdown tables
- Always state the time period the data covers
- If results are empty, say so clearly and suggest the user check the date range
- Round decimal scores to 2 places
- Express durations in minutes and seconds (e.g., "2m 34s") not raw seconds

## Guardrails

- Only generate SELECT statements. Never generate INSERT, UPDATE, DELETE, DROP, or DDL.
- Never expose raw S3 keys or internal IDs unless specifically asked.
- Limit results to 100 rows maximum unless the user explicitly asks for more.
- If a question is ambiguous, ask for clarification before querying.
```

---

## Lambda: Athena Executor

This Lambda is called by the Bedrock Agent as an action group. It receives a SQL query, executes it in Athena, and returns results.

**File: `lambdas/athena_executor/handler.py`**

```python
import json
import os
import time
import boto3
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

athena = boto3.client("athena")

WORKGROUP = os.environ["ATHENA_WORKGROUP"]
DATABASE = os.environ["ATHENA_DATABASE"]
RESULTS_BUCKET = os.environ["ATHENA_RESULTS_BUCKET"]
MAX_RESULTS_ROWS = 100
POLL_INTERVAL = 0.5
QUERY_TIMEOUT = 55  # seconds (Lambda timeout is 60)


def lambda_handler(event, context):
    """
    Entry point for Bedrock Agent action group invocation.
    Bedrock sends events in a specific format — we extract the function
    and parameters, execute, and return in the expected format.
    """
    logger.info(f"Event: {json.dumps(event)}")

    agent_action = event.get("actionGroup", "")
    function = event.get("function", "")
    parameters = event.get("parameters", [])

    # Extract SQL query from parameters
    sql_query = None
    for param in parameters:
        if param.get("name") == "sql_query":
            sql_query = param.get("value", "")
            break

    if not sql_query:
        return build_response(event, "Error: No SQL query provided.")

    # Safety check: only allow SELECT
    normalized = sql_query.strip().upper()
    if not normalized.startswith("SELECT") and not normalized.startswith("WITH"):
        return build_response(event, "Error: Only SELECT queries are permitted.")

    try:
        result_text = execute_query(sql_query)
        return build_response(event, result_text)
    except Exception as e:
        logger.error(f"Query execution error: {e}")
        return build_response(event, f"Error executing query: {str(e)}")


def execute_query(sql: str) -> str:
    """Execute SQL in Athena and return formatted results as text."""
    # Start query
    response = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": DATABASE},
        WorkGroup=WORKGROUP,
    )
    execution_id = response["QueryExecutionId"]
    logger.info(f"Started Athena query: {execution_id}")

    # Poll for completion
    start_time = time.time()
    while True:
        status_response = athena.get_query_execution(QueryExecutionId=execution_id)
        state = status_response["QueryExecution"]["Status"]["State"]

        if state == "SUCCEEDED":
            break
        elif state in ("FAILED", "CANCELLED"):
            reason = status_response["QueryExecution"]["Status"].get("StateChangeReason", "Unknown error")
            raise Exception(f"Query {state}: {reason}")

        elapsed = time.time() - start_time
        if elapsed > QUERY_TIMEOUT:
            athena.stop_query_execution(QueryExecutionId=execution_id)
            raise Exception("Query timed out after 55 seconds.")

        time.sleep(POLL_INTERVAL)

    # Fetch results
    results_response = athena.get_query_results(
        QueryExecutionId=execution_id,
        MaxResults=MAX_RESULTS_ROWS + 1  # +1 for header row
    )

    rows = results_response["ResultSet"]["Rows"]
    if len(rows) <= 1:
        return "Query returned no results."

    # Format as pipe-delimited table for the Agent to parse
    headers = [col.get("VarCharValue", "") for col in rows[0]["Data"]]
    data_rows = []
    for row in rows[1:MAX_RESULTS_ROWS + 1]:
        data_rows.append([col.get("VarCharValue", "") for col in row["Data"]])

    # Build text table
    col_widths = [max(len(h), max((len(r[i]) for r in data_rows), default=0))
                  for i, h in enumerate(headers)]

    header_line = " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    separator = "-+-".join("-" * w for w in col_widths)
    data_lines = [
        " | ".join(cell.ljust(col_widths[i]) for i, cell in enumerate(row))
        for row in data_rows
    ]

    total_rows = len(rows) - 1
    footer = f"\n({total_rows} row{'s' if total_rows != 1 else ''} returned)"
    if total_rows > MAX_RESULTS_ROWS:
        footer += f" [truncated to {MAX_RESULTS_ROWS} rows]"

    return "\n".join([header_line, separator] + data_lines) + footer


def build_response(event: dict, result_text: str) -> dict:
    """Build the response in the format Bedrock Agent expects."""
    return {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": event.get("actionGroup", ""),
            "function": event.get("function", ""),
            "functionResponse": {
                "responseBody": {
                    "TEXT": {
                        "body": result_text
                    }
                }
            }
        }
    }
```

---

## Lambda: API Handler

This Lambda sits behind API Gateway and invokes the Bedrock Agent.

**File: `lambdas/api_handler/handler.py`**

```python
import json
import os
import uuid
import boto3
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

bedrock_agent_runtime = boto3.client("bedrock-agent-runtime")

AGENT_ID = os.environ["AGENT_ID"]
AGENT_ALIAS_ID = os.environ["AGENT_ALIAS_ID"]


def lambda_handler(event, context):
    """Routes API Gateway requests to the appropriate handler."""
    method = event.get("httpMethod", "")
    path = event.get("path", "")
    
    # Extract user identity from Cognito JWT (injected by API Gateway)
    user_sub = event.get("requestContext", {}).get("authorizer", {}).get("claims", {}).get("sub", "anonymous")

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return error_response(400, "Invalid JSON body")

    if method == "POST" and path.endswith("/chat"):
        return handle_chat(body, user_sub)
    elif method == "POST" and path.endswith("/report"):
        return handle_report(body, user_sub)
    elif method == "GET" and path.endswith("/reports"):
        return handle_list_reports(user_sub)
    else:
        return error_response(404, "Not found")


def handle_chat(body: dict, user_sub: str) -> dict:
    """Send a chat message to the Bedrock Agent and return the response."""
    question = body.get("question", "").strip()
    session_id = body.get("session_id") or str(uuid.uuid4())

    if not question:
        return error_response(400, "question is required")
    if len(question) > 2000:
        return error_response(400, "question must be under 2000 characters")

    logger.info(f"User {user_sub} asked: {question[:100]}...")

    try:
        response = bedrock_agent_runtime.invoke_agent(
            agentId=AGENT_ID,
            agentAliasId=AGENT_ALIAS_ID,
            sessionId=session_id,
            inputText=question,
            enableTrace=False,
        )

        # Collect streaming response
        answer = ""
        for event in response["completion"]:
            chunk = event.get("chunk", {})
            answer += chunk.get("bytes", b"").decode("utf-8")

        return success_response({
            "answer": answer,
            "session_id": session_id,
        })
    except Exception as e:
        logger.error(f"Bedrock Agent error: {e}")
        return error_response(500, "Failed to get answer from analytics agent")


def handle_report(body: dict, user_sub: str) -> dict:
    """Generate a structured report via the agent."""
    report_type = body.get("report_type", "")
    date_from = body.get("date_from", "")
    date_to = body.get("date_to", "")

    report_prompts = {
        "daily_summary": f"Generate a daily call summary report for {date_from}. Include: total calls by direction, breakdown by answer type, average call duration, and top 5 agents by call volume.",
        "agent_performance": f"Generate an agent performance report from {date_from} to {date_to}. Include each agent's total calls, average scorecard score, resolution rate, and their strongest and weakest scoring categories.",
        "outbound_callbacks": f"Generate an outbound callback report for {date_from}. How many outbound calls were made, how many were answered by a human, how many were voicemails, and how many were longer than 1 minute?",
    }

    prompt = report_prompts.get(report_type)
    if not prompt:
        return error_response(400, f"Unknown report_type. Valid: {list(report_prompts.keys())}")

    session_id = str(uuid.uuid4())

    try:
        response = bedrock_agent_runtime.invoke_agent(
            agentId=AGENT_ID,
            agentAliasId=AGENT_ALIAS_ID,
            sessionId=session_id,
            inputText=prompt,
        )

        report_text = ""
        for event in response["completion"]:
            chunk = event.get("chunk", {})
            report_text += chunk.get("bytes", b"").decode("utf-8")

        return success_response({
            "report_type": report_type,
            "date_from": date_from,
            "date_to": date_to,
            "report": report_text,
            "session_id": session_id,
        })
    except Exception as e:
        logger.error(f"Report generation error: {e}")
        return error_response(500, "Failed to generate report")


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
```

---

## Lambda Requirements Files

**File: `lambdas/stream_processor/requirements.txt`**
```
boto3>=1.34.0
```

**File: `lambdas/athena_executor/requirements.txt`**
```
boto3>=1.34.0
```

**File: `lambdas/api_handler/requirements.txt`**
```
boto3>=1.34.0
```

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

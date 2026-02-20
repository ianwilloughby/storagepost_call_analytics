"""Unit tests for athena_executor handler — SQL validation and response formatting."""
import os
import sys
import json
import unittest

# Ensure the handler can be imported
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "athena_executor"))

# Set required env vars before import
os.environ.setdefault("ATHENA_WORKGROUP", "test-workgroup")
os.environ.setdefault("ATHENA_DATABASE", "test_db")
os.environ.setdefault("ATHENA_RESULTS_BUCKET", "test-results-bucket")

from handler import lambda_handler, build_response


class TestSQLValidation(unittest.TestCase):
    """Test that only SELECT/WITH queries are allowed."""

    def _make_event(self, sql_query):
        return {
            "actionGroup": "AthenaQueryExecutor",
            "function": "execute_sql_query",
            "parameters": [{"name": "sql_query", "value": sql_query}],
        }

    def test_reject_insert(self):
        event = self._make_event("INSERT INTO calls VALUES ('x')")
        result = lambda_handler(event, None)
        body = result["response"]["functionResponse"]["responseBody"]["TEXT"]["body"]
        self.assertIn("Only SELECT queries", body)

    def test_reject_delete(self):
        event = self._make_event("DELETE FROM calls WHERE call_id='123'")
        result = lambda_handler(event, None)
        body = result["response"]["functionResponse"]["responseBody"]["TEXT"]["body"]
        self.assertIn("Only SELECT queries", body)

    def test_reject_drop(self):
        event = self._make_event("DROP TABLE calls")
        result = lambda_handler(event, None)
        body = result["response"]["functionResponse"]["responseBody"]["TEXT"]["body"]
        self.assertIn("Only SELECT queries", body)

    def test_reject_update(self):
        event = self._make_event("UPDATE calls SET agent_name='x'")
        result = lambda_handler(event, None)
        body = result["response"]["functionResponse"]["responseBody"]["TEXT"]["body"]
        self.assertIn("Only SELECT queries", body)

    def test_reject_create(self):
        event = self._make_event("CREATE TABLE evil (id INT)")
        result = lambda_handler(event, None)
        body = result["response"]["functionResponse"]["responseBody"]["TEXT"]["body"]
        self.assertIn("Only SELECT queries", body)

    def test_no_query_provided(self):
        event = {
            "actionGroup": "AthenaQueryExecutor",
            "function": "execute_sql_query",
            "parameters": [],
        }
        result = lambda_handler(event, None)
        body = result["response"]["functionResponse"]["responseBody"]["TEXT"]["body"]
        self.assertIn("No SQL query provided", body)

    def test_empty_query(self):
        event = self._make_event("")
        result = lambda_handler(event, None)
        body = result["response"]["functionResponse"]["responseBody"]["TEXT"]["body"]
        self.assertIn("No SQL query provided", body)

    def test_select_with_leading_whitespace(self):
        event = self._make_event("  SELECT COUNT(*) FROM calls")
        # This will try to actually execute the query and fail (no real Athena),
        # but the point is it passes the validation step
        result = lambda_handler(event, None)
        body = result["response"]["functionResponse"]["responseBody"]["TEXT"]["body"]
        self.assertNotIn("Only SELECT queries", body)

    def test_with_cte_allowed(self):
        event = self._make_event("WITH cte AS (SELECT * FROM calls) SELECT * FROM cte")
        result = lambda_handler(event, None)
        body = result["response"]["functionResponse"]["responseBody"]["TEXT"]["body"]
        self.assertNotIn("Only SELECT queries", body)


class TestBuildResponse(unittest.TestCase):
    def test_response_format(self):
        event = {"actionGroup": "TestGroup", "function": "test_func"}
        response = build_response(event, "Hello world")
        self.assertEqual(response["messageVersion"], "1.0")
        self.assertEqual(response["response"]["actionGroup"], "TestGroup")
        self.assertEqual(response["response"]["function"], "test_func")
        body = response["response"]["functionResponse"]["responseBody"]["TEXT"]["body"]
        self.assertEqual(body, "Hello world")

    def test_response_with_empty_event(self):
        response = build_response({}, "test result")
        self.assertEqual(response["response"]["actionGroup"], "")
        self.assertEqual(response["response"]["function"], "")


if __name__ == "__main__":
    unittest.main()

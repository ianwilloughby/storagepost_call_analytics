"""Unit tests for stream_processor handler — flatten functions."""
import os
import sys
import json
import unittest

# Ensure the handler can be imported
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "stream_processor"))

# Set required env vars before import
os.environ.setdefault("ANALYTICS_BUCKET", "test-bucket")
os.environ.setdefault("TRANSCRIBE_BUCKET", "test-transcribe-bucket")

from handler import (
    deserialize_dynamodb_value,
    flatten_call_record,
    flatten_scorecard_record,
)


class TestDeserializeDynamoDBValue(unittest.TestCase):
    def test_string(self):
        self.assertEqual(deserialize_dynamodb_value({"S": "hello"}), "hello")

    def test_number_int(self):
        self.assertEqual(deserialize_dynamodb_value({"N": "42"}), 42)

    def test_number_float(self):
        self.assertAlmostEqual(deserialize_dynamodb_value({"N": "3.14"}), 3.14)

    def test_bool(self):
        self.assertTrue(deserialize_dynamodb_value({"BOOL": True}))

    def test_null(self):
        self.assertIsNone(deserialize_dynamodb_value({"NULL": True}))

    def test_map(self):
        result = deserialize_dynamodb_value({
            "M": {
                "name": {"S": "Alice"},
                "age": {"N": "30"},
            }
        })
        self.assertEqual(result, {"name": "Alice", "age": 30})

    def test_list(self):
        result = deserialize_dynamodb_value({
            "L": [{"S": "a"}, {"N": "1"}]
        })
        self.assertEqual(result, ["a", 1])

    def test_string_set(self):
        result = deserialize_dynamodb_value({"SS": ["a", "b", "c"]})
        self.assertEqual(sorted(result), ["a", "b", "c"])

    def test_number_set(self):
        result = deserialize_dynamodb_value({"NS": ["1", "2.5"]})
        self.assertEqual(result, [1.0, 2.5])


class TestFlattenCallRecord(unittest.TestCase):
    def _make_image(self, call_id="test-123", timestamp="2026-02-11T10:30:00Z", payload=None):
        if payload is None:
            payload = {
                "agentId": "agent-1",
                "agentName": "John Doe",
                "direction": "Outbound",
                "firstOrFollowUp": "First",
                "medium": "Phone",
                "queueName": "Service Center",
                "siteName": "Brooklyn",
            }
        return {
            "callId": {"S": call_id},
            "callTimestampUTC": {"S": timestamp},
            "payload": {"M": {k: {"S": str(v)} for k, v in payload.items()}},
        }

    def test_basic_flatten(self):
        image = self._make_image()
        result = flatten_call_record(image)
        self.assertIsNotNone(result)
        self.assertEqual(result["call_id"], "test-123")
        self.assertEqual(result["agent_name"], "John Doe")
        self.assertEqual(result["direction"], "Outbound")
        self.assertEqual(result["year"], "2026")
        self.assertEqual(result["month"], "02")
        self.assertEqual(result["day"], "11")

    def test_missing_call_id(self):
        image = {"callId": {"S": ""}}
        result = flatten_call_record(image)
        self.assertIsNone(result)

    def test_no_call_id_key(self):
        image = {}
        result = flatten_call_record(image)
        self.assertIsNone(result)

    def test_bad_timestamp(self):
        image = self._make_image(timestamp="not-a-date")
        result = flatten_call_record(image)
        self.assertIsNotNone(result)
        self.assertEqual(result["year"], "unknown")


class TestFlattenScorecardRecord(unittest.TestCase):
    def _make_image(self, guid="sc-123", datetime_val="2026-02-11T14-30-00"):
        return {
            "guid": {"S": guid},
            "datetime": {"S": datetime_val},
            "agent": {"S": "John-Doe"},
            "callType": {"S": "Outbound"},
            "outcome": {"S": "resolved"},
            "overallScore": {"N": "2.5"},
            "primaryIntent": {"S": "Inquiry"},
            "summary": {"S": "Customer asked about pricing"},
            "scores": {"M": {
                "askForPayment": {"M": {
                    "score": {"N": "3"},
                    "evidence": {"S": "Agent asked for payment"},
                }},
                "urgency": {"M": {
                    "score": {"N": "2"},
                    "evidence": {"S": "Some urgency shown"},
                }},
            }},
        }

    def test_basic_flatten(self):
        image = self._make_image()
        result = flatten_scorecard_record(image)
        self.assertIsNotNone(result)
        self.assertEqual(result["guid"], "sc-123")
        self.assertEqual(result["agent"], "John-Doe")
        self.assertEqual(result["overall_score"], 2.5)
        self.assertEqual(result["score_ask_for_payment"], 3)
        self.assertEqual(result["score_urgency"], 2)
        self.assertEqual(result["year"], "2026")
        self.assertEqual(result["month"], "02")
        self.assertEqual(result["day"], "11")

    def test_missing_guid(self):
        image = {"guid": {"S": ""}}
        result = flatten_scorecard_record(image)
        self.assertIsNone(result)

    def test_missing_scores(self):
        image = {
            "guid": {"S": "sc-456"},
            "datetime": {"S": "2026-03-01T10-00-00"},
            "agent": {"S": "Jane-Smith"},
            "outcome": {"S": "unresolved"},
            "overallScore": {"N": "1.5"},
        }
        result = flatten_scorecard_record(image)
        self.assertIsNotNone(result)
        self.assertEqual(result["score_ask_for_payment"], 0)
        self.assertEqual(result["score_urgency"], 0)


if __name__ == "__main__":
    unittest.main()

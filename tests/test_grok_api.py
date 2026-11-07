"""The live Grok client builds a Responses API request and reads the JSON text back."""

import json
import os
import unittest
from urllib.error import HTTPError

from migration.grok_api import GrokApi, client_for

SHAPE = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["sentiment", "issueCategory", "summary"],
    "properties": {
        "sentiment": {"type": "string", "enum": ["positive", "neutral", "negative"]},
        "issueCategory": {"type": "string", "enum": ["delivery", "product", "billing", "other"]},
        "summary": {"type": "string", "maxLength": 280},
    },
}
CLASSIFIED = (
    '{"sentiment":"negative","issueCategory":"delivery","summary":"Crushed box and a late shipment."}'
)


class Response:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class GrokApiTests(unittest.TestCase):
    def test_classify_posts_the_note_and_schema(self):
        seen = {}

        def opener(request):
            seen["url"] = request.full_url
            seen["auth"] = request.get_header("Authorization")
            seen["body"] = json.loads(request.data.decode("utf-8"))
            payload = {
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": CLASSIFIED}],
                    }
                ]
            }
            return Response(json.dumps(payload).encode("utf-8"))

        text = GrokApi("test-key", opener=opener).classify("Box arrived crushed.", SHAPE)

        self.assertEqual(text, CLASSIFIED)
        self.assertEqual(seen["url"], "https://api.x.ai/v1/responses")
        self.assertEqual(seen["auth"], "Bearer test-key")
        self.assertEqual(seen["body"]["model"], "grok-4.7")
        self.assertIn("Box arrived crushed.", seen["body"]["input"])
        schema = seen["body"]["text"]["format"]["schema"]
        self.assertNotIn("$schema", schema)
        self.assertEqual(schema["required"], ["sentiment", "issueCategory", "summary"])

    def test_classify_reads_output_text(self):
        def opener(request):
            return Response(json.dumps({"output_text": CLASSIFIED}).encode("utf-8"))

        self.assertEqual(GrokApi("test-key", opener=opener).classify("note", SHAPE), CLASSIFIED)

    def test_classify_reports_an_http_error_without_the_key(self):
        def opener(request):
            raise HTTPError(request.full_url, 401, "Unauthorized", hdrs=None, fp=_Body(b"bad key"))

        with self.assertRaises(RuntimeError) as raised:
            GrokApi("secret-key", opener=opener).classify("note", SHAPE)
        self.assertIn("401", str(raised.exception))
        self.assertNotIn("secret-key", str(raised.exception))

    def test_client_requires_a_key_when_the_spec_classifies_a_field(self):
        spec = {"fieldMapping": {"mappings": [{"transform": "grok"}]}}
        previous = os.environ.pop("XAI_API_KEY", None)
        try:
            with self.assertRaises(RuntimeError) as raised:
                client_for(spec)
            self.assertIn("XAI_API_KEY", str(raised.exception))
        finally:
            if previous is not None:
                os.environ["XAI_API_KEY"] = previous

    def test_client_is_idle_when_the_spec_has_no_grok_mapping(self):
        spec = {"fieldMapping": {"mappings": [{"transform": "copy"}]}}
        client = client_for(spec)
        with self.assertRaises(RuntimeError):
            client.classify("note", SHAPE)


class _Body:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return self.payload

    def close(self):
        return None


if __name__ == "__main__":
    unittest.main()

"""Live Grok classification for mappings whose transform is grok."""

import json
import os
import urllib.error
import urllib.request


API_URL = "https://api.x.ai/v1/responses"
MODEL = "grok-4.7"


class GrokApi:
    """Call the xAI Responses API and return the model's JSON text."""

    def __init__(self, api_key, model=MODEL, url=API_URL, opener=None):
        self.api_key = api_key
        self.model = model
        self.url = url
        self.opener = opener or urllib.request.urlopen

    def classify(self, text, target_shape):
        body = {
            "model": self.model,
            "input": (
                "Classify this customer or support note. "
                "Return only the JSON object described by the schema.\n\n" + text
            ),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "classification",
                    "strict": True,
                    "schema": _schema(target_shape),
                }
            },
        }
        request = urllib.request.Request(
            self.url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self.opener(request) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            try:
                detail = error.read().decode("utf-8", errors="replace")
            finally:
                error.close()
            raise RuntimeError(f"Grok API returned {error.code}: {detail}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"Grok API request failed: {error.reason}") from error
        return _output_text(json.loads(raw.decode("utf-8")))


def client_for(spec):
    """Return a live client when the spec classifies a field. Otherwise return a client that is never called."""
    needs_grok = any(item.get("transform") == "grok" for item in spec["fieldMapping"]["mappings"])
    if not needs_grok:
        return _IdleGrok()
    api_key = os.environ.get("XAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("XAI_API_KEY is required when the spec classifies a field")
    return GrokApi(api_key)


def _schema(target_shape):
    return {key: value for key, value in target_shape.items() if not key.startswith("$")}


def _output_text(payload):
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                return content["text"]
    raise RuntimeError("Grok response did not include output text")


class _IdleGrok:
    def classify(self, text, target_shape):
        raise RuntimeError("this spec has no grok mapping")

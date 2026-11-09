"""BSON encoding for the MongoDB load, and the local .env URI."""

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from bson import Int64

from migration.__main__ import _dotenv_value, apply_local_env, mongodb_uri, timestamped_database
from migration.store import _as_bson


class BsonEncodingTests(unittest.TestCase):
    def test_ints_become_long_and_bools_stay_bools(self):
        encoded = _as_bson({"_id": 42, "isCurrent": True, "lines": [{"quantity": 2}]})
        self.assertIsInstance(encoded["_id"], Int64)
        self.assertIs(encoded["isCurrent"], True)
        self.assertIsInstance(encoded["lines"][0]["quantity"], Int64)
        self.assertEqual(int(encoded["_id"]), 42)


class DotenvUriTests(unittest.TestCase):
    def test_reads_a_quoted_uri_and_leaves_existing_env_in_place(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                '# local only\nMONGODB_URI="mongodb+srv://demo:p=1@cluster.example"\n',
                encoding="utf-8",
            )
            self.assertEqual(
                _dotenv_value(env_file, "MONGODB_URI"),
                "mongodb+srv://demo:p=1@cluster.example",
            )
            self.assertEqual(
                mongodb_uri("", {}, env_file),
                "mongodb+srv://demo:p=1@cluster.example",
            )
            self.assertEqual(
                mongodb_uri("", {"MONGODB_URI": "mongodb://from-env"}, env_file),
                "mongodb://from-env",
            )
            self.assertEqual(
                mongodb_uri("mongodb://from-flag", os.environ, env_file),
                "mongodb://from-flag",
            )

    def test_fills_a_missing_api_key_from_the_env_file(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text('XAI_API_KEY="xai-example"\n', encoding="utf-8")
            environ = {}
            apply_local_env(env_file, environ)
            self.assertEqual(environ["XAI_API_KEY"], "xai-example")
            environ["XAI_API_KEY"] = "xai-already"
            apply_local_env(env_file, environ)
            self.assertEqual(environ["XAI_API_KEY"], "xai-already")


class DatabaseNameTests(unittest.TestCase):
    def test_name_contains_the_local_timestamp(self):
        name = timestamped_database(datetime(2026, 11, 9, 19, 5, 30))
        self.assertEqual(name, "order_management_2026-11-09_190530")
        self.assertLess(len(name), 64)
        self.assertFalse(any(char in name for char in '/\\. "$'))

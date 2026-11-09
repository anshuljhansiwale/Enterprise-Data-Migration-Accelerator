"""Stub-Grok coverage for the approved order-management load."""

import copy
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime
from pathlib import Path

from migration.__main__ import main
from migration.hashing import spec_hash
from migration.load import (
    SpecRejected,
    UnrecognizedPredicate,
    _embed,
    _lookup,
    compare_source_counts,
    format_count_report,
    load,
)
from migration.runlog import install, log_run
from migration.store import MemoryDatabase, apply_bson_types, shared_client

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "fixtures" / "order-management" / "approved-spec.json"
NOTE = "The box arrived crushed and the shipment was late."
CLASSIFIED = json.dumps(
    {
        "sentiment": "negative",
        "issueCategory": "delivery",
        "summary": "Crushed box and a late shipment.",
    }
)


class StubGrok:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def classify(self, text, target_shape):
        self.calls.append(text)
        response = self.responses[text]
        if isinstance(response, list):
            return response.pop(0)
        return response


def source_rows():
    return {
        "customers": [
            {
                "customer_id": 42,
                "full_name": "Sample Customer",
                "email": "customer@example.com",
                "created_at": "2020-01-15T00:00:00",
            }
        ],
        "addresses": [
            {
                "address_id": 7,
                "customer_id": 42,
                "label": "home",
                "line1": "1 Sample Street",
                "line2": None,
                "city": "Austin",
                "region": None,
                "postal_code": "78701",
                "country_code": "US",
                "is_current": 1,
            },
            {
                "address_id": 8,
                "customer_id": 42,
                "label": "old",
                "line1": "2 Sample Street",
                "line2": "Apt 2",
                "city": "Austin",
                "region": "TX",
                "postal_code": "78702",
                "country_code": "US",
                "is_current": 0,
            },
        ],
        "products": [
            {
                "product_id": 1001,
                "sku": "BK-1001",
                "type": "book",
                "name": "Sample Field Guide",
                "price_cents": 2400,
                "author": "Sample Author",
                "isbn": "9780000000001",
                "page_count": 320,
                "brand": "Stray Brand",
            },
            {
                "product_id": 2001,
                "sku": "EL-2001",
                "type": "electronics",
                "name": "Sample Radio",
                "price_cents": 8900,
                "author": "Stray Author",
                "brand": "Sample Brand",
                "warranty_months": 24,
            },
            {
                "product_id": 3001,
                "sku": "CL-3001",
                "type": "clothing",
                "name": "Sample Jacket",
                "price_cents": 12000,
                "size": "M",
                "color": "navy",
                "material": "wool",
            },
        ],
        "orders": [
            {
                "order_id": 9001,
                "customer_id": 42,
                "ship_address_id": 7,
                "placed_at": "2024-11-02T15:04:00",
                "status": "shipped",
                "notes": NOTE,
            },
            {
                "order_id": 9002,
                "customer_id": 42,
                "ship_address_id": 7,
                "placed_at": "2024-10-01T12:00:00",
                "status": "placed",
                "notes": None,
            },
            {
                "order_id": 9003,
                "customer_id": 42,
                "ship_address_id": 8,
                "placed_at": "2024-09-01T12:00:00",
                "status": "placed",
                "notes": "   ",
            },
        ],
        "order_lines": [
            {
                "order_line_id": 1,
                "order_id": 9001,
                "product_id": 1001,
                "quantity": 2,
                "unit_price_cents": 2400,
            },
            {
                "order_line_id": 2,
                "order_id": 9001,
                "product_id": 2001,
                "quantity": 1,
                "unit_price_cents": 8900,
            },
        ],
    }


class LoadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))

    def test_mappings_indexes_and_rerun(self):
        database = MemoryDatabase()
        grok = StubGrok({NOTE: CLASSIFIED})
        rejects = []
        calls = load(self.spec, source_rows(), database, grok, rejects)

        self.assertEqual(calls, 1)
        self.assertEqual(rejects, [])
        self.assertEqual(grok.calls, [NOTE])
        self.assertEqual(set(database.docs), {"customers", "addresses", "products", "orders"})
        self.assertEqual(
            [item["name"] for item in database.indexes],
            ["type_1_priceCents_1", "customer.customerId_1_placedAt_-1"],
        )
        self.assertEqual(
            list(database.indexes[0]["keys"].items()),
            [("type", 1), ("priceCents", 1)],
        )
        self.assertEqual(
            list(database.indexes[1]["keys"].items()),
            [("customer.customerId", 1), ("placedAt", -1)],
        )
        self.assertEqual(
            [item["partialFilter"] for item in database.indexes],
            [None, None],
        )
        self.assertEqual(
            sorted(item["collection"] for item in database.indexes),
            ["orders", "products"],
        )
        self.assertEqual(
            [item["name"] for item in self.spec["documentModel"]["collections"]],
            ["customers", "addresses", "products", "orders"],
        )
        grok_mappings = [
            item for item in self.spec["fieldMapping"]["mappings"] if item["transform"] == "grok"
        ]
        self.assertEqual(
            [
                (
                    item["sourceTable"],
                    item["sourceColumn"],
                    item["targetCollection"],
                    item["targetPath"],
                )
                for item in grok_mappings
            ],
            [("orders", "notes", "orders", "notesClassification")],
        )
        for collection in self.spec["documentModel"]["collections"]:
            self.assertEqual(
                database.validators[collection["name"]],
                {"$jsonSchema": collection["validation"]},
            )
            self.assertEqual(database.validation_levels[collection["name"]], "strict")
            self.assertEqual(database.validation_actions[collection["name"]], "error")

        customer = database.get("customers", 42)
        self.assertEqual(customer["fullName"], "Sample Customer")
        self.assertEqual(customer["email"], "customer@example.com")
        self.assertEqual(customer["createdAt"], datetime.fromisoformat("2020-01-15T00:00:00"))

        current = database.get("addresses", 7)
        self.assertTrue(current["isCurrent"])
        self.assertNotIn("line2", current)
        self.assertNotIn("region", current)
        previous = database.get("addresses", 8)
        self.assertFalse(previous["isCurrent"])
        self.assertEqual(previous["line2"], "Apt 2")

        book = database.get("products", 1001)
        self.assertEqual(book["author"], "Sample Author")
        self.assertEqual(book["pageCount"], 320)
        self.assertNotIn("brand", book)
        radio = database.get("products", 2001)
        self.assertEqual(radio["brand"], "Sample Brand")
        self.assertEqual(radio["warrantyMonths"], 24)
        self.assertNotIn("author", radio)
        jacket = database.get("products", 3001)
        self.assertEqual(jacket["size"], "M")
        self.assertEqual(jacket["material"], "wool")

        order = database.get("orders", 9001)
        self.assertEqual(order["customer"]["fullName"], "Sample Customer")
        self.assertEqual(order["customer"]["email"], "customer@example.com")
        self.assertEqual(order["customer"]["customerId"], 42)
        self.assertEqual(order["shipAddressId"], 7)
        self.assertEqual(order["notesRaw"], NOTE)
        self.assertEqual(order["notesClassification"]["issueCategory"], "delivery")
        self.assertEqual(
            order["lines"],
            [
                {"orderLineId": 1, "productId": 1001, "quantity": 2, "unitPriceCents": 2400},
                {"orderLineId": 2, "productId": 2001, "quantity": 1, "unitPriceCents": 8900},
            ],
        )
        blank = database.get("orders", 9002)
        self.assertNotIn("notesRaw", blank)
        self.assertNotIn("notesClassification", blank)
        whitespace = database.get("orders", 9003)
        self.assertNotIn("notesRaw", whitespace)
        self.assertNotIn("notesClassification", whitespace)

        report = compare_source_counts(self.spec, source_rows(), database, rejects)
        self.assertEqual(
            [
                (item["sourceTable"], item["sourceRows"], item["unloadedRows"], item["documents"])
                for item in report
            ],
            [
                ("customers", 1, 0, 1),
                ("addresses", 2, 0, 2),
                ("products", 3, 0, 3),
                ("orders", 3, 0, 3),
                ("order_lines", 2, 0, 2),
            ],
        )
        self.assertTrue(all(item["match"] for item in report))
        self.assertIn(
            "orders -> orders: source 3, unloaded 0, documents 3",
            format_count_report(report),
        )
        self.assertIn(
            "order_lines -> orders embedded: source 2, unloaded 0, documents 2",
            format_count_report(report),
        )
        again = load(self.spec, source_rows(), database, grok, rejects)
        self.assertEqual(again, 0)
        self.assertEqual(grok.calls, [NOTE])
        self.assertEqual(database.count("customers"), 1)
        self.assertEqual(database.count("addresses"), 2)
        self.assertEqual(database.count("products"), 3)
        self.assertEqual(database.count("orders"), 3)
        self.assertEqual(len(database.indexes), 2)
        self.assertEqual(
            compare_source_counts(self.spec, source_rows(), database, rejects),
            report,
        )

    def test_source_counts_leave_rejected_rows_unloaded(self):
        database = MemoryDatabase()
        rows = source_rows()
        bad = "This note should be rejected."
        rows["orders"][0]["notes"] = bad
        grok = StubGrok({bad: json.dumps({"sentiment": "nope"})})
        rejects = []
        load(self.spec, rows, database, grok, rejects)

        self.assertEqual(len(rejects), 1)
        self.assertEqual(rejects[0]["sourceTable"], "orders")
        self.assertEqual(rejects[0]["primaryKey"], 9001)
        self.assertEqual(rejects[0]["targetCollection"], "orders")
        self.assertEqual(rejects[0]["targetPath"], "notesClassification")
        self.assertEqual(rejects[0]["reason"], "missing issueCategory")
        self.assertIsNone(database.get("orders", 9001))
        self.assertIsNotNone(database.get("orders", 9002))
        self.assertIsNotNone(database.get("orders", 9003))
        report = compare_source_counts(self.spec, rows, database, rejects)
        self.assertEqual(
            [
                (item["sourceTable"], item["sourceRows"], item["unloadedRows"], item["documents"])
                for item in report
            ],
            [
                ("customers", 1, 0, 1),
                ("addresses", 2, 0, 2),
                ("products", 3, 0, 3),
                ("orders", 3, 1, 2),
                ("order_lines", 2, 2, 0),
            ],
        )
        self.assertTrue(all(item["match"] for item in report))

        load(self.spec, rows, database, grok, rejects)
        self.assertEqual(database.count("customers"), 1)
        self.assertEqual(database.count("addresses"), 2)
        self.assertEqual(database.count("products"), 3)
        self.assertEqual(database.count("orders"), 2)
        self.assertIsNone(database.get("orders", 9001))

    def test_invalid_grok_output_is_rejected_and_not_loaded(self):
        database = MemoryDatabase()
        database.upsert(
            "orders",
            {"_id": 9001, "status": "already loaded"},
        )
        rows = source_rows()
        rows["orders"] = [rows["orders"][0], copy.deepcopy(rows["orders"][0])]
        rows["orders"][1]["order_id"] = 9004
        rows["orders"][1]["notes"] = "Wrong shape."
        extra = json.dumps(
            {
                "sentiment": "negative",
                "issueCategory": "delivery",
                "summary": "x",
                "confidence": 0.2,
            }
        )
        bad_enum = json.dumps(
            {"sentiment": "angry", "issueCategory": "delivery", "summary": "x"}
        )
        grok = StubGrok({NOTE: extra, "Wrong shape.": bad_enum})
        rejects = []
        load(self.spec, rows, database, grok, rejects)

        self.assertEqual(database.get("orders", 9001), {"_id": 9001, "status": "already loaded"})
        self.assertIsNone(database.get("orders", 9004))
        self.assertEqual(database.count("orders"), 1)
        self.assertEqual(len(rows["orders"]), 2)
        self.assertEqual(len(rejects), 2)
        self.assertEqual(rejects[0]["sourceTable"], "orders")
        self.assertEqual(rejects[0]["primaryKey"], 9001)
        self.assertEqual(rejects[0]["targetCollection"], "orders")
        self.assertEqual(rejects[0]["targetPath"], "notesClassification")
        self.assertEqual(rejects[0]["reason"], "extra properties")
        self.assertEqual(rejects[0]["rawModelOutput"], extra)
        self.assertEqual(rejects[1]["reason"], "sentiment is not an allowed value")
        self.assertEqual(database.count("customers"), 1)

    def test_unknown_invoke_when_writes_nothing(self):
        spec = copy.deepcopy(self.spec)
        for item in spec["fieldMapping"]["mappings"]:
            if item["transform"] == "grok":
                item["grok"]["invokeWhen"] = "The source value is longer than 10 characters."
        spec["approval"]["specHash"] = spec_hash(spec)
        database = MemoryDatabase()
        grok = StubGrok({NOTE: CLASSIFIED})
        with self.assertRaises(UnrecognizedPredicate) as raised:
            load(spec, source_rows(), database, grok, [])
        self.assertIn("orders.notesClassification", str(raised.exception))
        self.assertEqual(grok.calls, [])
        self.assertEqual(database.docs, {})
        self.assertEqual(database.indexes, [])
        self.assertEqual(database.validators, {})

    def test_unapproved_or_mismatched_spec_writes_nothing(self):
        pending = copy.deepcopy(self.spec)
        pending["approval"] = {
            "status": "pending",
            "approver": None,
            "approvedAt": None,
            "changes": [],
            "specHash": None,
        }
        mismatched = copy.deepcopy(self.spec)
        mismatched["approval"]["specHash"] = "0" * 64
        database = MemoryDatabase()
        for spec in (pending, mismatched):
            with self.assertRaises(SpecRejected):
                load(spec, source_rows(), database, StubGrok({}), [])
        self.assertEqual(database.docs, {})
        self.assertEqual(database.indexes, [])
        self.assertEqual(database.validators, {})

    def test_recorded_approval_hash(self):
        self.assertEqual(self.spec["approval"]["status"], "approved")
        self.assertEqual(self.spec["approval"]["approver"], "Anshul Jhansiwale")
        self.assertEqual(self.spec["approval"]["approvedAt"], "2026-11-09T14:30:00Z")
        self.assertEqual(self.spec["approval"]["changes"], [])
        self.assertEqual(
            self.spec["approval"]["specHash"],
            "fdd95fd46110a6bb345a7344d46b670a5f2e820f45d6241d3c6308a664547369",
        )
        self.assertEqual(spec_hash(self.spec), self.spec["approval"]["specHash"])

    def test_create_index_uses_partial_filter_when_the_spec_has_one(self):
        spec = copy.deepcopy(self.spec)
        for item in spec["indexPlan"]["indexes"]:
            if item["name"] == "type_1_priceCents_1":
                item["partialFilter"] = {"type": "book"}
        spec["approval"]["specHash"] = spec_hash(spec)
        database = MemoryDatabase()
        load(spec, source_rows(), database, StubGrok({NOTE: CLASSIFIED}), [])
        created = [item for item in database.indexes if item["name"] == "type_1_priceCents_1"]
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["partialFilter"], {"type": "book"})
        self.assertEqual(list(created[0]["keys"].items()), [("type", 1), ("priceCents", 1)])
        self.assertEqual(len(database.indexes), 2)

    def test_long_fields_follow_the_validator(self):
        schema = self.spec["documentModel"]["collections"][3]["validation"]
        document = {
            "_id": 9001,
            "customer": {"customerId": 42, "fullName": "Sample Customer", "email": "customer@example.com"},
            "shipAddressId": 7,
            "placedAt": datetime.fromisoformat("2024-11-02T15:04:00"),
            "status": "shipped",
            "lines": [{"orderLineId": 1, "productId": 1001, "quantity": 2, "unitPriceCents": 2400}],
            "notesRaw": NOTE,
        }
        encoded = apply_bson_types(document, schema, long_type=lambda number: ("long", number))
        self.assertEqual(encoded["_id"], ("long", 9001))
        self.assertEqual(encoded["customer"]["customerId"], ("long", 42))
        self.assertEqual(encoded["shipAddressId"], ("long", 7))
        self.assertEqual(encoded["lines"][0]["quantity"], ("long", 2))
        self.assertEqual(encoded["notesRaw"], NOTE)
        self.assertEqual(encoded["placedAt"], document["placedAt"])
        self.assertIs(encoded["customer"]["fullName"], document["customer"]["fullName"])
        self.assertIsInstance(document["_id"], int)
        self.assertNotIsInstance(document["_id"], tuple)
        flagged = apply_bson_types({"isCurrent": True, "_id": 7}, {
            "bsonType": "object",
            "properties": {
                "_id": {"bsonType": "long"},
                "isCurrent": {"bsonType": "bool"},
            },
        }, long_type=lambda number: ("long", number))
        self.assertIs(flagged["isCurrent"], True)
        self.assertEqual(flagged["_id"], ("long", 7))

    def test_one_client_is_reused_for_the_process(self):
        from migration import store

        store._CLIENTS.clear()
        created = []

        def factory(uri):
            created.append(uri)
            return {"uri": uri}

        try:
            first = shared_client("mongodb://example", factory)
            second = shared_client("mongodb://example", factory)
            other = shared_client("mongodb://other", factory)
            self.assertIs(first, second)
            self.assertEqual(other, {"uri": "mongodb://other"})
            self.assertEqual(created, ["mongodb://example", "mongodb://other"])
        finally:
            store._CLIENTS.clear()

    def test_cli_checks_the_hash_before_a_load(self):
        self.assertEqual(main(["--spec", str(SPEC_PATH)]), 0)
        pending = copy.deepcopy(self.spec)
        pending["approval"]["status"] = "pending"
        pending["approval"]["specHash"] = None
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(pending, handle)
            pending_path = handle.name
        try:
            self.assertEqual(main(["--spec", pending_path]), 2)
        finally:
            os.unlink(pending_path)

    def test_cli_refuses_a_load_without_a_uri(self):
        previous = os.environ.pop("MONGODB_URI", None)
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
                json.dump(source_rows(), handle)
                source_path = handle.name
            try:
                captured = io.StringIO()
                with redirect_stderr(captured):
                    code = main(["--spec", str(SPEC_PATH), "--source", source_path, "--rejects", source_path + ".rejects"])
                self.assertEqual(code, 2)
                self.assertIn("Atlas load did not run", captured.getvalue())
            finally:
                os.unlink(source_path)
        finally:
            if previous is not None:
                os.environ["MONGODB_URI"] = previous

    def test_demo_script_stops_when_the_uri_is_missing(self):
        env = os.environ.copy()
        env.pop("MONGODB_URI", None)
        result = subprocess.run(
            ["sh", "scripts/load-demo.sh"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("Atlas load did not run", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_embed_keeps_a_copy_path_without_a_dot(self):
        spec = {
            "relationalModel": {
                "tables": [
                    {
                        "name": "order_lines",
                        "columns": [
                            {"name": "order_line_id", "dataType": "NUMBER(10)", "nullable": False},
                            {"name": "note", "dataType": "VARCHAR2(40)", "nullable": True},
                        ],
                    }
                ]
            }
        }
        owner = {"sourceTable": "orders", "sourceColumn": "order_id"}
        mappings = [
            {
                "sourceTable": "order_lines",
                "sourceColumn": "order_id",
                "targetPath": "_id",
                "transform": "parent",
            },
            {
                "sourceTable": "order_lines",
                "sourceColumn": "order_line_id",
                "targetPath": "lines.orderLineId",
                "transform": "copy",
            },
            {
                "sourceTable": "order_lines",
                "sourceColumn": "note",
                "targetPath": "metadata",
                "transform": "copy",
            },
        ]
        elements = _embed(
            spec,
            {"order_lines": [{"order_id": 9001, "order_line_id": 1, "note": "gift"}]},
            owner,
            {"order_id": 9001},
            mappings,
            "lines",
        )
        self.assertEqual(elements, [{"orderLineId": 1, "metadata": "gift"}])

    def test_lookup_matches_every_foreign_key_column(self):
        spec = {
            "relationalModel": {
                "tables": [
                    {
                        "name": "orders",
                        "foreignKeys": [
                            {
                                "columns": ["customer_id", "tenant_id"],
                                "referencesTable": "customers",
                                "referencesColumns": ["customer_id", "tenant_id"],
                            }
                        ],
                    }
                ]
            }
        }
        found = _lookup(
            spec,
            {
                "customers": [
                    {"customer_id": 42, "tenant_id": 2, "full_name": "Wrong"},
                    {"customer_id": 42, "tenant_id": 1, "full_name": "Right"},
                ]
            },
            {"sourceTable": "orders"},
            {"customer_id": 42, "tenant_id": 1},
            "customers",
        )
        self.assertEqual(found["full_name"], "Right")
        spec["relationalModel"]["tables"][0]["foreignKeys"][0]["referencesColumns"] = ["customer_id"]
        with self.assertRaises(ValueError):
            _lookup(spec, {"customers": []}, {"sourceTable": "orders"}, {}, "customers")

    def test_run_log_keeps_each_spec_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = log_run(
                self.spec,
                [ROOT / "tests" / "test_load.py"],
                [
                    {
                        "id": "tests.test_load.LoadTests.test_mappings_indexes_and_rerun",
                        "status": "passed",
                    }
                ],
                root=root,
                logged_at="2026-11-01T11-30-00Z",
                output="Ran 1 test\n\nOK\n",
            )
            digest = self.spec["approval"]["specHash"]
            self.assertEqual(folder.parent.name, digest)
            self.assertEqual((folder / "spec-hash.txt").read_text(encoding="utf-8").strip(), digest)
            stored = json.loads((folder / "approved-spec.json").read_text(encoding="utf-8"))
            self.assertEqual(spec_hash(stored), digest)
            self.assertIn("def test_mappings_indexes_and_rerun", (folder / "tests" / "test_load.py").read_text(encoding="utf-8"))
            results = json.loads((folder / "results.json").read_text(encoding="utf-8"))
            self.assertTrue(results["ok"])
            self.assertEqual(results["testsRun"], 1)
            self.assertIn("OK", (folder / "results.txt").read_text(encoding="utf-8"))

            changed = copy.deepcopy(self.spec)
            changed["documentModel"]["collections"][0]["purpose"] += " revised"
            changed["approval"]["specHash"] = spec_hash(changed)
            other = log_run(
                changed,
                [ROOT / "tests" / "test_load.py"],
                [{"id": "tests.test_load.LoadTests.test_mappings_indexes_and_rerun", "status": "failed", "detail": "boom"}],
                root=root,
                logged_at="2026-11-01T11-31-00Z",
            )
            self.assertNotEqual(other.parent.name, folder.parent.name)
            self.assertFalse(json.loads((other / "results.json").read_text(encoding="utf-8"))["ok"])

            rejected = copy.deepcopy(self.spec)
            rejected["approval"]["specHash"] = "0" * 64
            with self.assertRaises(SpecRejected):
                log_run(rejected, [], [], root=root, logged_at="2026-11-01T11-32-00Z")
            self.assertFalse((root / "runs" / ("0" * 64)).exists())


install(SPEC_PATH, [Path(__file__)], ROOT)


if __name__ == "__main__":
    unittest.main()

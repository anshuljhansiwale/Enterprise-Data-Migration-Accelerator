"""Stub-Grok coverage for the approved order-management load."""

import copy
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from migration.__main__ import main
from migration.hashing import spec_hash
from migration.load import SpecRejected, load
from migration.runlog import install, log_run
from migration.store import MemoryDatabase

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
            database.indexes[0]["keys"],
            {"type": 1, "priceCents": 1},
        )
        self.assertEqual(
            database.indexes[1]["keys"],
            {"customer.customerId": 1, "placedAt": -1},
        )
        self.assertEqual(
            set(database.validators),
            {"customers", "addresses", "products", "orders"},
        )
        self.assertIn("$jsonSchema", database.validators["products"])

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

        self.assertEqual(database.count("orders"), len(source_rows()["orders"]))
        again = load(self.spec, source_rows(), database, grok, rejects)
        self.assertEqual(again, 0)
        self.assertEqual(grok.calls, [NOTE])
        self.assertEqual(database.count("customers"), 1)
        self.assertEqual(database.count("addresses"), 2)
        self.assertEqual(database.count("products"), 3)
        self.assertEqual(database.count("orders"), 3)
        self.assertEqual(len(database.indexes), 2)

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

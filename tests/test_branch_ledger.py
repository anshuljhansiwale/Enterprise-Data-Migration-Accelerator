"""Stub-Grok coverage for the approved branch-ledger load."""

import copy
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from bson import Int64

from migration.__main__ import main
from migration.hashing import spec_hash
from migration.load import SpecRejected, load
from migration.runlog import install
from migration.store import MemoryDatabase

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "specs" / "branch-ledger" / "approved-spec.json"
SPEC_HASH = "eacf20fa2411769253927b01ac17383c954fead7237f22d90358060b03c6f8b9"
ACCOUNT = "GB00SAMP00000000000001"
MEMO_FEE = "Monthly service fee reversed after branch review."
MEMO_TRANSFER = "Moved funds to the treasury account."
CLASS_FEE = json.dumps(
    {
        "entryCategory": "fee",
        "summary": "Reversal of a monthly service fee after review.",
    }
)
CLASS_TRANSFER = json.dumps(
    {
        "entryCategory": "transfer",
        "summary": "Transfer to the treasury account.",
    }
)
OPENED = "2021-03-01T00:00:00"
POSTED = "2026-10-30T09:15:00"
INDEX_NAME = "account.institutionId_1_account.accountNumber_1_postedAt_-1"


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
    """Rows shaped from relationalModel. source.sql is not in the repo."""
    return {
        "institutions": [
            {"institution_id": 12, "name": "Sample Institution"},
            {"institution_id": 13, "name": "Second Institution"},
        ],
        "accounts": [
            {
                "institution_id": 12,
                "account_number": ACCOUNT,
                "title": "Sample Operating Account",
                "currency_code": "GBP",
                "opened_at": OPENED,
            },
            {
                "institution_id": 13,
                "account_number": ACCOUNT,
                "title": "Sample Treasury Account",
                "currency_code": "EUR",
                "opened_at": "2022-06-15T00:00:00",
            },
        ],
        "account_signers": [
            {
                "signer_id": 501,
                "institution_id": 12,
                "account_number": ACCOUNT,
                "full_name": "Sample Signer",
                "role_name": "primary",
                "is_active": 1,
            },
            {
                "signer_id": 502,
                "institution_id": 12,
                "account_number": ACCOUNT,
                "full_name": "Former Signer",
                "role_name": "secondary",
                "is_active": 0,
            },
        ],
        "journal_entries": [
            {
                "entry_id": 700001,
                "institution_id": 12,
                "account_number": ACCOUNT,
                "posted_at": POSTED,
                "memo": MEMO_FEE,
            },
            {
                "entry_id": 700002,
                "institution_id": 13,
                "account_number": ACCOUNT,
                "posted_at": "2026-10-29T09:15:00",
                "memo": None,
            },
            {
                "entry_id": 700003,
                "institution_id": 12,
                "account_number": ACCOUNT,
                "posted_at": "2026-10-28T09:15:00",
                "memo": "   ",
            },
            {
                "entry_id": 700004,
                "institution_id": 12,
                "account_number": ACCOUNT,
                "posted_at": "2026-10-27T09:15:00",
                "memo": MEMO_TRANSFER,
            },
            {
                "entry_id": 700005,
                "institution_id": 12,
                "account_number": ACCOUNT,
                "posted_at": "2026-10-26T09:15:00",
                "memo": None,
            },
        ],
        "journal_lines": [
            {"line_id": 1, "entry_id": 700001, "side": "debit", "amount_cents": 125000},
            {"line_id": 2, "entry_id": 700001, "side": "credit", "amount_cents": 125000},
            {"line_id": 3, "entry_id": 700002, "side": "debit", "amount_cents": 5000},
            {"line_id": 4, "entry_id": 700002, "side": "credit", "amount_cents": 5000},
            {"line_id": 5, "entry_id": 700003, "side": "debit", "amount_cents": 100},
            {"line_id": 6, "entry_id": 700004, "side": "debit", "amount_cents": 2500},
            {"line_id": 7, "entry_id": 700004, "side": "credit", "amount_cents": 2500},
        ],
    }


def count_tuples(rows):
    return [
        (
            row["sourceTable"],
            row["sourceRows"],
            row["documents"],
            row["rejected"],
            row["unloadedWithParent"],
        )
        for row in rows
    ]


class LoadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))

    def test_fixture_rows_cover_relational_columns(self):
        rows = source_rows()
        for table in self.spec["relationalModel"]["tables"]:
            self.assertIn(table["name"], rows)
            columns = {column["name"] for column in table["columns"]}
            self.assertGreater(len(rows[table["name"]]), 0)
            for row in rows[table["name"]]:
                self.assertEqual(set(row), columns)

    def test_mappings_index_and_rerun(self):
        database = MemoryDatabase()
        grok = StubGrok({MEMO_FEE: CLASS_FEE, MEMO_TRANSFER: CLASS_TRANSFER})
        rejects = []
        calls = load(self.spec, source_rows(), database, grok, rejects)

        self.assertEqual(calls, 2)
        self.assertEqual(rejects, [])
        self.assertEqual(grok.calls, [MEMO_FEE, MEMO_TRANSFER])
        self.assertEqual(
            set(database.docs),
            {"institutions", "accounts", "accountSigners", "journalEntries"},
        )
        self.assertEqual(
            database.indexes,
            [
                {
                    "collection": "journalEntries",
                    "keys": {
                        "account.institutionId": 1,
                        "account.accountNumber": 1,
                        "postedAt": -1,
                    },
                    "name": INDEX_NAME,
                    "partialFilter": None,
                }
            ],
        )
        self.assertEqual(
            list(database.indexes[0]["keys"]),
            ["account.institutionId", "account.accountNumber", "postedAt"],
        )
        for collection in self.spec["documentModel"]["collections"]:
            self.assertEqual(
                database.validators[collection["name"]],
                {"$jsonSchema": collection["validation"]},
            )

        institution = database.get("institutions", 12)
        self.assertIsInstance(institution["_id"], Int64)
        self.assertEqual(institution["name"], "Sample Institution")
        self.assertEqual(set(institution), {"_id", "name"})

        operating = database.get(
            "accounts",
            {"institutionId": 12, "accountNumber": ACCOUNT},
        )
        treasury = database.get(
            "accounts",
            {"institutionId": 13, "accountNumber": ACCOUNT},
        )
        self.assertEqual(operating["title"], "Sample Operating Account")
        self.assertEqual(operating["currencyCode"], "GBP")
        self.assertEqual(operating["openedAt"], datetime.fromisoformat(OPENED))
        self.assertIsInstance(operating["_id"]["institutionId"], Int64)
        self.assertEqual(set(operating), {"_id", "title", "currencyCode", "openedAt"})
        self.assertEqual(treasury["title"], "Sample Treasury Account")
        self.assertEqual(treasury["currencyCode"], "EUR")
        self.assertEqual(database.count("accounts"), 2)

        active = database.get("accountSigners", 501)
        former = database.get("accountSigners", 502)
        self.assertEqual(active["fullName"], "Sample Signer")
        self.assertEqual(active["roleName"], "primary")
        self.assertIs(active["isActive"], True)
        self.assertEqual(
            active["accountId"],
            {"institutionId": 12, "accountNumber": ACCOUNT},
        )
        self.assertEqual(set(active), {"_id", "accountId", "fullName", "roleName", "isActive"})
        self.assertIs(former["isActive"], False)
        self.assertEqual(former["fullName"], "Former Signer")

        fee = database.get("journalEntries", 700001)
        self.assertIsInstance(fee["_id"], Int64)
        self.assertEqual(fee["postedAt"], datetime.fromisoformat(POSTED))
        self.assertEqual(
            fee["account"],
            {
                "institutionId": 12,
                "accountNumber": ACCOUNT,
                "title": "Sample Operating Account",
            },
        )
        self.assertEqual(
            fee["lines"],
            [
                {"lineId": 1, "side": "debit", "amountCents": 125000},
                {"lineId": 2, "side": "credit", "amountCents": 125000},
            ],
        )
        self.assertEqual(set(fee["lines"][0]), {"lineId", "side", "amountCents"})
        self.assertIsInstance(fee["lines"][0]["lineId"], Int64)
        self.assertIsInstance(fee["lines"][0]["amountCents"], Int64)
        self.assertEqual(fee["memoRaw"], MEMO_FEE)
        self.assertEqual(fee["memoClassification"], json.loads(CLASS_FEE))
        self.assertEqual(
            set(fee),
            {"_id", "account", "postedAt", "lines", "memoRaw", "memoClassification"},
        )

        transfer_parent = database.get("journalEntries", 700002)
        self.assertEqual(transfer_parent["account"]["title"], "Sample Treasury Account")
        self.assertEqual(transfer_parent["account"]["institutionId"], 13)
        self.assertNotIn("memoRaw", transfer_parent)
        self.assertNotIn("memoClassification", transfer_parent)
        self.assertEqual(len(transfer_parent["lines"]), 2)

        blank = database.get("journalEntries", 700003)
        self.assertNotIn("memoRaw", blank)
        self.assertNotIn("memoClassification", blank)
        self.assertEqual(blank["lines"], [{"lineId": 5, "side": "debit", "amountCents": 100}])

        moved = database.get("journalEntries", 700004)
        self.assertEqual(moved["memoRaw"], MEMO_TRANSFER)
        self.assertEqual(moved["memoClassification"]["entryCategory"], "transfer")

        empty = database.get("journalEntries", 700005)
        self.assertEqual(empty["lines"], [])
        self.assertNotIn("memoRaw", empty)
        self.assertNotIn("memoClassification", empty)

        self.assertEqual(
            count_tuples(database.load_counts),
            [
                ("institutions", 2, 2, 0, 0),
                ("accounts", 2, 2, 0, 0),
                ("account_signers", 2, 2, 0, 0),
                ("journal_entries", 5, 5, 0, 0),
                ("journal_lines", 7, 7, 0, 0),
            ],
        )

        again = load(self.spec, source_rows(), database, grok, rejects)
        self.assertEqual(again, 0)
        self.assertEqual(grok.calls, [MEMO_FEE, MEMO_TRANSFER])
        self.assertEqual(database.count("institutions"), 2)
        self.assertEqual(database.count("accounts"), 2)
        self.assertEqual(database.count("accountSigners"), 2)
        self.assertEqual(database.count("journalEntries"), 5)
        self.assertEqual(len(database.get("journalEntries", 700001)["lines"]), 2)
        self.assertEqual(len(database.indexes), 1)
        self.assertEqual(database.load_counts[3]["documents"], 5)

    def test_grok_cache_uses_spec_hash_primary_key_and_text(self):
        rows = source_rows()
        rows["journal_entries"][3]["memo"] = MEMO_FEE
        database = MemoryDatabase()
        grok = StubGrok({MEMO_FEE: CLASS_FEE})
        rejects = []
        self.assertEqual(load(self.spec, rows, database, grok, rejects), 2)
        self.assertEqual(grok.calls, [MEMO_FEE, MEMO_FEE])
        self.assertEqual(load(self.spec, rows, database, grok, rejects), 0)
        self.assertEqual(grok.calls, [MEMO_FEE, MEMO_FEE])

        rows["journal_entries"][0]["memo"] = "A later memo with different wording."
        grok.responses[rows["journal_entries"][0]["memo"]] = CLASS_FEE
        self.assertEqual(load(self.spec, rows, database, grok, rejects), 1)
        self.assertEqual(database.count("journalEntries"), 5)

        changed = copy.deepcopy(self.spec)
        changed["documentModel"]["collections"][0]["purpose"] += " revised"
        changed["approval"]["specHash"] = spec_hash(changed)
        self.assertEqual(load(changed, rows, database, grok, rejects), 2)

    def test_rejected_grok_payload_is_unloaded(self):
        database = MemoryDatabase()
        marker = {"_id": 700004, "memoRaw": "keep"}
        database.upsert("journalEntries", marker)
        bad = json.dumps({"entryCategory": "fee", "summary": "x" * 281})
        grok = StubGrok({MEMO_FEE: CLASS_FEE, MEMO_TRANSFER: bad})
        rejects = []
        calls = load(self.spec, source_rows(), database, grok, rejects)

        self.assertEqual(calls, 2)
        self.assertEqual(database.get("journalEntries", 700004), marker)
        self.assertEqual(database.count("journalEntries"), 5)
        self.assertEqual(database.get("journalEntries", 700001)["memoClassification"]["entryCategory"], "fee")
        self.assertNotIn("memoRaw", database.get("journalEntries", 700002))
        line_ids = [
            line["lineId"]
            for document in database.docs["journalEntries"].values()
            for line in document.get("lines", [])
        ]
        self.assertNotIn(6, line_ids)
        self.assertNotIn(7, line_ids)
        self.assertIn(1, line_ids)
        self.assertEqual(len(rejects), 1)
        self.assertEqual(
            rejects[0],
            {
                "sourceTable": "journal_entries",
                "primaryKey": 700004,
                "targetCollection": "journalEntries",
                "targetPath": "memoClassification",
                "reason": "summary is too long",
                "rawModelOutput": bad,
            },
        )
        self.assertEqual(
            count_tuples(database.load_counts),
            [
                ("institutions", 2, 2, 0, 0),
                ("accounts", 2, 2, 0, 0),
                ("account_signers", 2, 2, 0, 0),
                ("journal_entries", 5, 4, 1, 0),
                ("journal_lines", 7, 5, 0, 2),
            ],
        )
        for row in database.load_counts:
            self.assertEqual(
                row["documents"] + row["rejected"] + row["unloadedWithParent"],
                row["sourceRows"],
            )

        again = load(self.spec, source_rows(), database, grok, rejects)
        self.assertEqual(again, 1)
        self.assertEqual(grok.calls, [MEMO_FEE, MEMO_TRANSFER, MEMO_TRANSFER])
        self.assertEqual(len(rejects), 2)
        self.assertEqual(database.get("journalEntries", 700004), marker)
        self.assertEqual(database.count("journalEntries"), 5)
        self.assertEqual(database.count("accounts"), 2)

    def test_unapproved_or_mismatched_spec_writes_nothing(self):
        pending = copy.deepcopy(self.spec)
        pending["approval"] = {
            "status": "pending",
            "approver": None,
            "approvedAt": None,
            "changes": [],
            "specHash": None,
        }
        missing_hash = copy.deepcopy(self.spec)
        missing_hash["approval"]["specHash"] = None
        mismatched = copy.deepcopy(self.spec)
        mismatched["documentModel"]["collections"][0]["purpose"] += " revised"
        database = MemoryDatabase()
        grok = StubGrok({MEMO_FEE: CLASS_FEE, MEMO_TRANSFER: CLASS_TRANSFER})
        for spec in (pending, missing_hash, mismatched):
            with self.assertRaises(SpecRejected):
                load(spec, source_rows(), database, grok, [])
        self.assertEqual(grok.calls, [])
        self.assertEqual(database.docs, {})
        self.assertEqual(database.indexes, [])
        self.assertEqual(database.validators, {})
        self.assertFalse(hasattr(database, "load_counts"))

    def test_approved_hash_and_cli_refusal(self):
        self.assertEqual(self.spec["approval"]["status"], "approved")
        self.assertEqual(self.spec["approval"]["approver"], "Anshul Jhansiwale")
        self.assertEqual(self.spec["approval"]["changes"], [])
        self.assertEqual(self.spec["approval"]["specHash"], SPEC_HASH)
        self.assertEqual(spec_hash(self.spec), SPEC_HASH)
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


install(SPEC_PATH, [Path(__file__)], ROOT)


if __name__ == "__main__":
    unittest.main()

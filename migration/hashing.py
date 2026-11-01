"""Canonical Migration Spec hash. Approval is excluded."""

import hashlib
import json


def canonical_body(spec):
    return {
        "specVersion": spec["specVersion"],
        "relationalModel": spec["relationalModel"],
        "documentModel": spec["documentModel"],
        "indexPlan": spec["indexPlan"],
        "fieldMapping": spec["fieldMapping"],
    }


def spec_hash(spec):
    payload = json.dumps(
        canonical_body(spec),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()

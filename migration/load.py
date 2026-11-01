"""Batch load of an approved Migration Spec. The spec decides every write."""

import hashlib
import json
from datetime import datetime

from migration.hashing import spec_hash


class SpecRejected(Exception):
    """The spec is not approved, or its hash does not match."""


class UnrecognizedPredicate(Exception):
    """A grok mapping names an invokeWhen predicate this load cannot evaluate."""


_NONBLANK_TEXT = "The source value is a string and contains a non-whitespace character."


def _nonblank_text(value):
    return isinstance(value, str) and any(not char.isspace() for char in value)


_PREDICATES = {_NONBLANK_TEXT: _nonblank_text}


def prepare(spec):
    approval = spec.get("approval") or {}
    if approval.get("status") != "approved" or not approval.get("specHash"):
        raise SpecRejected("spec is not approved")
    actual = spec_hash(spec)
    if actual != approval["specHash"]:
        raise SpecRejected("spec hash does not match")
    return actual


def load(spec, source, database, grok, rejects):
    """Load source tables. Returns the number of Grok calls made in this run."""
    digest = prepare(spec)
    _require_known_predicates(spec)
    cache = getattr(database, "grok_cache", None)
    if cache is None:
        cache = {}
        database.grok_cache = cache
    for collection in spec["documentModel"]["collections"]:
        database.set_validator(collection["name"], collection["validation"])
    for index in spec["indexPlan"]["indexes"]:
        if index.get("create"):
            database.create_index(
                index["collection"],
                index["keys"],
                index["name"],
                index.get("partialFilter"),
            )

    calls = 0
    for collection in spec["documentModel"]["collections"]:
        for document, call_count in _documents(spec, source, collection, grok, rejects, cache, digest):
            calls += call_count
            if document is not None:
                database.upsert(collection["name"], document)
    return calls


def _documents(spec, source, collection, grok, rejects, cache, digest):
    name = collection["name"]
    mappings = [
        item for item in spec["fieldMapping"]["mappings"] if item["targetCollection"] == name
    ]
    owner = next(
        item for item in mappings if item["targetPath"] == "_id" and item["transform"] == "copy"
    )
    validation = collection["validation"]
    discriminator = (collection.get("discriminator") or {}).get("field")
    for row in source.get(owner["sourceTable"], []):
        document = {}
        for item in mappings:
            if item["sourceTable"] != owner["sourceTable"]:
                continue
            if item["transform"] == "parent":
                continue
            if item["transform"] == "grok":
                continue
            if item["targetPath"].startswith(_array_prefix(mappings) + "."):
                continue
            value = _value(spec, item, row)
            if _blank(value) and _classified_column(mappings, item):
                continue
            _set_path(document, item["targetPath"], value)

        for item in mappings:
            if item["transform"] != "snapshot" or item["sourceTable"] == owner["sourceTable"]:
                continue
            joined = _lookup(spec, source, owner, row, item["sourceTable"])
            if joined is not None:
                _set_path(document, item["targetPath"], _value(spec, item, joined))

        prefix = _array_prefix(mappings)
        if prefix:
            document[prefix] = _embed(spec, source, owner, row, mappings, prefix)

        calls = 0
        failed = False
        for item in mappings:
            if item["transform"] != "grok":
                continue
            raw_text = row.get(item["sourceColumn"])
            if not _invoke(item, raw_text):
                continue
            raw, used_call, cache_key = _classify(item, raw_text, grok, cache, digest, row, owner)
            calls += used_call
            ok, reason = _valid(raw, item["grok"]["targetShape"])
            if not ok:
                rejects.append(
                    {
                        "sourceTable": owner["sourceTable"],
                        "primaryKey": row.get(owner["sourceColumn"]),
                        "targetCollection": name,
                        "targetPath": item["targetPath"],
                        "reason": reason,
                        "rawModelOutput": raw,
                    }
                )
                failed = True
                break
            cache[cache_key] = raw
            _set_path(document, item["targetPath"], json.loads(raw))

        if failed:
            yield None, calls
            continue
        _keep_owned_subtype_fields(document, validation, discriminator)
        yield document, calls


def _embed(spec, source, owner, row, mappings, prefix):
    parent = next(item for item in mappings if item["transform"] == "parent")
    elements = []
    for child in source.get(parent["sourceTable"], []):
        if child.get(parent["sourceColumn"]) != row.get(owner["sourceColumn"]):
            continue
        element = {}
        for item in mappings:
            if item["sourceTable"] != parent["sourceTable"] or item["transform"] != "copy":
                continue
            _set_path(element, _element_field(item["targetPath"], prefix), _value(spec, item, child))
        elements.append(element)
    return elements


def _element_field(target_path, prefix):
    head = prefix + "."
    if target_path.startswith(head) and len(target_path) > len(head):
        return target_path[len(head):]
    return target_path


def _array_prefix(mappings):
    parent = next((item for item in mappings if item["transform"] == "parent"), None)
    if parent is None:
        return ""
    for item in mappings:
        if item["sourceTable"] == parent["sourceTable"] and item["transform"] == "copy" and "." in item["targetPath"]:
            return item["targetPath"].split(".", 1)[0]
    return ""


def _lookup(spec, source, owner, row, other_table):
    owner_table = _table(spec, owner["sourceTable"])
    for foreign_key in owner_table.get("foreignKeys", []):
        if foreign_key["referencesTable"] != other_table:
            continue
        columns = foreign_key.get("columns") or []
        referenced = foreign_key.get("referencesColumns") or []
        if not columns or len(columns) != len(referenced):
            raise ValueError(
                f"{owner['sourceTable']} foreign key to {other_table} needs columns and referencesColumns of equal length"
            )
        for candidate in source.get(other_table, []):
            if all(candidate.get(ref) == row.get(col) for col, ref in zip(columns, referenced)):
                return candidate
    return None


def _value(spec, mapping, row):
    value = row.get(mapping["sourceColumn"])
    if value is None:
        return None
    if mapping.get("valueConversion") == "zero-one-to-boolean":
        return bool(int(value))
    column = _column(spec, mapping["sourceTable"], mapping["sourceColumn"])
    data_type = column["dataType"]
    if data_type.startswith("NUMBER"):
        return int(value)
    if data_type == "TIMESTAMP":
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(value)
    return value


def _blank(value):
    # A classified column stores nothing when the source text is blank.
    return isinstance(value, str) and not any(not char.isspace() for char in value)


def _classified_column(mappings, item):
    return any(
        other["transform"] == "grok"
        and other["sourceTable"] == item["sourceTable"]
        and other["sourceColumn"] == item["sourceColumn"]
        for other in mappings
    )


def _require_known_predicates(spec):
    for item in spec["fieldMapping"]["mappings"]:
        if item.get("transform") != "grok":
            continue
        predicate = item.get("grok", {}).get("invokeWhen")
        if predicate not in _PREDICATES:
            raise UnrecognizedPredicate(_predicate_message(item, predicate))


def _predicate_message(mapping, predicate):
    return (
        f"unrecognized invokeWhen for {mapping['targetCollection']}.{mapping['targetPath']}: "
        f"{predicate}"
    )


def _invoke(mapping, value):
    predicate = mapping["grok"]["invokeWhen"]
    try:
        check = _PREDICATES[predicate]
    except KeyError as error:
        raise UnrecognizedPredicate(_predicate_message(mapping, predicate)) from error
    return check(value)


def _classify(mapping, text, grok, cache, digest, row, owner):
    key = (
        digest,
        row.get(owner["sourceColumn"]),
        mapping["targetPath"],
        hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
    if key in cache:
        return cache[key], 0, key
    raw = grok.classify(text, mapping["grok"]["targetShape"])
    return raw, 1, key


def _valid(raw, shape):
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return False, "response is not JSON"
    if not isinstance(payload, dict):
        return False, "response is not an object"
    allowed = set(shape.get("properties", {}))
    if shape.get("additionalProperties") is False and set(payload) - allowed:
        return False, "extra properties"
    for name in shape.get("required", []):
        if name not in payload:
            return False, "missing " + name
    for name, rules in shape.get("properties", {}).items():
        if name not in payload:
            continue
        value = payload[name]
        if rules.get("type") == "string" and not isinstance(value, str):
            return False, name + " is not a string"
        if "enum" in rules and value not in rules["enum"]:
            return False, name + " is not an allowed value"
        if isinstance(value, str) and "maxLength" in rules and len(value) > rules["maxLength"]:
            return False, name + " is too long"
    return True, ""


def _keep_owned_subtype_fields(document, validation, discriminator):
    branches = validation.get("oneOf")
    if not branches or not discriminator:
        return
    specific = set()
    owned = set()
    for branch in branches:
        required = set(branch.get("required", []))
        specific.update(required)
        enum = branch.get("properties", {}).get(discriminator, {}).get("enum", [])
        if document.get(discriminator) in enum:
            owned = required
    for field in specific - owned:
        document.pop(field, None)


def _set_path(document, path, value):
    if value is None:
        return
    cursor = document
    parts = path.split(".")
    for part in parts[:-1]:
        cursor = cursor.setdefault(part, {})
    cursor[parts[-1]] = value


def _table(spec, name):
    return next(item for item in spec["relationalModel"]["tables"] if item["name"] == name)


def _column(spec, table_name, column_name):
    table = _table(spec, table_name)
    return next(item for item in table["columns"] if item["name"] == column_name)

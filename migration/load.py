"""Batch load of an approved Migration Spec. The spec decides every write."""

import hashlib
import json
from datetime import datetime
from decimal import Decimal

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


def reconcile(spec, source, database, rejects):
    """Compare each source table with the documents stored for it.

    A rejected source row is not a loaded document. Rows that embed into a
    parent are counted on that parent, and they are absent when the parent
    row was rejected. This reads the target; it does not create a collection,
    an index, or a Grok call.
    """
    rejected_ids = {}
    for item in rejects:
        rejected_ids.setdefault(item["sourceTable"], set()).add(item["primaryKey"])

    mappings_by_collection = {}
    for collection in spec["documentModel"]["collections"]:
        mappings_by_collection[collection["name"]] = [
            item
            for item in spec["fieldMapping"]["mappings"]
            if item["targetCollection"] == collection["name"]
        ]
    owners = {
        name: _owner(mappings)["sourceTable"]
        for name, mappings in mappings_by_collection.items()
    }
    embedded = {}
    for collection_name, mappings in mappings_by_collection.items():
        parent = next((item for item in mappings if item["transform"] == "parent"), None)
        if parent is None:
            continue
        embedded[parent["sourceTable"]] = {
            "collection": collection_name,
            "prefix": _array_prefix(mappings),
            "ownerTable": owners[collection_name],
            "parentColumn": parent["sourceColumn"],
            "mappings": mappings,
        }

    report = []
    for table in spec["relationalModel"]["tables"]:
        name = table["name"]
        rows = source.get(name, [])
        rejected = {
            _norm_id(key) for key in rejected_ids.get(name, set())
        }
        rejected_rows = sum(1 for row in rows if _row_key(table, row) in rejected)
        collection = next((cname for cname, owner in owners.items() if owner == name), None)
        if collection is not None:
            written = _count_loaded(database, collection, table, rows, rejected)
            storage = "collection"
        elif name in embedded:
            info = embedded[name]
            parent_rejected = {
                _norm_id(key) for key in rejected_ids.get(info["ownerTable"], set())
            }
            written = _count_embedded(database, info, table, rows, rejected, parent_rejected)
            storage = "embedded"
        else:
            written = 0
            storage = "unmapped"
        report.append(
            {
                "sourceTable": name,
                "sourceRows": len(rows),
                "documentsWritten": written,
                "rejected": rejected_rows,
                "storage": storage,
            }
        )
    return report


def _row_key(table, row):
    columns = table.get("primaryKey") or []
    if len(columns) != 1:
        return None
    return _norm_id(row.get(columns[0]))


def _norm_id(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return value
    return int(value)


def _count_loaded(database, collection, table, rows, rejected):
    """Documents of this source table present in the collection.

    Rejected primary keys are left out even when an older document still
    occupies that _id, because this load did not write them.
    """
    present = {_norm_id(document.get("_id")) for document in database.each(collection)}
    written = 0
    for row in rows:
        key = _row_key(table, row)
        if key in rejected or key not in present:
            continue
        written += 1
    return written


def _count_embedded(database, info, table, rows, rejected, parent_rejected):
    id_field = _embedded_id_field(info, table)
    stored = {}
    for document in database.each(info["collection"]):
        parent_id = _norm_id(document.get("_id"))
        if parent_id in parent_rejected:
            continue
        elements = document.get(info["prefix"]) or []
        stored[parent_id] = {_norm_id(element.get(id_field)) for element in elements}
    written = 0
    for row in rows:
        key = _row_key(table, row)
        if key in rejected:
            continue
        parent_id = _norm_id(row.get(info["parentColumn"]))
        if parent_id in parent_rejected:
            continue
        if key in stored.get(parent_id, set()):
            written += 1
    return written


def _embedded_id_field(info, table):
    columns = table.get("primaryKey") or []
    if len(columns) != 1:
        return None
    prefix = info["prefix"] + "."
    for item in info["mappings"]:
        if item["transform"] != "copy" or item["sourceColumn"] != columns[0]:
            continue
        if item["targetPath"].startswith(prefix):
            return item["targetPath"][len(prefix):]
    return None


def _documents(spec, source, collection, grok, rejects, cache, digest):
    name = collection["name"]
    mappings = [
        item for item in spec["fieldMapping"]["mappings"] if item["targetCollection"] == name
    ]
    owner = _owner(mappings)
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


def _owner(mappings):
    exact = next(
        (item for item in mappings if item["targetPath"] == "_id" and item["transform"] == "copy"),
        None,
    )
    if exact is not None:
        return exact
    parts = [
        item
        for item in mappings
        if item["transform"] == "copy" and item["targetPath"].startswith("_id.")
    ]
    if not parts:
        raise ValueError("collection has no _id mapping")
    return parts[0]


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
    foreign_keys = _table(spec, owner["sourceTable"]).get("foreignKeys", [])
    direct = [item for item in foreign_keys if item["referencesTable"] == other_table]
    if direct:
        for foreign_key in direct:
            found = _match_foreign_key(source, other_table, foreign_key, row, owner["sourceTable"])
            if found is not None:
                return found
        return None
    for foreign_key in foreign_keys:
        middle_name = foreign_key["referencesTable"]
        middle = _match_foreign_key(source, middle_name, foreign_key, row, owner["sourceTable"])
        if middle is None:
            continue
        for hop in _table(spec, middle_name).get("foreignKeys", []):
            if hop["referencesTable"] != other_table:
                continue
            found = _match_foreign_key(source, other_table, hop, middle, middle_name)
            if found is not None:
                return found
    return None


def _match_foreign_key(source, other_table, foreign_key, row, owner_table):
    columns = foreign_key.get("columns") or []
    referenced = foreign_key.get("referencesColumns") or []
    if not columns or len(columns) != len(referenced):
        raise ValueError(
            f"{owner_table} foreign key to {other_table} needs columns and referencesColumns of equal length"
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
        if "," in data_type:
            return Decimal(str(value))
        return int(value)
    if data_type in ("TIMESTAMP", "DATE"):
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

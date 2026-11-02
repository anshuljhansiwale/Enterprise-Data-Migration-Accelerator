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
    database.load_counts = compare_source_counts(spec, source, database, rejects)
    return calls


def _collection_mappings(spec, collection_name):
    return [
        item
        for item in spec["fieldMapping"]["mappings"]
        if item["targetCollection"] == collection_name
    ]


def _id_copy_mappings(mappings):
    return [
        item
        for item in mappings
        if item["transform"] == "copy"
        and (item["targetPath"] == "_id" or item["targetPath"].startswith("_id."))
    ]


def _owner_mapping(mappings):
    copies = _id_copy_mappings(mappings)
    if not copies:
        raise ValueError("collection has no _id mapping")
    tables = {item["sourceTable"] for item in copies}
    if len(tables) != 1:
        raise ValueError("document _id must come from one source table")
    exact = [item for item in copies if item["targetPath"] == "_id"]
    if len(exact) > 1:
        raise ValueError("collection has more than one scalar _id mapping")
    return exact[0] if exact else copies[0]


def _primary_key(spec, table_name, row):
    columns = _table(spec, table_name)["primaryKey"]
    if len(columns) == 1:
        return row.get(columns[0])
    return {column: row.get(column) for column in columns}


def _hashable(value):
    if isinstance(value, dict):
        return tuple(sorted((key, _hashable(item)) for key, item in value.items()))
    if isinstance(value, list):
        return tuple(_hashable(item) for item in value)
    return value


def _document_id(spec, mappings, row):
    document = {}
    for item in _id_copy_mappings(mappings):
        _set_path(document, item["targetPath"], _value(spec, item, row))
    return document.get("_id")


def _documents(spec, source, collection, grok, rejects, cache, digest):
    name = collection["name"]
    mappings = _collection_mappings(spec, name)
    owner = _owner_mapping(mappings)
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
        primary_key = _primary_key(spec, owner["sourceTable"], row)
        for item in mappings:
            if item["transform"] != "grok":
                continue
            raw_text = row.get(item["sourceColumn"])
            if not _invoke(item, raw_text):
                continue
            raw, used_call, cache_key = _classify(item, raw_text, grok, cache, digest, primary_key)
            calls += used_call
            ok, reason = _valid(raw, item["grok"]["targetShape"])
            if not ok:
                rejects.append(
                    {
                        "sourceTable": owner["sourceTable"],
                        "primaryKey": primary_key,
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
        from bson import Int64

        return Int64(int(value))
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


def _classify(mapping, text, grok, cache, digest, primary_key):
    key = (
        digest,
        _hashable(primary_key),
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


def compare_source_counts(spec, source, database, rejects):
    """Compare each source table with the documents written from it.

    A rejected source row is not a loaded document. Embedded child rows whose
    parent was rejected are counted in unloadedWithParent and are not loaded.
    """
    rejected = {}
    for item in rejects:
        rejected.setdefault(item["sourceTable"], set()).add(_hashable(item["primaryKey"]))

    by_collection = {}
    for collection in spec["documentModel"]["collections"]:
        mappings = _collection_mappings(spec, collection["name"])
        by_collection[collection["name"]] = (_owner_mapping(mappings), mappings)

    reports = []
    for table in spec["relationalModel"]["tables"]:
        name = table["name"]
        rows = source.get(name, [])
        owner_hit = None
        for collection_name, (owner, mappings) in by_collection.items():
            if owner["sourceTable"] == name:
                owner_hit = (collection_name, mappings)
                break
        if owner_hit is not None:
            collection_name, mappings = owner_hit
            reports.append(
                _owner_count(spec, name, rows, collection_name, mappings, database, rejected)
            )
            continue
        parent_mapping = next(
            (
                item
                for item in spec["fieldMapping"]["mappings"]
                if item["sourceTable"] == name and item["transform"] == "parent"
            ),
            None,
        )
        if parent_mapping is None:
            reports.append(_count_row(name, None, len(rows), 0, 0, 0))
            continue
        collection_name = parent_mapping["targetCollection"]
        owner, mappings = by_collection[collection_name]
        reports.append(
            _embedded_count(
                spec, name, rows, parent_mapping, owner, mappings, database, rejected
            )
        )
    return reports


def _count_row(source_table, target_collection, source_rows, documents, rejected, unloaded):
    return {
        "sourceTable": source_table,
        "targetCollection": target_collection,
        "sourceRows": source_rows,
        "documents": documents,
        "rejected": rejected,
        "unloadedWithParent": unloaded,
    }


def _owner_count(spec, name, rows, collection_name, mappings, database, rejected):
    rejected_keys = rejected.get(name, set())
    written = 0
    rejected_count = 0
    for row in rows:
        if _hashable(_primary_key(spec, name, row)) in rejected_keys:
            rejected_count += 1
            continue
        if database.get(collection_name, _document_id(spec, mappings, row)) is not None:
            written += 1
    return _count_row(name, collection_name, len(rows), written, rejected_count, 0)


def _embedded_count(spec, name, rows, parent_mapping, owner, mappings, database, rejected):
    collection_name = parent_mapping["targetCollection"]
    prefix = _array_prefix(mappings)
    rejected_parents = rejected.get(owner["sourceTable"], set())
    rejected_keys = rejected.get(name, set())
    written = 0
    rejected_count = 0
    unloaded = 0
    for row in rows:
        if _hashable(_primary_key(spec, name, row)) in rejected_keys:
            rejected_count += 1
            continue
        if _hashable(_parent_key(spec, owner, parent_mapping, row)) in rejected_parents:
            unloaded += 1
            continue
        document = database.get(
            collection_name,
            _parent_document_id(spec, owner, mappings, parent_mapping, row),
        )
        element = _child_element(spec, mappings, parent_mapping, row, prefix)
        elements = document.get(prefix, []) if document else []
        if element in elements:
            written += 1
    return _count_row(name, collection_name, len(rows), written, rejected_count, unloaded)


def _parent_key(spec, owner, parent_mapping, child_row):
    columns = _table(spec, owner["sourceTable"])["primaryKey"]
    if len(columns) == 1:
        return child_row.get(parent_mapping["sourceColumn"])
    return {column: child_row.get(column) for column in columns}


def _parent_document_id(spec, owner, mappings, parent_mapping, child_row):
    columns = _table(spec, owner["sourceTable"])["primaryKey"]
    if len(columns) == 1:
        synthetic = {columns[0]: child_row.get(parent_mapping["sourceColumn"])}
    else:
        synthetic = {column: child_row.get(column) for column in columns}
    return _document_id(spec, mappings, synthetic)


def _child_element(spec, mappings, parent_mapping, child, prefix):
    element = {}
    for item in mappings:
        if item["sourceTable"] != parent_mapping["sourceTable"] or item["transform"] != "copy":
            continue
        _set_path(element, _element_field(item["targetPath"], prefix), _value(spec, item, child))
    return element


def _table(spec, name):
    return next(item for item in spec["relationalModel"]["tables"] if item["name"] == name)


def _column(spec, table_name, column_name):
    table = _table(spec, table_name)
    return next(item for item in table["columns"] if item["name"] == column_name)

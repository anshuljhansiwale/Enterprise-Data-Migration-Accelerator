"""Check an approved spec, or load source rows described by it."""

import argparse
import json
import os
import sys
from datetime import datetime

from migration.grok_api import client_for
from migration.load import (
    SpecRejected,
    UnrecognizedPredicate,
    compare_source_counts,
    format_count_report,
    load,
    prepare,
)
from migration.store import MongoDatabase


def main(argv=None):
    parser = argparse.ArgumentParser(description="Load an approved Migration Spec")
    parser.add_argument("--spec", required=True)
    parser.add_argument("--source")
    parser.add_argument("--uri")
    parser.add_argument("--database", default=None)
    parser.add_argument("--rejects", default="rejects.jsonl")
    args = parser.parse_args(argv)

    with open(args.spec, encoding="utf-8") as handle:
        spec = json.load(handle)

    if args.source is None:
        try:
            prepare(spec)
        except SpecRejected as error:
            print(str(error), file=sys.stderr)
            return 2
        print(spec["approval"]["specHash"])
        return 0

    with open(args.source, encoding="utf-8") as handle:
        source = json.load(handle)

    try:
        prepare(spec)
    except SpecRejected as error:
        print(str(error), file=sys.stderr)
        return 2

    uri = args.uri or os.environ.get("MONGODB_URI", "")
    if not uri:
        print("MONGODB_URI is missing. The Atlas load did not run.", file=sys.stderr)
        return 2

    database_name = args.database or timestamped_database(datetime.now())
    print("database", database_name)
    rejects = []
    try:
        grok = client_for(spec)
        database = MongoDatabase(uri, database_name)
        calls = load(spec, source, database, grok, rejects)
    except (SpecRejected, UnrecognizedPredicate) as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception as error:
        print(_redact(str(error), uri), file=sys.stderr)
        return 2

    with open(args.rejects, "w", encoding="utf-8") as handle:
        for line in rejects:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    report = compare_source_counts(spec, source, database, rejects)
    print(format_count_report(report))
    print("grok calls", calls)
    for collection in spec["documentModel"]["collections"]:
        name = collection["name"]
        print(f"{name} {database.count(name)}")
    created = [item["name"] for item in spec["indexPlan"]["indexes"] if item.get("create")]
    print("indexes", " ".join(created))
    print("rejects", len(rejects))
    if not all(item["match"] for item in report):
        return 1
    return 0


def timestamped_database(moment):
    """Name one database per load so earlier runs stay visible."""
    return "order_management_" + moment.strftime("%Y-%m-%d_%H%M%S")


def _redact(message, uri):
    redacted = message
    if uri:
        redacted = redacted.replace(uri, "MONGODB_URI")
    for name in ("MONGODB_URI", "XAI_API_KEY"):
        secret = os.environ.get(name, "")
        if secret and secret != uri:
            redacted = redacted.replace(secret, name)
    return redacted


if __name__ == "__main__":
    sys.exit(main())

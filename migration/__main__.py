"""Check an approved spec, or load source rows described by it."""

import argparse
import json
import sys

from migration.load import (
    SpecRejected,
    UnrecognizedPredicate,
    compare_source_counts,
    format_count_report,
    load,
)
from migration.store import MemoryDatabase, MongoDatabase


def main(argv=None):
    parser = argparse.ArgumentParser(description="Load an approved Migration Spec")
    parser.add_argument("--spec", required=True)
    parser.add_argument("--source")
    parser.add_argument("--uri")
    parser.add_argument("--database", default="migration")
    parser.add_argument("--rejects", default="rejects.jsonl")
    args = parser.parse_args(argv)

    with open(args.spec, encoding="utf-8") as handle:
        spec = json.load(handle)

    if args.source is None:
        try:
            from migration.load import prepare

            prepare(spec)
        except SpecRejected as error:
            print(str(error), file=sys.stderr)
            return 2
        print(spec["approval"]["specHash"])
        return 0

    with open(args.source, encoding="utf-8") as handle:
        source = json.load(handle)
    database = MongoDatabase(args.uri, args.database) if args.uri else MemoryDatabase()
    rejects = []
    try:
        load(spec, source, database, _NotImplementedGrok(), rejects)
    except (SpecRejected, UnrecognizedPredicate) as error:
        print(str(error), file=sys.stderr)
        return 2
    with open(args.rejects, "w", encoding="utf-8") as handle:
        for line in rejects:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    report = compare_source_counts(spec, source, database, rejects)
    print(format_count_report(report))
    if rejects:
        print(f"rejects {len(rejects)} {args.rejects}")
    if not all(item["match"] for item in report):
        return 1
    return 0


class _NotImplementedGrok:
    def classify(self, text, target_shape):
        raise RuntimeError("a live Grok call is outside this slice; tests pass a stub client")


if __name__ == "__main__":
    sys.exit(main())

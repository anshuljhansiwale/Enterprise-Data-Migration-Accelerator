"""Check an approved spec, or load source rows described by it."""

import argparse
import json
import sys

from migration.grok_api import client_for
from migration.load import SpecRejected, UnrecognizedPredicate, load
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
        load(spec, source, database, client_for(spec), rejects)
    except (SpecRejected, UnrecognizedPredicate) as error:
        print(str(error), file=sys.stderr)
        return 2
    with open(args.rejects, "w", encoding="utf-8") as handle:
        for line in rejects:
            handle.write(json.dumps(line) + "\n")
    for name in database.docs if isinstance(database, MemoryDatabase) else []:
        print(f"{name} {database.count(name)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

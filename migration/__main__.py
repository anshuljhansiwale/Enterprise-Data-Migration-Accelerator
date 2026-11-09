"""Check an approved spec, or load source rows described by it."""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from migration.grok_api import client_for
from migration.load import SpecRejected, UnrecognizedPredicate, load
from migration.store import MemoryDatabase, MongoDatabase

_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


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
            from migration.load import prepare

            prepare(spec)
        except SpecRejected as error:
            print(str(error), file=sys.stderr)
            return 2
        print(spec["approval"]["specHash"])
        return 0

    with open(args.source, encoding="utf-8") as handle:
        source = json.load(handle)
    apply_local_env(_ENV_FILE, os.environ)
    uri = mongodb_uri(args.uri, os.environ, _ENV_FILE)
    database_name = args.database or timestamped_database(datetime.now())
    print("database", database_name)
    database = MongoDatabase(uri, database_name) if uri else MemoryDatabase()
    rejects = []
    try:
        calls = load(spec, source, database, client_for(spec), rejects)
    except (SpecRejected, UnrecognizedPredicate) as error:
        print(str(error), file=sys.stderr)
        return 2
    with open(args.rejects, "w", encoding="utf-8") as handle:
        for line in rejects:
            handle.write(json.dumps(line) + "\n")
    print("grok calls", calls)
    for collection in spec["documentModel"]["collections"]:
        name = collection["name"]
        print(f"{name} {database.count(name)}")
    created = [item["name"] for item in spec["indexPlan"]["indexes"] if item.get("create")]
    print("indexes", " ".join(created))
    print("rejects", len(rejects))
    return 0


def timestamped_database(moment):
    """Name one database per load so earlier runs stay visible."""
    return "order_management_" + moment.strftime("%Y-%m-%d_%H%M%S")


def apply_local_env(env_file, environ):
    """Fill MONGODB_URI and XAI_API_KEY from .env when the process does not already have them."""
    for name in ("MONGODB_URI", "XAI_API_KEY"):
        if environ.get(name):
            continue
        value = _dotenv_value(env_file, name)
        if value:
            environ[name] = value


def mongodb_uri(explicit, environ, env_file):
    """Use --uri, then an existing MONGODB_URI, then the local .env file."""
    if explicit:
        return explicit
    current = environ.get("MONGODB_URI", "")
    if current:
        return current
    return _dotenv_value(env_file, "MONGODB_URI")


def _dotenv_value(env_file, name):
    path = Path(env_file)
    if not path.is_file():
        return ""
    prefix = name + "="
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or not line.startswith(prefix):
            continue
        value = line[len(prefix):].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value
    return ""


if __name__ == "__main__":
    sys.exit(main())

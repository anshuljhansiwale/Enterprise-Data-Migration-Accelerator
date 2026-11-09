#!/bin/sh
# Load the order-management sample into a new Atlas database.
# MONGODB_URI and XAI_API_KEY come from the environment. This script does not print them.

set -eu
cd "$(dirname "$0")/.."

if [ -z "${MONGODB_URI:-}" ]; then
  echo "MONGODB_URI is missing. The Atlas load did not run." >&2
  exit 2
fi

python3 -m venv .venv
.venv/bin/pip install --quiet pymongo
.venv/bin/python -m migration \
  --spec fixtures/order-management/approved-spec.json \
  --source fixtures/order-management/source-rows.json

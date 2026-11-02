"""Target databases. Tests use memory. A URI uses one MongoClient with driver defaults."""

import json

_clients = {}


def _identity(doc_id):
    """Hashable identity for a scalar or compound _id."""
    if isinstance(doc_id, dict):
        return json.dumps(doc_id, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return doc_id


def _mongo_client(uri):
    """One client per process. Driver defaults: this batch has no pool or timeout measurements."""
    client = _clients.get(uri)
    if client is None:
        from pymongo import MongoClient

        client = MongoClient(uri)
        _clients[uri] = client
    return client


class MemoryDatabase:
    def __init__(self):
        self.docs = {}
        self.indexes = []
        self.validators = {}

    def upsert(self, collection, document):
        self.docs.setdefault(collection, {})
        self.docs[collection][_identity(document["_id"])] = document

    def count(self, collection):
        return len(self.docs.get(collection, {}))

    def get(self, collection, doc_id):
        return self.docs.get(collection, {}).get(_identity(doc_id))

    def create_index(self, collection, keys, name, partial_filter=None):
        self.indexes = [
            item
            for item in self.indexes
            if not (item["collection"] == collection and item["name"] == name)
        ]
        self.indexes.append(
            {
                "collection": collection,
                "keys": keys,
                "name": name,
                "partialFilter": partial_filter,
            }
        )

    def set_validator(self, collection, validation):
        self.validators[collection] = {"$jsonSchema": validation}


class MongoDatabase:
    def __init__(self, uri, database):
        self.client = _mongo_client(uri)
        self.db = self.client[database]

    def upsert(self, collection, document):
        self.db[collection].replace_one(
            {"_id": document["_id"]},
            document,
            upsert=True,
        )

    def count(self, collection):
        return self.db[collection].count_documents({})

    def get(self, collection, doc_id):
        return self.db[collection].find_one({"_id": doc_id})

    def create_index(self, collection, keys, name, partial_filter=None):
        kwargs = {"name": name}
        if partial_filter:
            kwargs["partialFilterExpression"] = partial_filter
        self.db[collection].create_index(list(keys.items()), **kwargs)

    def set_validator(self, collection, validation):
        options = {
            "validator": {"$jsonSchema": validation},
            "validationLevel": "strict",
            "validationAction": "error",
        }
        if collection in self.db.list_collection_names():
            self.db.command("collMod", collection, **options)
        else:
            self.db.create_collection(collection, **options)

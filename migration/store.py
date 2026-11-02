"""Target databases. Tests use memory. A URI uses one MongoClient with driver defaults."""

_CLIENTS = {}


def shared_client(uri, factory):
    """Return the process's client for this URI, creating it once."""
    client = _CLIENTS.get(uri)
    if client is None:
        client = factory(uri)
        _CLIENTS[uri] = client
    return client


def _default_client(uri):
    """Open one MongoClient. Pool size and timeouts stay at the driver defaults."""
    from pymongo import MongoClient

    return MongoClient(uri)


def mongo_client(uri):
    return shared_client(uri, _default_client)


def apply_bson_types(value, schema, long_type=None):
    """Encode integers as BSON long where the installed validator says so.

    bool is a subclass of int and stays a bool. The caller's document is not mutated.
    """
    if not isinstance(schema, dict):
        return value
    if long_type is None:
        from bson import Int64

        long_type = Int64
    bson_type = schema.get("bsonType")
    if bson_type == "long":
        if isinstance(value, bool) or not isinstance(value, int):
            return value
        return long_type(value)
    if bson_type == "object" and isinstance(value, dict):
        properties = schema.get("properties") or {}
        return {
            key: apply_bson_types(item, properties.get(key) or {}, long_type)
            for key, item in value.items()
        }
    if bson_type == "array" and isinstance(value, list):
        item_schema = schema.get("items") or {}
        return [apply_bson_types(item, item_schema, long_type) for item in value]
    return value


class MemoryDatabase:
    def __init__(self):
        self.docs = {}
        self.indexes = []
        self.validators = {}
        self.validation_levels = {}
        self.validation_actions = {}

    def upsert(self, collection, document):
        self.docs.setdefault(collection, {})
        self.docs[collection][document["_id"]] = document

    def count(self, collection):
        return len(self.docs.get(collection, {}))

    def documents(self, collection):
        return list(self.docs.get(collection, {}).values())

    def get(self, collection, doc_id):
        return self.docs.get(collection, {}).get(doc_id)

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
        # The spec's validation object is the $jsonSchema body, unchanged.
        self.validators[collection] = {"$jsonSchema": validation}
        self.validation_levels[collection] = "strict"
        self.validation_actions[collection] = "error"


class MongoDatabase:
    def __init__(self, uri, database):
        self.client = mongo_client(uri)
        self.db = self.client[database]
        self.schemas = {}

    def upsert(self, collection, document):
        schema = self.schemas.get(collection)
        encoded = apply_bson_types(document, schema) if schema else document
        doc_id = encoded["_id"]
        body = {key: value for key, value in encoded.items() if key != "_id"}
        self.db[collection].update_one({"_id": doc_id}, {"$set": body}, upsert=True)

    def count(self, collection):
        return self.db[collection].count_documents({})

    def documents(self, collection):
        return list(self.db[collection].find())

    def get(self, collection, doc_id):
        return self.db[collection].find_one({"_id": doc_id})

    def create_index(self, collection, keys, name, partial_filter=None):
        kwargs = {"name": name}
        if partial_filter:
            kwargs["partialFilterExpression"] = partial_filter
        self.db[collection].create_index(list(keys.items()), **kwargs)

    def set_validator(self, collection, validation):
        # Install the spec validator as written. strict/error is the driver's recommended action.
        self.schemas[collection] = validation
        validator = {"$jsonSchema": validation}
        options = {
            "validator": validator,
            "validationLevel": "strict",
            "validationAction": "error",
        }
        if collection in self.db.list_collection_names():
            self.db.command("collMod", collection, **options)
        else:
            self.db.create_collection(collection, **options)

"""Target databases. Tests use memory. A URI uses one MongoClient with driver defaults."""


class MemoryDatabase:
    def __init__(self):
        self.docs = {}
        self.indexes = []
        self.validators = {}

    def upsert(self, collection, document):
        self.docs.setdefault(collection, {})
        self.docs[collection][_doc_key(document["_id"])] = document

    def count(self, collection):
        return len(self.docs.get(collection, {}))

    def get(self, collection, doc_id):
        return self.docs.get(collection, {}).get(_doc_key(doc_id))

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


def _doc_key(doc_id):
    if isinstance(doc_id, dict):
        return tuple(sorted((name, _doc_key(value)) for name, value in doc_id.items()))
    return doc_id


class MongoDatabase:
    def __init__(self, uri, database):
        from pymongo import MongoClient

        self.client = MongoClient(uri)
        self.db = self.client[database]

    def upsert(self, collection, document):
        self.db[collection].update_one(
            {"_id": document["_id"]},
            {"$set": document},
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
        validator = {"$jsonSchema": validation}
        if collection in self.db.list_collection_names():
            self.db.command("collMod", collection, validator=validator)
        else:
            self.db.create_collection(collection, validator=validator)

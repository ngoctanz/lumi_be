from types import SimpleNamespace

from lumi_be.db import utc_date

COLLECTIONS = {
    "Device": "devices",
    "User": "users",
    "Asset": "assets",
    "ProviderCache": "provider_cache",
    "LoginSession": "login_sessions",
}


class FixtureDocument(SimpleNamespace):
    def __setattr__(self, name, value):
        if name != "_store" and "_store" in self.__dict__:
            db, collection, key = self._store
            db[collection].update_one({"_id": key}, {"$set": {name: value}})
        super().__setattr__(name, value)


def find_model(db, model_name, key):
    if model_name == "WidgetBinding":
        device = db.devices.find_one({"_id": key[0]})
        row = device["widgets"].get(key[1]) if device else None
        return SimpleNamespace(**row) if row else None
    collection = COLLECTIONS[model_name]
    row = db[collection].find_one({"_id": key})
    if row is None:
        return None
    record = FixtureDocument(**row, id=row["_id"])
    record._store = db, collection, key
    return record


def insert_model(db, value):
    row = value.model_dump(by_alias=True)
    if type(value).__name__ == "ProviderCache":
        row["expire_at"] = utc_date(row["updated_at"] + 86400)
    db[COLLECTIONS[type(value).__name__]].insert_one(row)


def insert_models(db, values):
    for value in values:
        insert_model(db, value)

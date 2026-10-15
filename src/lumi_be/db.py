"""One synchronous PyMongo pool per process, used by FastAPI's sync threadpool."""

from datetime import datetime, timezone

from pymongo import ASCENDING, MongoClient
from pymongo.errors import CollectionInvalid

SCHEMA_VERSION = 3


def utc_date(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc)


def database(settings):
    client = MongoClient(
        settings.mongodb_uri.get_secret_value(),
        maxPoolSize=settings.mongodb_pool_size,
        minPoolSize=0,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        socketTimeoutMS=10000,
        waitQueueTimeoutMS=5000,
        tz_aware=True,
        appname="lumi-be",
    )
    return client, client[settings.mongodb_database]


def initialize(db):
    """Operator command, idempotent; index failures must block deployment."""
    definitions = {
        "assets": {
            "required": ["_id", "model", "url", "sha256", "size"],
            "properties": {
                "owner_id": {"bsonType": ["string", "null"]},
                "model": {"bsonType": "string"},
                "url": {"bsonType": "string"},
                "sha256": {"bsonType": "string"},
                "size": {"bsonType": ["int", "long"]},
                "provider": {"bsonType": "string"},
                "provider_id": {"bsonType": ["string", "null"]},
                "preview_url": {"bsonType": ["string", "null"]},
                "preview_provider_id": {"bsonType": ["string", "null"]},
            },
        },
        "provider_cache": {
            "required": ["_id", "source_id", "payload", "updated_at", "expire_at"],
            "properties": {"payload": {"bsonType": "object"}, "expire_at": {"bsonType": "date"}},
        },
        "rate_limits": {
            "required": ["_id", "hits", "expire_at"],
            "properties": {"expire_at": {"bsonType": "date"}},
        },
    }
    for name, schema in definitions.items():
        schema = {
            "bsonType": "object",
            "properties": {"_id": {"bsonType": "string"}, **schema.get("properties", {})},
            **{k: v for k, v in schema.items() if k != "properties"},
        }
        try:
            db.create_collection(name, validator={"$jsonSchema": schema}, validationLevel="strict")
        except CollectionInvalid:
            db.command("collMod", name, validator={"$jsonSchema": schema}, validationLevel="strict")
    db.assets.create_index(
        [("model", ASCENDING), ("owner_id", ASCENDING), ("_id", ASCENDING)], name="ix_assets_scope"
    )
    db.assets.create_index(
        "provider_id",
        unique=True,
        name="uq_asset_provider_id",
        partialFilterExpression={"provider_id": {"$type": "string"}},
    )
    db.provider_cache.create_index("expire_at", expireAfterSeconds=0, name="ttl_provider_cache")
    db.rate_limits.create_index("expire_at", expireAfterSeconds=0, name="ttl_rate_limits")
    db.metadata.update_one({"_id": "schema"}, {"$set": {"version": SCHEMA_VERSION}}, upsert=True)


def ready(db):
    db.command("ping")
    return db.metadata.find_one({"_id": "schema", "version": SCHEMA_VERSION}) is not None

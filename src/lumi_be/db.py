"""One synchronous PyMongo pool per process, used by FastAPI's sync threadpool."""

from datetime import datetime, timezone

from pymongo import ASCENDING, MongoClient
from pymongo.errors import CollectionInvalid

SCHEMA_VERSION = 1


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
        "users": {
            "required": ["_id", "email", "password_hash", "created_at"],
            "properties": {"email": {"bsonType": "string"}, "password_hash": {"bsonType": "string"}},
        },
        "login_sessions": {
            "required": ["_id", "user_id", "expires_at", "expire_at"],
            "properties": {"user_id": {"bsonType": "string"}, "expire_at": {"bsonType": "date"}},
        },
        "devices": {
            "required": ["_id", "serial", "token_hash", "enabled", "revision", "config", "widgets"],
            "properties": {
                "serial": {"bsonType": "string"},
                "token_hash": {"bsonType": "string"},
                "owner_id": {"bsonType": ["string", "null"]},
                "enabled": {"bsonType": "bool"},
                "revision": {"bsonType": ["int", "long"], "minimum": 1, "maximum": 2147483647},
                "config": {"bsonType": "object"},
                "layout": {"bsonType": ["object", "null"]},
                "widgets": {
                    "bsonType": "object",
                    "maxProperties": 6,
                    "properties": {
                        k: {
                            "bsonType": "object",
                            "required": ["source_id", "params", "enabled"],
                            "properties": {
                                "source_id": {"bsonType": "string"},
                                "params": {"bsonType": "object"},
                                "enabled": {"bsonType": "bool"},
                            },
                        }
                        for k in ("clock", "weather", "calendar", "bot", "navigation", "stock")
                    },
                    "additionalProperties": False,
                },
            },
        },
        "assets": {"required": ["_id", "model", "url", "sha256", "size"]},
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
    db.users.create_index("email", unique=True, name="uq_user_email")
    db.login_sessions.create_index("user_id", name="ix_sessions_user")
    db.login_sessions.create_index("expire_at", expireAfterSeconds=0, name="ttl_sessions")
    db.devices.create_index("serial", unique=True, name="uq_device_serial")
    db.devices.create_index("token_hash", unique=True, name="uq_device_token")
    db.devices.create_index(
        "pairing_hash",
        unique=True,
        name="uq_active_pairing",
        partialFilterExpression={"pairing_hash": {"$type": "string"}},
    )
    db.devices.create_index([("owner_id", ASCENDING), ("_id", ASCENDING)], name="ix_devices_owner")
    db.assets.create_index(
        [("model", ASCENDING), ("owner_id", ASCENDING), ("_id", ASCENDING)], name="ix_assets_scope"
    )
    db.provider_cache.create_index("expire_at", expireAfterSeconds=0, name="ttl_provider_cache")
    db.rate_limits.create_index("expire_at", expireAfterSeconds=0, name="ttl_rate_limits")
    db.metadata.update_one({"_id": "schema"}, {"$set": {"version": SCHEMA_VERSION}}, upsert=True)


def ready(db):
    db.command("ping")
    return db.metadata.find_one({"_id": "schema", "version": SCHEMA_VERSION}) is not None

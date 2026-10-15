import os
import uuid

import pytest
from fastapi.testclient import TestClient

from lumi_be.app import create_app
from lumi_be.config import Settings
from lumi_be.db import initialize


@pytest.fixture
def app():
    # Atlas limits database names to 38 bytes. Keep random test databases well below it.
    name = "lumi_t_" + uuid.uuid4().hex[:24]
    app = create_app(
        Settings(
            mongodb_uri=os.getenv("LUMI_TEST_MONGODB_URI", "mongodb://127.0.0.1:27018"),
            mongodb_database=name,
            cdn_hosts=["cdn.example.test"],
            cloudinary_cloud_name=None,
            cloudinary_api_key=None,
            cloudinary_api_secret=None,
        )
    )
    initialize(app.state.db)
    # Keep a separate cleanup client because TestClient lifespan closes the app pool.
    from pymongo import MongoClient

    cleanup = MongoClient(app.state.settings.mongodb_uri.get_secret_value())
    yield app
    cleanup.drop_database(name)
    cleanup.close()
    app.state.mongo_client.close()


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client

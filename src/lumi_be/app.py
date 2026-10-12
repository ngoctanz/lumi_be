import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pymongo.errors import PyMongoError

from .cloud_images import CloudinaryImageStore
from .config import Settings
from .db import database
from .db import ready as database_ready
from .errors import error, install_errors
from .routers import app_devices, auth, device, widgets


class BoundedBody:
    """Reject oversized/chunked bodies before Pydantic reads them into memory."""

    def __init__(self, app, maximum, image_maximum):
        self.app, self.maximum, self.image_maximum = app, maximum, image_maximum

    def maximum_for(self, scope):
        parts = scope.get("path", "").strip("/").split("/")
        is_image_upload = (
            scope.get("method") == "POST"
            and len(parts) == 6
            and parts[:4] == ["api", "v1", "me", "devices"]
            and parts[5] == "assets"
        )
        return self.image_maximum + 65536 if is_image_upload else self.maximum

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        maximum = self.maximum_for(scope)
        chunks = []
        length = 0
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                return
            chunk = event.get("body", b"")
            length += len(chunk)
            if length > maximum:
                response = error(Request(scope), 413, "body_too_large", "Request body exceeds the API limit")
                return await response(scope, receive, send)
            chunks.append(chunk)
            if not event.get("more_body", False):
                break
        body = b"".join(chunks)
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, bounded_receive, send)


def create_app(settings: Settings | None = None):
    settings = settings or Settings()
    client, db = database(settings)

    @asynccontextmanager
    async def lifespan(_):
        yield
        client.close()

    app = FastAPI(
        title="Lumi Cloud",
        version="0.1.0",
        lifespan=lifespan,
        description="Versioned, account-isolated API. Devices authenticate with their own credentials.",
    )
    app.state.settings, app.state.mongo_client, app.state.db = settings, client, db
    app.state.image_store = CloudinaryImageStore(settings)
    app.add_middleware(
        BoundedBody,
        maximum=settings.request_max_bytes,
        image_maximum=settings.image_upload_max_bytes,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "If-None-Match"],
        expose_headers=["ETag", "X-Request-ID", "X-Frame-Age-Ms", "Retry-After"],
    )

    @app.middleware("http")
    async def metadata(request, call_next):
        request.state.request_id = uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    install_errors(app)

    @app.exception_handler(Exception)
    async def unexpected(request, _):
        logging.getLogger("lumi").error("Unhandled request failure id=%s", request.state.request_id)
        return error(request, 500, "internal_error", "Request could not be completed")

    @app.get("/health/live", tags=["Health"])
    def live():
        return {"status": "ok"}

    @app.get("/health/ready", tags=["Health"])
    def ready(request: Request):
        try:
            if not database_ready(db):
                return error(request, 503, "schema_missing", "Run the database initialization command")
        except PyMongoError:
            return error(
                request, 503, "database_unavailable", "Database unavailable or schema initialization missing"
            )
        return {"status": "ready"}

    app.include_router(auth.router)
    app.include_router(app_devices.router)
    app.include_router(device.router)
    app.include_router(widgets.router)
    return app

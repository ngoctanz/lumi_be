import time
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pymongo.database import Database

from .errors import fail
from .models import Device, User
from .security import digest

bearer = HTTPBearer(auto_error=False)


def get_db(request: Request):
    return request.app.state.db


DB = Annotated[Database, Depends(get_db)]
Authorization = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


def access_token(credentials: Authorization, prefix: str):
    if (
        not credentials
        or credentials.scheme.lower() != "bearer"
        or not credentials.credentials.startswith(prefix)
    ):
        fail(401, "unauthorized", "A valid bearer token is required")
    return credentials.credentials


def user(db: DB, credentials: Authorization):
    session = db.login_sessions.find_one(
        {"_id": digest(access_token(credentials, "usr_")), "expires_at": {"$gt": int(time.time())}}
    )
    document = db.users.find_one({"_id": session["user_id"]}) if session else None
    if not document:
        fail(401, "session_expired", "Sign in again")
    return User.model_validate(document)


def device(db: DB, credentials: Authorization):
    document = db.devices.find_one({"token_hash": digest(access_token(credentials, "dev_")), "enabled": True})
    if not document:
        fail(401, "device_unauthorized", "Device credentials are invalid or revoked")
    return Device.model_validate(document)


CurrentUser = Annotated[User, Depends(user)]
CurrentDevice = Annotated[Device, Depends(device)]

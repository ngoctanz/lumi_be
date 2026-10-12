import time

from fastapi import APIRouter, Request
from pymongo.errors import DuplicateKeyError

from ..db import utc_date
from ..deps import DB, Authorization, CurrentUser, access_token
from ..errors import fail
from ..models import User
from ..schemas import Credentials
from ..security import digest, dummy_hash, passwords, token
from ..services import limit, remote

router = APIRouter(prefix="/api/v1/auth", tags=["Accounts"])


def issue(db, user, settings):
    value = token("usr")
    expiry = int(time.time()) + settings.session_ttl_seconds
    db.login_sessions.insert_one(
        {"_id": digest(value), "user_id": user.id, "expires_at": expiry, "expire_at": utc_date(expiry)}
    )
    return {
        "access_token": value,
        "token_type": "bearer",
        "expires_in": settings.session_ttl_seconds,
        "user": {"id": user.id, "email": user.email},
    }


@router.post("/register", status_code=201)
def register(body: Credentials, request: Request, db: DB):
    limit(request, f"register:{remote(request)}", 5)
    if not request.app.state.settings.registration_enabled:
        fail(403, "registration_disabled", "Registration is disabled")
    user = User(email=body.email, password_hash=passwords.hash(body.password), created_at=int(time.time()))
    try:
        db.users.insert_one(user.mongo())
    except DuplicateKeyError:
        fail(409, "account_unavailable", "This account cannot be registered")
    return issue(db, user, request.app.state.settings)


@router.post("/login")
def login(body: Credentials, request: Request, db: DB):
    limit(request, f"login-ip:{remote(request)}", 20)
    limit(request, f"login-account:{body.email}", 10)
    document = db.users.find_one({"email": body.email})
    user = User.model_validate(document) if document else None
    valid = passwords.verify(body.password, user.password_hash if user else dummy_hash)
    if not user or not valid:
        fail(401, "invalid_credentials", "Email or password is incorrect")
    return issue(db, user, request.app.state.settings)


@router.get("/me")
def me(user: CurrentUser):
    return {"id": user.id, "email": user.email}


@router.post("/logout", status_code=204)
def logout(user: CurrentUser, credentials: Authorization, db: DB):
    db.login_sessions.delete_one({"_id": digest(access_token(credentials, "usr_")), "user_id": user.id})


@router.post("/logout-all", status_code=204)
def logout_all(user: CurrentUser, db: DB):
    db.login_sessions.delete_many({"user_id": user.id})

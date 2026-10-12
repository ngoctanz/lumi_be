import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


def error(request: Request, status: int, code: str, message: str, headers=None):
    request_id = getattr(request.state, "request_id", uuid.uuid4().hex)
    if request.url.path.startswith("/api/v1/device/"):
        body = {"v": 1, "t": "err", "code": code, "request_id": request_id}
    else:
        body = {"error": {"code": code, "message": message, "request_id": request_id}}
    return JSONResponse(body, status_code=status, headers=headers)


def install_errors(app: FastAPI):
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        return error(
            request,
            exc.status_code,
            detail.get("code", "request_failed"),
            detail.get("message", "Request failed"),
            exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, _):
        # Never echo password, tokens, navigation or rejected input into responses/logs.
        return error(request, 422, "validation_error", "Request does not match the API schema")


def fail(status: int, code: str, message: str):
    raise HTTPException(status, {"code": code, "message": message})

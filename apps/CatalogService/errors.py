"""DS09 error envelope and Flask error handlers.

Envelope: {"error", "message", "correlation_id", "details"}. Never includes stack traces,
SQL, tokens, Redis contents or secrets.
"""

from __future__ import annotations

import logging
from typing import Any

from flask import Flask
from redis.exceptions import RedisError
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError, SQLAlchemyError
from werkzeug.exceptions import HTTPException

logger = logging.getLogger("catalog.errors")

ERROR_CODE_BY_STATUS = {
    400: "invalid_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_error",
    500: "internal_error",
    503: "dependency_unavailable",
}

DEFAULT_MESSAGE_BY_STATUS = {
    400: "The request is invalid",
    401: "Authentication required",
    403: "You do not have permission to perform this action",
    404: "Resource not found",
    405: "Method not allowed for this resource",
    409: "The request conflicts with the current state of the resource",
    413: "Request body is too large",
    422: "The request could not be processed",
    500: "Unexpected internal error",
    503: "A required dependency is unavailable",
}


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        message: str | None = None,
        *,
        error: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message or DEFAULT_MESSAGE_BY_STATUS.get(status, "Error"))
        self.status = status
        self.error = error or ERROR_CODE_BY_STATUS.get(status, "error")
        self.message = message or DEFAULT_MESSAGE_BY_STATUS.get(status, "Error")
        self.details = details or {}

    @classmethod
    def invalid_request(cls, message: str, details: dict[str, Any] | None = None) -> "ApiError":
        return cls(400, message, details=details)

    @classmethod
    def unauthorized(cls, message: str = "Authentication required") -> "ApiError":
        return cls(401, message)

    @classmethod
    def forbidden(cls, message: str | None = None) -> "ApiError":
        return cls(403, message)

    @classmethod
    def not_found(cls, message: str = "Resource not found") -> "ApiError":
        return cls(404, message)

    @classmethod
    def conflict(cls, message: str, details: dict[str, Any] | None = None) -> "ApiError":
        return cls(409, message, details=details)

    @classmethod
    def validation_error(cls, message: str, details: dict[str, Any] | None = None) -> "ApiError":
        return cls(422, message, error="validation_error", details=details)

    @classmethod
    def invalid_reference(cls, message: str, details: dict[str, Any] | None = None) -> "ApiError":
        return cls(422, message, error="invalid_reference", details=details)

    @classmethod
    def dependency_unavailable(cls, dependency: str) -> "ApiError":
        return cls(503, f"{dependency.capitalize()} is unavailable", details={"dependency": dependency})


def register_error_handlers(app: Flask) -> None:
    # Imported here to avoid a cycle: serializers renders the envelope.
    from serializers import error_response

    @app.errorhandler(ApiError)
    def handle_api_error(exc: ApiError):
        if exc.status >= 500:
            logger.warning("request failed: %s (%s)", exc.error, exc.details.get("dependency", "-"))
        return error_response(exc)

    @app.errorhandler(HTTPException)
    def handle_http_exception(exc: HTTPException):
        status = exc.code or 500
        return error_response(ApiError(status))

    @app.errorhandler(RedisError)
    def handle_redis_error(exc: RedisError):
        logger.error("redis error: %s", type(exc).__name__)
        return error_response(ApiError.dependency_unavailable("redis"))

    @app.errorhandler(SQLAlchemyError)
    def handle_database_error(exc: SQLAlchemyError):
        # Connection-level failures are a dependency outage; anything else is a bug.
        if isinstance(exc, (OperationalError, InterfaceError)) or (
            isinstance(exc, DBAPIError) and exc.connection_invalidated
        ):
            logger.error("database unavailable: %s", type(exc).__name__)
            return error_response(ApiError.dependency_unavailable("database"))
        logger.exception("database error")
        return error_response(ApiError(500))

    @app.errorhandler(Exception)
    def handle_unexpected(exc: Exception):
        logger.exception("unhandled error")
        return error_response(ApiError(500))

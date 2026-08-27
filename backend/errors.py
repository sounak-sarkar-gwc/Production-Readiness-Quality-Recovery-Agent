"""Typed application errors -> consistent JSON error envelope.

Every error the API returns intentionally (as opposed to an actual bug)
should raise one of these, not a bare HTTPException, so the response shape
is uniform: {"error": {"type", "message", "request_id", ...}}. See
server.py's exception handlers for how these get turned into responses.
"""

from typing import Any, Dict, Optional


class AppError(Exception):
    status_code = 500
    error_type = "internal_error"

    def __init__(self, message: str, *, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.details = details


class NotFoundError(AppError):
    status_code = 404
    error_type = "not_found"


class InvalidInputError(AppError):
    status_code = 400
    error_type = "invalid_input"


class ConflictError(AppError):
    status_code = 409
    error_type = "conflict"


class AuthError(AppError):
    status_code = 401
    error_type = "unauthorized"


class ConfigurationError(AppError):
    """Server is missing something it needs (e.g. no API key configured) --
    not the caller's fault, so 503 rather than 4xx."""

    status_code = 503
    error_type = "configuration_error"


class UpstreamServiceError(AppError):
    """The Gemini API failed or timed out after retries."""

    status_code = 503
    error_type = "upstream_unavailable"

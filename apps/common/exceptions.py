from typing import Any


class ApplicationError(Exception):
    """Base error raised by services.

    The API exception handlers (config/api/exception_handlers.py) map every
    ApplicationError to the {"message": ..., "extra": {...}} contract using
    status_code. Domain apps subclass this per app (e.g. UserError) and may
    override status_code (e.g. 404 for a not-found error).
    """

    status_code: int = 400

    def __init__(self, message: str, extra: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.extra = extra or {}

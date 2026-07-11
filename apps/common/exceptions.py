from typing import Any


class ApplicationError(Exception):
    """Base error raised by services.

    The API exception handlers (config/api/exception_handlers.py, G03) map
    every ApplicationError to the {"message": ..., "extra": {...}} contract.
    Domain apps subclass this per app (e.g. UserError).
    """

    def __init__(self, message: str, extra: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.extra = extra or {}

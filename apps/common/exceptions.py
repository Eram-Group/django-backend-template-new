import re
from typing import Any


def _derive_code(class_name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", class_name.removesuffix("Error")).lower()


class ApplicationError(Exception):
    """Base error raised by services.

    The API exception handlers (config/api/exception_handlers.py) map every
    ApplicationError to the {"message": ..., "extra": {...}} contract using
    status_code. Domain apps subclass this per app (e.g. UserError) and may
    override status_code (e.g. 404 for a not-found error).

    Every subclass carries a stable machine-readable ``code`` - the
    snake_case class name (``UserNotFoundError`` -> ``user_not_found``),
    emitted as extra["code"]. The name IS the code - never overridden on the
    class; a raise site may still narrow it through ``extra={"code": ...}``,
    which the handler lets win. Messages are localized (Arabic-first), so
    clients branch on the code - never on the text.
    """

    status_code: int = 400
    code: str = "application"

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls.code = _derive_code(cls.__name__)

    # TODO(cleanup): make ``extra`` required once every raise site in
    # apps/payments and apps/notifications states it (~30 call sites).
    def __init__(self, message: str, *, extra: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.extra = {} if extra is None else extra

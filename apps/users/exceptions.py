from apps.common.exceptions import ApplicationError


class UserError(ApplicationError):
    """Base error for the users domain."""


class UserNotFoundError(UserError):
    status_code = 404

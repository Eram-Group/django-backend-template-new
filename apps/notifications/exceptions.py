from apps.common.exceptions import ApplicationError


class NotificationError(ApplicationError):
    """Base error for the notifications domain."""


class NotificationNotFoundError(NotificationError):
    status_code = 404

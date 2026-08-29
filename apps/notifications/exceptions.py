from apps.common.exceptions import ApplicationError


class NotificationError(ApplicationError):
    """Base error for the notifications domain."""


class NotificationNotFoundError(NotificationError):
    status_code = 404


class BroadcastStateError(NotificationError):
    """The broadcast is not in a status that allows the requested move."""


class BroadcastAudienceError(NotificationError):
    """The requested audience or channel selection is not coherent."""


class NotificationConfigError(NotificationError):
    """The requested notification-action config change is not allowed."""


class NotificationWebhookRejectedError(NotificationError):
    """Status webhook failed verification (bad/absent signature or config)."""

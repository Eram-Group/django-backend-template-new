from django.apps import AppConfig


class UsersConfig(AppConfig):
    name = "apps.users"
    label = "users"  # keeps AUTH_USER_MODEL = "users.User" stable
    verbose_name = "Users"

    def ready(self) -> None:
        from apps.users import signals  # noqa: F401

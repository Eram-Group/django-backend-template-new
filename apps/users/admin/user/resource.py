"""Import-export resource for User (explicit fields, never empty)."""

from apps.common.admin import BaseModelResource
from apps.users.models import User


class UserResource(BaseModelResource):
    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "name",
            "language",
            "is_active",
            "is_staff",
            "created_at",
        )

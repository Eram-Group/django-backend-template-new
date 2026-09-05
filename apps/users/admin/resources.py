"""Import-export resources for users: one per admin, explicit fields only.

Exports are read by non-engineers - never raw provider payloads or credentials.
"""

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

from django.db import models
from django.db.models import Func
from django.db.models.functions import Now


class BaseModel(models.Model):
    id = models.UUIDField(
        primary_key=True,
        editable=False,
        db_default=Func(function="uuidv7"),
    )
    created_at = models.DateTimeField(
        auto_now_add=True, db_default=Now(), db_index=True
    )
    updated_at = models.DateTimeField(auto_now=True, db_default=Now())

    class Meta:
        abstract = True

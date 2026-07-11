from django.db import models
from django.db.models import Func


class BaseModel(models.Model):
    """Abstract base for every concrete model in the project (including User).

    The pk is a UUIDv7 generated IN the database (PG18-native uuidv7()), so
    inserts outside Django (fixtures, raw SQL) get correct pks too; Django's
    INSERT ... RETURNING populates the value on save.
    """

    id = models.UUIDField(
        primary_key=True,
        editable=False,
        db_default=Func(function="uuidv7"),
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

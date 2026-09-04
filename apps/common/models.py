from django.db import models
from django.db.models.functions import UUID7
from django.db.models.functions import Now


class BaseModel(models.Model):
    """Abstract base for every concrete model in the project (including User).

    The pk is a UUIDv7 generated IN the database (``UUID7()`` renders the
    PG18-native uuidv7()), so inserts outside Django (fixtures, raw SQL) get
    correct pks too; Django's INSERT ... RETURNING populates the value on
    save. The timestamps carry
    db_default=Now() for the same reason - an out-of-Django INSERT that
    omits them must not hit NOT NULL. updated_at keeps auto_now on top
    because Postgres has no declarative on-update default (that would need
    a trigger); bulk .update() calls must set it explicitly.
    """

    id = models.UUIDField(
        primary_key=True,
        editable=False,
        db_default=UUID7(),
    )
    created_at = models.DateTimeField(
        auto_now_add=True, db_default=Now(), db_index=True
    )
    updated_at = models.DateTimeField(auto_now=True, db_default=Now())

    class Meta:
        abstract = True

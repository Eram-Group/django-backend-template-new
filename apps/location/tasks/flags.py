"""Flag download - one task per country, after the row is committed.

Idempotent and overwriting: the file name is fixed per code, and any
existing object under that name is deleted by NAME before the save.
Deleting through the field (``flag.delete()``) would only remove what the
DB currently points at - production storage has ``file_overwrite: False``,
so an orphan left by a crashed run would make the next save emit
``eg_XXXX.png`` and the flags directory would silt up.

A failed download escapes as OutboundError: FAILED task row + Sentry, the
country simply has no flag until the admin "Fetch flags" action retries.
"""

from django.core.files.base import ContentFile
from django.tasks import task
from django.utils import timezone

from apps.location.clients.flags import flag_fetch
from apps.location.models import Country


@task()
def fetch_country_flag(country_id: str) -> None:
    country = Country.objects.filter(pk=country_id).first()
    if country is None:
        return
    content = flag_fetch(code=country.code)
    filename = f"{country.code.lower()}.png"
    field = country.flag.field
    name = field.generate_filename(country, filename)
    storage = field.storage
    if storage.exists(name):
        storage.delete(name)
    # FieldFile.save applies upload_to itself - pass the bare filename.
    country.flag.save(filename, ContentFile(content), save=False)
    Country.objects.filter(pk=country.pk).update(
        flag=country.flag.name, updated_at=timezone.now()
    )

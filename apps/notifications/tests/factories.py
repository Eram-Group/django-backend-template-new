"""Notification factories - factory_boy structure, mimesis values."""

import uuid

from factory.declarations import LazyAttribute
from factory.declarations import Sequence
from factory.declarations import SubFactory
from factory.django import DjangoModelFactory

from apps.common.tests import fake
from apps.notifications.constants import DevicePlatform
from apps.notifications.constants import NotificationKind
from apps.notifications.models import Device
from apps.notifications.models import Notification
from apps.users.tests.factories import UserFactory

# Sequences restart at 0 per process, but --reuse-db keeps rows committed by
# session fixtures in earlier runs - the tag keeps registration_id unique.
_RUN_TAG = uuid.uuid4().hex[:6]


class DeviceFactory(DjangoModelFactory[Device]):
    class Meta:
        model = Device
        skip_postgeneration_save = True

    user = SubFactory(UserFactory)
    registration_id = Sequence(lambda n: f"fcm-token-{_RUN_TAG}-{n:08d}")
    platform = DevicePlatform.ANDROID


class NotificationFactory(DjangoModelFactory[Notification]):
    class Meta:
        model = Notification
        skip_postgeneration_save = True

    recipient = SubFactory(UserFactory)
    kind = NotificationKind.WELCOME
    # Context must satisfy the catalog's context_keys for the kind.
    context = LazyAttribute(
        lambda o: (
            {"name": fake.full_name(o.recipient.language)}
            if o.kind == NotificationKind.WELCOME
            else {"message": "Scheduled maintenance tonight."}
        )
    )

"""The operator's "needs attention" view of deliveries.

Nothing runs on a timer: a delivery a dead worker left PROCESSING, or a
transactional send the provider rejected, stays that way until an operator
re-queues it ("Re-queue stuck deliveries" on the Deliveries list; broadcasts
resume from their own page). This selector is what the sidebar badge and
the list filter show.
"""

from datetime import timedelta

from django.db.models import Q
from django.db.models import QuerySet
from django.utils import timezone

from apps.notifications.constants import DeliveryStatus
from apps.notifications.models import NotificationDelivery

#: A PROCESSING row untouched this long was claimed by a worker that died
#: mid-batch (a batch is one FCM/OurSMS call - seconds, not minutes).
STALE_PROCESSING_MINUTES = 30


def deliveries_needing_attention() -> QuerySet[NotificationDelivery]:
    cutoff = timezone.now() - timedelta(minutes=STALE_PROCESSING_MINUTES)
    return NotificationDelivery.objects.filter(
        Q(status=DeliveryStatus.PROCESSING, updated_at__lt=cutoff)
        | Q(status=DeliveryStatus.FAILED, broadcast__isnull=True)
    )

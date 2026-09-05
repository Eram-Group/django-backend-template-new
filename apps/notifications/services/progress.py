"""Broadcast progress counters - moved only by state transitions.

``sent_count``/``failed_count``/``skipped_count`` count delivery ROWS in
each terminal state, never attempts: a row that fails, is re-queued by
``deliveries_resume`` and then succeeds ends as one SENT row, one +1 and
one -1, not two increments. Every mover writes the delta in the same
transaction as the row change, so the counters can never drift from the
rows on a crash. GREATEST(...) keeps a counter at zero should a delta ever
outrun its rows.
"""

import uuid

from django.db.models import F
from django.db.models import Value
from django.db.models.functions import Greatest
from django.utils import timezone

from apps.notifications.models import Broadcast


def broadcast_record_progress(
    *,
    broadcast_id: uuid.UUID | str,
    sent: int = 0,
    failed: int = 0,
    skipped: int = 0,
) -> None:
    """Apply signed deltas to the outcome counters in one UPDATE."""
    deltas = {"sent_count": sent, "failed_count": failed, "skipped_count": skipped}
    updates = {
        field: Greatest(F(field) + Value(delta), Value(0))
        for field, delta in deltas.items()
        if delta
    }
    if not updates:
        return
    Broadcast.objects.filter(pk=broadcast_id).update(
        **updates, updated_at=timezone.now()
    )

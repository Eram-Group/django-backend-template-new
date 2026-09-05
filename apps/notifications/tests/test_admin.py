"""The Broadcast admin: the standard add form is the only authoring path.

The point of these tests is the seam the form closes - before it, the admin
add view called obj.save() directly, so services.notification_broadcast (and
with it the catalog's context validation) never ran, and a malformed
announcement only failed later inside the worker.

Every assertion is scoped by the marker message rather than
``Broadcast.objects.get()``: the admin basics gate seeds rows for every
registered factory outside the test transaction, so the table is never
empty.
"""

from typing import Any

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from apps.notifications.constants import BroadcastStatus
from apps.notifications.constants import Channel
from apps.notifications.constants import NotificationKind
from apps.notifications.models import Broadcast
from apps.notifications.selectors import notification_config_map
from apps.notifications.selectors import notification_render
from apps.notifications.tests.factories import BroadcastFactory
from apps.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

ADD_URL = "admin:notifications_broadcast_add"
REACH_URL = "admin:notifications_broadcast_audience_reach"
TITLE = "Composer test title"
MESSAGE = "Composer test announcement - unique to this module."
CONFIRM = {"_form_submitted": "1"}  # what unfold's confirmation dialog posts


def _superuser_client(client: Client) -> Client:
    client.force_login(UserFactory.create(is_staff=True, is_superuser=True))
    return client


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": TITLE,
        "message": MESSAGE,
        "language": "",
        "joined_after": "",
        "joined_before": "",
        "channels": [Channel.PUSH],
        "target": "filters",
        "_save": "Save",
    }
    payload.update(overrides)
    return payload


def _composed() -> Broadcast:
    return Broadcast.objects.get(context__message=MESSAGE)


def _nothing_composed() -> bool:
    return not Broadcast.objects.filter(context__message=MESSAGE).exists()


def _change_url(broadcast: Broadcast) -> str:
    return reverse("admin:notifications_broadcast_change", args=[broadcast.pk])


def _dispatch_url(broadcast: Broadcast) -> str:
    return reverse(
        "admin:notifications_broadcast_dispatch_broadcast", args=[broadcast.pk]
    )


def _resume_url(broadcast: Broadcast) -> str:
    return reverse(
        "admin:notifications_broadcast_resume_broadcast", args=[broadcast.pk]
    )


# --- the add form ---------------------------------------------------------------


def test_add_page_renders_the_composer(client: Client) -> None:
    response = _superuser_client(client).get(reverse(ADD_URL))

    assert response.status_code == 200
    body = response.content.decode()
    # The title/body pair replaces the raw context JSON box.
    assert 'name="title"' in body
    assert 'name="message"' in body
    assert 'name="context"' not in body
    # Stock admin widgets inside the composer layout: the user picker is
    # Django's autocomplete, the dates the admin date widget.
    assert "admin-autocomplete" in body
    assert 'name="joined_after"' in body
    # The live pieces: Alpine state fed by the widgets, htmx reach fragment.
    assert 'x-model.fill="title"' in body
    assert 'x-model.fill="target"' in body
    assert 'x-model.fill="channels"' in body
    assert f'hx-post="{reverse(REACH_URL)}"' in body
    assert 'hx-sync="this:replace"' in body  # a stale count can never win
    assert "{#" not in body  # a template comment leaking into the page


class TestAudienceReach:
    """The composer's live reach counter: the same form and the same query
    the dispatcher pages, rendered as an htmx fragment."""

    def test_counts_active_users_with_no_filters(self, client: Client) -> None:
        from apps.users.models import User

        User.objects.update(is_active=False)  # rolled back with the test
        UserFactory.create_batch(3)
        client.force_login(UserFactory.create(is_staff=True, is_superuser=True))

        response = client.post(reverse(REACH_URL), {"target": "filters"})

        assert response.status_code == 200
        body = response.content.decode()
        assert ">4<" in body  # the three plus the signed-in staff user
        assert "recipients" in body

    def test_counts_reachable_devices(self, client: Client) -> None:
        from apps.notifications.tests.factories import DeviceFactory
        from apps.users.models import User

        User.objects.update(is_active=False)
        staff = UserFactory.create(is_staff=True, is_superuser=True)
        DeviceFactory.create(user=staff)
        DeviceFactory.create(user=staff)  # two devices, one recipient
        client.force_login(staff)

        body = client.post(
            reverse(REACH_URL), {"target": "filters", "require_device": "on"}
        ).content.decode()

        assert ">1<" in body
        assert ">2<" in body

    def test_counts_exactly_the_picked_users(self, client: Client) -> None:
        picked = UserFactory.create()
        UserFactory.create()
        _superuser_client(client)

        body = client.post(
            reverse(REACH_URL), {"target": "users", "recipients": [str(picked.pk)]}
        ).content.decode()

        assert ">1<" in body

    def test_an_empty_audience_is_called_out(self, client: Client) -> None:
        picked = UserFactory.create(is_active=False)
        _superuser_client(client)

        body = client.post(
            reverse(REACH_URL), {"target": "users", "recipients": [str(picked.pk)]}
        ).content.decode()

        assert ">0<" in body
        assert "Dispatch will refuse it" in body

    def test_reversed_dates_render_the_error_not_a_count(self, client: Client) -> None:
        _superuser_client(client)

        body = client.post(
            reverse(REACH_URL),
            {
                "target": "filters",
                "joined_after": "2026-06-30",
                "joined_before": "2026-01-01",
            },
        ).content.decode()

        assert "bc-alert" in body  # the field error, in the viewer's language
        assert "bc-reach-n" not in body

    def test_anonymous_callers_are_redirected_to_login(self, client: Client) -> None:
        assert client.post(reverse(REACH_URL), {}).status_code == 302

    def test_a_staff_user_without_add_permission_is_refused(
        self, client: Client
    ) -> None:
        client.force_login(UserFactory.create(is_staff=True))

        response = client.post(reverse(REACH_URL), {})

        # admin_view bounces a staff user with no model perms at the door.
        assert response.status_code in (302, 403)


def test_compose_creates_a_draft_with_the_message_as_context(client: Client) -> None:
    response = _superuser_client(client).post(reverse(ADD_URL), _payload())

    assert response.status_code == 302
    broadcast = _composed()
    assert broadcast.kind == NotificationKind.ANNOUNCEMENT
    assert broadcast.status == BroadcastStatus.DRAFT
    assert broadcast.context == {"title": TITLE, "message": MESSAGE}
    # Stamped by the service from the request user, never typed.
    assert broadcast.created_by is not None


def test_a_title_is_required(client: Client) -> None:
    response = _superuser_client(client).post(reverse(ADD_URL), _payload(title=""))

    assert response.status_code == 200
    assert _nothing_composed()
    assert response.context["adminform"].form.errors["title"]


def test_the_authored_title_is_what_renders(client: Client) -> None:
    """The catalog title is "{title}" now - the operator's words, not a label."""
    _superuser_client(client).post(reverse(ADD_URL), _payload())

    rendered = notification_render(
        kind=NotificationKind.ANNOUNCEMENT,
        context=_composed().context,
        configs=notification_config_map(),
    )
    assert rendered.title == TITLE
    assert rendered.body == MESSAGE


def test_compose_records_the_audience_filters(client: Client) -> None:
    _superuser_client(client).post(
        reverse(ADD_URL),
        _payload(
            language="ar",
            require_device="on",
            joined_after="2026-01-01",
            joined_before="2026-06-30",
        ),
    )

    broadcast = _composed()
    assert broadcast.language == "ar"
    assert broadcast.require_device is True
    assert str(broadcast.joined_after) == "2026-01-01"
    assert str(broadcast.joined_before) == "2026-06-30"


def test_compose_records_selected_channels(client: Client) -> None:
    _superuser_client(client).post(
        reverse(ADD_URL), _payload(channels=[Channel.PUSH, Channel.SMS])
    )

    assert sorted(_composed().channels) == sorted([Channel.PUSH, Channel.SMS])


def test_compose_with_specific_users(client: Client) -> None:
    picked = UserFactory.create()

    _superuser_client(client).post(
        reverse(ADD_URL), _payload(target="users", recipients=[str(picked.pk)])
    )

    assert list(_composed().recipients.all()) == [picked]


def test_specific_users_needs_at_least_one(client: Client) -> None:
    response = _superuser_client(client).post(
        reverse(ADD_URL), _payload(target="users", recipients=[])
    )

    assert response.status_code == 200
    assert response.context["adminform"].form.errors["recipients"]
    assert _nothing_composed()


def test_filters_target_drops_a_leftover_pick(client: Client) -> None:
    """Switching back to "everyone" must not silently narrow the audience."""
    picked = UserFactory.create()

    _superuser_client(client).post(
        reverse(ADD_URL), _payload(target="filters", recipients=[str(picked.pk)])
    )

    assert not _composed().recipients.exists()


def test_channels_are_required(client: Client) -> None:
    """No kind-level default: a broadcast must say where it goes."""
    response = _superuser_client(client).post(reverse(ADD_URL), _payload(channels=[]))

    assert response.status_code == 200  # the form re-renders with the error
    assert "channels" in response.context["adminform"].form.errors
    assert _nothing_composed()


def test_reversed_dates_are_rejected_as_a_field_error(client: Client) -> None:
    response = _superuser_client(client).post(
        reverse(ADD_URL),
        _payload(joined_after="2026-06-30", joined_before="2026-01-01"),
    )

    assert response.status_code == 200  # redisplayed, not saved
    assert _nothing_composed()
    assert response.context["adminform"].form.errors["joined_before"]


def test_a_message_is_required(client: Client) -> None:
    response = _superuser_client(client).post(reverse(ADD_URL), _payload(message=""))

    assert response.status_code == 200
    assert response.context["adminform"].form.errors["message"]


def test_an_unsupported_channel_cannot_be_chosen(client: Client) -> None:
    response = _superuser_client(client).post(
        reverse(ADD_URL), _payload(channels=["carrier_pigeon"])
    )

    assert response.status_code == 200
    assert _nothing_composed()


def test_the_context_field_cannot_be_posted_on_add(client: Client) -> None:
    """The form owns the context shape - a hand-crafted POST cannot win."""
    _superuser_client(client).post(
        reverse(ADD_URL), _payload(context='{"message": "smuggled"}')
    )

    assert _composed().context == {"title": TITLE, "message": MESSAGE}


# --- the change form -----------------------------------------------------------


def test_change_form_shows_the_reach_of_a_draft(client: Client) -> None:
    """The reach is computed server-side from the same audience query the
    dispatcher pages - no live estimate endpoint."""
    picked = UserFactory.create()
    broadcast = BroadcastFactory.create(status=BroadcastStatus.DRAFT)
    broadcast.recipients.set([picked])

    body = _superuser_client(client).get(_change_url(broadcast)).content.decode()

    assert "1 recipients, 0 registered devices" in body


# --- lifecycle actions are permission-gated, not just status-gated ------------
#
# Before the guard checked the model permission, any is_staff account - with
# zero notifications permissions - could fan out a draft to the whole user
# base by URL. The actions confirm through a dialog: GET renders it, only the
# confirming POST runs the body.


def test_staff_without_permission_cannot_dispatch(client: Client) -> None:
    broadcast = BroadcastFactory.create(status=BroadcastStatus.DRAFT)
    client.force_login(UserFactory.create(is_staff=True))

    response = client.post(_dispatch_url(broadcast), CONFIRM)

    assert response.status_code == 403
    broadcast.refresh_from_db()
    assert broadcast.status == BroadcastStatus.DRAFT


def test_staff_without_permission_cannot_resume(client: Client) -> None:
    broadcast = BroadcastFactory.create(status=BroadcastStatus.DISPATCHED)
    client.force_login(UserFactory.create(is_staff=True))

    response = client.post(_resume_url(broadcast), CONFIRM)

    assert response.status_code == 403


def test_view_only_permission_cannot_dispatch(client: Client) -> None:
    broadcast = BroadcastFactory.create(status=BroadcastStatus.DRAFT)
    viewer = UserFactory.create(is_staff=True)
    viewer.user_permissions.add(Permission.objects.get(codename="view_broadcast"))
    client.force_login(viewer)

    response = client.post(_dispatch_url(broadcast), CONFIRM)

    assert response.status_code == 403


def test_change_permission_dispatches(client: Client) -> None:
    broadcast = BroadcastFactory.create(status=BroadcastStatus.DRAFT)
    operator = UserFactory.create(is_staff=True)
    operator.user_permissions.add(Permission.objects.get(codename="change_broadcast"))
    client.force_login(operator)

    response = client.post(_dispatch_url(broadcast), CONFIRM)

    assert response.status_code == 302  # back to the change form, dispatched
    broadcast.refresh_from_db()
    assert broadcast.status != BroadcastStatus.DRAFT


def test_dispatch_refuses_an_empty_audience(client: Client) -> None:
    """The old composer's confirm modal did this in JS; the service does it
    now, so every road (admin, shell) is covered."""
    picked = UserFactory.create(is_active=False)  # picked, then deactivated
    broadcast = BroadcastFactory.create(status=BroadcastStatus.DRAFT)
    broadcast.recipients.set([picked])
    client.force_login(UserFactory.create(is_staff=True, is_superuser=True))

    response = client.post(_dispatch_url(broadcast), CONFIRM)

    assert response.status_code == 302
    broadcast.refresh_from_db()
    assert broadcast.status == BroadcastStatus.DRAFT


@pytest.mark.parametrize(
    ("url_for", "status"),
    [(_dispatch_url, BroadcastStatus.DRAFT), (_resume_url, BroadcastStatus.DISPATCHED)],
)
def test_action_get_only_renders_the_confirmation(
    url_for: Any, status: BroadcastStatus
) -> None:
    """A GET (prefetch, unfurl, history restore) changes nothing; a POST
    without the CSRF token is refused."""
    broadcast = BroadcastFactory.create(status=status)
    client = Client(enforce_csrf_checks=True)
    client.force_login(UserFactory.create(is_staff=True, is_superuser=True))

    response = client.get(url_for(broadcast))

    assert response.status_code == 200
    assert 'name="_form_submitted"' in response.content.decode()
    assert client.post(url_for(broadcast), CONFIRM).status_code == 403
    broadcast.refresh_from_db()
    assert broadcast.status == status

from typing import TYPE_CHECKING
from typing import Any
from typing import cast

from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Model
from django.forms import ModelForm
from django.http import HttpRequest
from django.http import HttpResponse
from django.shortcuts import redirect
from django.shortcuts import render
from django.urls import path
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import RangeDateFilter
from unfold.decorators import action
from unfold.decorators import display
from unfold.forms import BaseDialogForm

from apps.common.admin import BaseModelAdmin
from apps.common.admin import FieldPermissions
from apps.common.admin import confirm_dialog
from apps.common.admin import on_change
from apps.common.exceptions import ApplicationError
from apps.notifications import selectors
from apps.notifications import services
from apps.notifications.admin.forms import MESSAGE_LIMIT
from apps.notifications.admin.forms import TITLE_LIMIT
from apps.notifications.admin.forms import BroadcastAudienceForm
from apps.notifications.admin.forms import BroadcastComposeForm
from apps.notifications.admin.resources import BroadcastResource
from apps.notifications.constants import BroadcastStatus
from apps.notifications.models import Broadcast

if TYPE_CHECKING:
    from apps.users.models import User


@admin.register(Broadcast)
class BroadcastAdmin(BaseModelAdmin):
    # Operators author a DRAFT here (title/message, audience, channels) on the
    # standard add form, then move it through its lifecycle with the
    # Dispatch/Resume detail actions - status, cursor and counters are
    # code-owned and stay read-only; content freezes once the row exists (a
    # dispatched broadcast must show what was sent).

    can_add = True
    can_change = True  # the change view hosts the lifecycle actions
    can_delete = False  # PROTECT semantics: history of what was sent stays
    field_permissions = FieldPermissions(
        readonly_when={
            "kind": on_change,
            "context": on_change,
            "language": on_change,
            # Audience and channels freeze with the content: a dispatched
            # broadcast must keep showing who it actually went to and how.
            "require_device": on_change,
            "joined_after": on_change,
            "joined_before": on_change,
            "recipients": on_change,
            "channels": on_change,
        },
    )
    resource_classes = [BroadcastResource]

    list_display = (
        "kind",
        "status",
        "language",
        "total_recipients",
        "sent_count",
        "failed_count",
        "skipped_count",
        "created_at",
    )
    list_filter = (
        "status",
        "kind",
        ("created_at", RangeDateFilter),
    )
    list_filter_submit = True  # form-based (range) filters apply on submit
    search_fields = ()
    search_help_text = ""
    ordering = ("-created_at",)
    list_per_page = 50

    # Composing an announcement and inspecting a dispatched one are different
    # jobs: the add view gets the message/audience form on the composer
    # template (two panes: the form, and a live summary with the reach), the
    # change view stays the plain frozen record. Mirrors
    # django.contrib.auth's UserAdmin. Presentation only: Django still owns
    # POST -> validate -> save_model -> redirect.
    add_form = BroadcastComposeForm
    add_form_template = "admin/notifications/broadcast/compose.html"
    add_fieldsets = (
        (None, {"fields": ("title", "message")}),
        (
            _("Audience"),
            {
                "fields": (
                    "target",
                    "recipients",
                    "language",
                    "require_device",
                    "joined_after",
                    "joined_before",
                ),
                "description": _("Leave every filter unset to reach all active users."),
            },
        ),
        (_("Channels"), {"fields": ("channels",)}),
    )
    fieldsets = (
        (None, {"fields": ("kind", "context")}),
        (
            _("Audience"),
            {
                "fields": (
                    "language",
                    "require_device",
                    "joined_after",
                    "joined_before",
                    "recipients",
                    "channels",
                    "audience_estimate",
                )
            },
        ),
        (
            _("Progress"),
            {
                "fields": (
                    "status",
                    "dispatch_cursor",
                    "total_recipients",
                    "total_deliveries",
                    "sent_count",
                    "failed_count",
                    "skipped_count",
                )
            },
        ),
        (_("Meta"), {"fields": ("created_by", "created_at", "updated_at")}),
    )
    # Code-owned: stamped from request.user / written by the dispatcher.
    readonly_fields = (
        "audience_estimate",
        "created_by",
        "status",
        "dispatch_cursor",
        "total_recipients",
        "total_deliveries",
        "sent_count",
        "failed_count",
        "skipped_count",
    )
    # The user picker: Django's autocomplete against UserAdmin.search_fields
    # (the operator needs the users view permission the endpoint checks).
    autocomplete_fields = ["recipients"]

    def get_urls(self) -> list[Any]:
        # Prepended so it wins over the admin's `<path:object_id>` catch-all,
        # which would otherwise read "audience-reach" as a primary key.
        reach = [
            path(
                "audience-reach/",
                self.admin_site.admin_view(self.audience_reach_view),
                name="notifications_broadcast_audience_reach",
            ),
        ]
        return reach + list(super().get_urls())

    def audience_reach_view(self, request: HttpRequest) -> HttpResponse:
        """The composer's live "who would this reach": an htmx fragment
        re-rendered on every audience change (the request carries the whole
        form; htmx's hx-sync drops a stale in-flight request, so the number
        on screen always belongs to the current selection).

        Validates only the audience half, through the same form and the same
        query the dispatcher pages - never a parallel reimplementation.
        """
        if not self.has_add_permission(request):
            raise PermissionDenied
        # Bound unconditionally, never `request.POST or None`: an empty POST
        # is the meaningful "no filters" case (everyone).
        form = BroadcastAudienceForm(data=request.POST)
        context: dict[str, Any] = {"form": form, "summary": None}
        if form.is_valid():
            audience = selectors.audience_queryset(**form.audience_filters())
            context["summary"] = selectors.audience_summary(audience=audience)
        return render(request, "admin/notifications/broadcast/_reach.html", context)

    def render_change_form(
        self,
        request: HttpRequest,
        context: dict[str, Any],
        add: bool = False,
        change: bool = False,
        form_url: str = "",
        obj: Any | None = None,
    ) -> HttpResponse:
        if add:
            context["reach_url"] = reverse(
                "admin:notifications_broadcast_audience_reach"
            )
            # Advisory counter limits; the form owns the numbers so the page
            # and any future validation cannot drift apart.
            context["title_limit"] = TITLE_LIMIT
            context["message_limit"] = MESSAGE_LIMIT
        response: HttpResponse = super().render_change_form(
            request, context, add=add, change=change, form_url=form_url, obj=obj
        )
        return response

    # Lifecycle buttons on the change form - each calls a service, never
    # obj.save(); the fan-out itself runs in the bulk-queue worker.
    actions_detail = ["dispatch_broadcast", "resume_broadcast"]

    @display(description=_("Reach"))
    def audience_estimate(self, obj: Broadcast) -> str:
        """Who a DRAFT would reach right now - the same query the dispatcher
        pages, so the number cannot drift from the send."""
        if obj.status != BroadcastStatus.DRAFT:
            return str(_("Dispatched - see the progress counters."))
        summary = selectors.broadcast_audience_summary(broadcast=obj)
        return str(
            _("%(recipients)d recipients, %(devices)d registered devices") % summary
        )

    def get_form(
        self,
        request: HttpRequest,
        obj: Any | None = None,
        change: bool = False,
        **kwargs: Any,
    ) -> type[ModelForm[Any]]:
        if obj is None:
            kwargs["form"] = self.add_form
        return super().get_form(request, obj, change=change, **kwargs)

    def get_fieldsets(self, request: HttpRequest, obj: Any | None = None) -> Any:
        if obj is None:
            return self.add_fieldsets
        return super().get_fieldsets(request, obj)

    def save_model(
        self, request: HttpRequest, obj: Model, form: ModelForm[Any], change: bool
    ) -> None:
        """Add goes through the service; change is content-frozen anyway.

        [WHY] Calling obj.save() on add skipped services.notification_broadcast
        entirely, so the catalog's context validation never ran from the admin -
        an announcement with an empty context saved fine and only blew up as a
        KeyError inside the worker, mid-dispatch. Routing add through the
        service is also the project rule: anything with business meaning calls
        the service, never obj.save().
        """
        if change or not isinstance(form, BroadcastComposeForm):
            super().save_model(request, obj, form, change)
            return
        broadcast = services.notification_broadcast(
            **form.service_kwargs(), actor=cast("User", request.user)
        )
        # Django builds the log entry and the post-save redirect from `obj`,
        # which the form left unsaved - point it at the row the service made.
        obj.pk = broadcast.pk

    def save_related(
        self, request: HttpRequest, form: ModelForm[Any], formsets: Any, change: bool
    ) -> None:
        # The service already set the recipients on add; Django's default
        # would re-save the M2M from the form onto the row.
        if change or not isinstance(form, BroadcastComposeForm):
            super().save_related(request, form, formsets, change)

    # Both lifecycle guards check the MODEL permission before the status
    # (unfold enforces has_<action>_permission inside the view), so status
    # alone must never authorize a send to the whole user base by any
    # is_staff account. The dialogs make the bodies POST-only: a GET on the
    # action URL renders the confirmation and changes nothing.

    def has_dispatch_broadcast_permission(
        self, request: HttpRequest, object_id: str | None = None
    ) -> bool:
        if not self.has_change_permission(request):
            return False
        if object_id is None:
            return True
        broadcast = Broadcast.objects.filter(pk=object_id).first()
        return broadcast is not None and broadcast.status == BroadcastStatus.DRAFT

    @action(
        description=_("Dispatch"),
        url_path="dispatch",
        permissions=["dispatch_broadcast"],
        icon="send",
        dialog=confirm_dialog(
            title=_("Dispatch this broadcast?"),
            description=_(
                "Every user in the audience (see Reach on the form) gets it on "
                "the selected channels. A dispatch cannot be recalled."
            ),
            submit=_("Dispatch"),
        ),
    )
    def dispatch_broadcast(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        broadcast = Broadcast.objects.get(pk=object_id)
        try:
            services.broadcast_dispatch(broadcast=broadcast)
        except ApplicationError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(
                request, _("Dispatch started - refresh to follow progress.")
            )
        return redirect(
            reverse("admin:notifications_broadcast_change", args=[object_id])
        )

    def has_resume_broadcast_permission(
        self, request: HttpRequest, object_id: str | None = None
    ) -> bool:
        if not self.has_change_permission(request):
            return False
        if object_id is None:
            return True
        broadcast = Broadcast.objects.filter(pk=object_id).first()
        return broadcast is not None and broadcast.status in (
            BroadcastStatus.DISPATCHING,
            BroadcastStatus.DISPATCHED,
        )

    @action(
        description=_("Resume incomplete"),
        url_path="resume",
        permissions=["resume_broadcast"],
        icon="replay",
        dialog=confirm_dialog(
            title=_("Resume the incomplete deliveries?"),
            description=_(
                "Re-enqueues exactly the deliveries that never completed; "
                "nothing already sent is sent again."
            ),
            submit=_("Resume"),
        ),
    )
    def resume_broadcast(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        broadcast = Broadcast.objects.get(pk=object_id)
        try:
            summary = services.deliveries_resume(
                broadcast=broadcast, include_failed=False
            )
        except ApplicationError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(
                request,
                _("Resume enqueued: %(summary)s")
                % {"summary": ", ".join(f"{k}={v}" for k, v in summary.items())},
            )
        return redirect(
            reverse("admin:notifications_broadcast_change", args=[object_id])
        )

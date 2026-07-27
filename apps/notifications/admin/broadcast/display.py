"""Computed display columns for Broadcast.

Every computed column must carry ordering= (changelist sorting keeps
working) and a translated description. Prefer unfold's display decorator -
it adds avatar header rows and colored label badges on top of django's:

    from unfold.decorators import display


    @display(description=_("Example"), header=True, ordering="created_at")
    def example_header(obj: Broadcast) -> list[object]:
        return [str(obj), obj.created_at, "XX"]  # title, subtitle, initials


    @display(
        description=_("State"),
        ordering="created_at",
        label={"True": "success", "False": "danger"},
    )
    def example_badge(obj: Broadcast) -> str:
        return str(obj)
"""

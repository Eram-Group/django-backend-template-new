"""Computed display columns for User.

Every computed column must carry ordering= (changelist sorting keeps
working) and a translated description. Prefer unfold's display decorator -
it adds avatar-style header rows and colored label badges on top of
django's:

    from unfold.decorators import display


    @display(description=_("User"), header=True, ordering="email")
    def user_header(obj: User) -> list[object]:
        # [title, subtitle, initials] renders an avatar header row
        return [obj.email, obj.name, obj.name[:2].upper()]


    @display(
        description=_("Active"),
        ordering="is_active",
        label={"True": "success", "False": "danger"},
    )
    def active_badge(obj: User) -> str:
        return str(obj.is_active)
"""

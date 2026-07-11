"""Computed display columns for User.

Every computed column must carry @admin.display(ordering=..., description=...)
so changelist sorting keeps working.
"""

# from django.contrib.admin import display
#
#
# @display(description="Example", ordering="created_at")
# def example_column(obj: object) -> str:
#     return str(obj)

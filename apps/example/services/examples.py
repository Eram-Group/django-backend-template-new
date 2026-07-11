"""Writes: keyword-only args, full_clean() before save, ApplicationError on failure.

Enqueue tasks only via transaction.on_commit (see apps/users/services/users.py).
"""

# from apps.example.models import Example
#
#
# def example_create(*, title: str) -> Example:
#     example = Example(title=title)
#     example.full_clean()
#     example.save()
#     return example

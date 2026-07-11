"""Reads only; each selector fetches exactly what its paired schema serializes."""

# import uuid
#
# from django.db.models import QuerySet
#
# from apps.example.exceptions import ExampleError
# from apps.example.models import Example
#
#
# def example_get(*, pk: uuid.UUID) -> Example:
#     try:
#         return Example.objects.get(pk=pk)
#     except Example.DoesNotExist as exc:
#         raise ExampleError("Example not found.") from exc
#
#
# def example_list() -> QuerySet[Example]:
#     return Example.objects.all()

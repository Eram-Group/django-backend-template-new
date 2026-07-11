"""One model per file; simple invariants only - business rules live in services."""

# from django.db import models
#
# from apps.common.models import BaseModel
# from apps.example.constants import ExampleStatus
#
#
# class Example(BaseModel):
#     title = models.CharField(max_length=255)
#     status = models.CharField(
#         max_length=16,
#         choices=ExampleStatus,
#         default=ExampleStatus.DRAFT,
#     )
#
#     def __str__(self) -> str:
#         return self.title

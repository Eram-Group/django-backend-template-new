"""Summary/Detail outputs + separate CreateIn/UpdateIn inputs - never ModelSchema."""

# import uuid
# from datetime import datetime
#
# from ninja import Schema
#
#
# class ExampleSummary(Schema):
#     id: uuid.UUID
#     title: str
#
#
# class ExampleDetail(ExampleSummary):
#     status: str
#     created_at: datetime
#
#
# class ExampleCreateIn(Schema):  # required fields
#     title: str
#
#
# class ExampleUpdateIn(Schema):  # all-optional; PATCH via .dict(exclude_unset=True)
#     title: str | None = None

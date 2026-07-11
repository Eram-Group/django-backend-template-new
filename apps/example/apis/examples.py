"""Thin endpoints: route + schemas + auth only; one function per endpoint.

List endpoints declare cursor pagination; auth comes from the NinjaAPI default.
"""

# from django.http import HttpRequest
# from ninja import Router
# from ninja.pagination import paginate
#
# from apps.common.pagination import CursorPagination
# from apps.example import selectors
# from apps.example import services
# from apps.example.schemas import ExampleCreateIn
# from apps.example.schemas import ExampleDetail
# from apps.example.schemas import ExampleSummary
#
# router = Router(tags=["examples"])
#
#
# @router.get("", response=list[ExampleSummary], summary="List examples")
# @paginate(CursorPagination)
# def example_list(request: HttpRequest):
#     return selectors.example_list()
#
#
# @router.post("", response=ExampleDetail, summary="Create example")
# def example_create(request: HttpRequest, payload: ExampleCreateIn):
#     return services.example_create(**payload.dict())

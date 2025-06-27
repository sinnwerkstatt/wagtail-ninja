import uuid
from datetime import datetime
from typing import Any

from ninja import ModelSchema, Schema
from pydantic import RootModel

from wagtail.api.v2.utils import get_full_url
from wagtail.models import Page


class PageMeta(Schema):
    type: str
    # detail_url: str TODO
    html_url: str
    slug: str
    show_in_menus: bool
    seo_title: str
    search_description: str
    first_published_at: datetime
    # alias_of: None  TODO
    # parent:   TODO
    locale: str


class StreamBlockSchema(Schema):
    type: str
    value: Any
    id: uuid.UUID


class StreamFieldSchema(RootModel):
    root: list[StreamBlockSchema] = []


class BasePageSchema(ModelSchema):
    meta: PageMeta

    class Config:
        model = Page
        model_fields = ["id", "title"]  # noqa: RUF012

    @staticmethod
    def resolve_meta(page: Page, context) -> PageMeta:
        return PageMeta(
            type=f"{page.specific_class._meta.app_label}.{type(page).__name__}",
            # detail_url="",  # TODO
            html_url=get_full_url(context["request"], page.get_url(context["request"])),
            slug=page.slug,
            show_in_menus=page.show_in_menus,
            seo_title=page.seo_title,
            search_description=page.search_description,
            first_published_at=page.first_published_at,
            # alias_of=None, TODO
            # parent, TODO
            locale=page.locale.language_code,
        )

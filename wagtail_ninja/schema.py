import uuid
from datetime import datetime
from typing import Any

from ninja import ModelSchema, Schema
from pydantic import Field, RootModel

from wagtail.api.v2.utils import get_full_url
from wagtail.contrib.redirects.models import Redirect
from wagtail.models import Page


class PageMeta(Schema):
    type: str
    # detail_url: str TODO
    html_url: str
    slug: str
    first_published_at: datetime | None
    locale: str


class PageParentMeta(Schema):
    type: str
    # detail_url: str
    html_url: str


class PageParent(Schema):
    id: int
    title: str
    meta: PageParentMeta

    @classmethod
    def from_page(cls, page: Page, context) -> "PageParent":
        meta = PageParentMeta(
            type=page.specific_class._meta.label,
            # detail_url="", TODO
            html_url=get_full_url(context["request"], page.get_url(context["request"])),
        )
        return PageParent(id=page.id, title=page.title, meta=meta)


class PageDetailMeta(PageMeta):
    show_in_menus: bool
    seo_title: str
    search_description: str
    # alias_of: None  TODO
    parent: PageParent | None


class StreamBlockSchema(Schema):
    type: str
    value: Any
    id: uuid.UUID


class StreamFieldSchema(RootModel):
    root: list[StreamBlockSchema] = []


class BasePageSchema(ModelSchema):
    meta: PageMeta
    content_type: str

    class Config:
        model = Page
        model_fields = ["id", "title"]  # noqa: RUF012

    @staticmethod
    def resolve_content_type(page: Page) -> str:
        """
        don't remove.
        this is part of the essential logic for the resolver to map the correct response
        """
        return page.specific_class._meta.label

    @staticmethod
    def resolve_meta(page: Page, context) -> PageMeta:
        return PageMeta(
            type=page.specific_class._meta.label,
            # detail_url="",  # TODO
            html_url=get_full_url(context["request"], page.get_url(context["request"])),
            slug=page.slug,
            first_published_at=page.first_published_at,
            locale=page.locale.language_code,
        )


class BasePageDetailSchema(BasePageSchema):
    meta: PageDetailMeta

    class Config(BasePageSchema.Config):
        pass

    @staticmethod
    def resolve_meta(page: Page, context) -> PageDetailMeta:
        # cant inherit from superclass. clashes somehow.

        prnt = page.get_parent()
        if prnt and not prnt.is_root():
            parent = PageParent.from_page(prnt, context)
        else:
            parent = None

        return PageDetailMeta(
            type=page.specific_class._meta.label,
            # detail_url="",  # TODO
            html_url=get_full_url(context["request"], page.get_url(context["request"])),
            slug=page.slug,
            first_published_at=page.first_published_at,
            locale=page.locale.language_code,
            show_in_menus=page.show_in_menus,
            seo_title=page.seo_title,
            search_description=page.search_description,
            # alias_of=None, TODO
            parent=parent,
        )


class WagtailImageMetaSchema(Schema):
    type: str
    # detail_url: str
    download_url: str


class WagtailImageSchema(Schema):
    id: int
    title: str
    width: int
    height: int
    meta: WagtailImageMetaSchema


class WagtailDocumentMetaSchema(Schema):
    type: str
    # detail_url: str
    download_url: str


class WagtailDocumentSchema(Schema):
    id: int
    title: str
    meta: WagtailDocumentMetaSchema


class RedirectSchema(ModelSchema):
    location: str = Field(None, alias="link")

    class Config:
        model = Redirect
        model_fields = ["id", "old_path", "is_permanent"]

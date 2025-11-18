import inspect
import logging
import sys
from collections.abc import Callable
from datetime import date, datetime
from functools import reduce
from operator import or_
from typing import Any, ClassVar, Literal, TypedDict, cast

from modelcluster.contrib.taggit import ClusterTaggableManager
from ninja import ModelSchema

from django.conf import settings
from django.core.exceptions import FieldDoesNotExist
from django.db.models import ForeignKey, ManyToOneRel
from django.urls import reverse
from wagtail import blocks as wagtail_blocks
from wagtail.api import APIField
from wagtail.api.v2.utils import get_full_url
from wagtail.blocks import StreamBlock
from wagtail.contrib.typed_table_block import blocks as typed_table_block_blocks
from wagtail.documents.models import Document
from wagtail.fields import RichTextField, StreamField
from wagtail.images.models import AbstractImage
from wagtail.models import Page, get_page_models
from wagtail.rich_text import expand_db_html

from wagtail_ninja import WagtailNinjaException
from wagtail_ninja.schema import (
    BasePageDetailSchema,
    StreamBlockSchema,
    StreamFieldSchema,
    WagtailDocumentSchema,
    WagtailImageSchema,
    WagtailTagSchema,
)

logger = logging.getLogger(__name__)


def serialize_streamfield(sfield: StreamField, context):
    cntnt = sfield.stream_block.get_api_representation(sfield, context)
    return cntnt


def serialize_image(img: AbstractImage | None, context):
    if img is None:
        return None

    return {
        "id": img.id,
        "meta": {
            "type": img.__class__._meta.label,
            "download_url": get_full_url(context["request"], img.file.url),
        },
        "title": img.title,
        "description": img.description,
        "width": img.width,
        "height": img.height,
    }


def serialize_document(doc: Document | None, context):
    if doc is None:
        return None

    return {
        "id": doc.id,
        "meta": {
            "type": doc.__class__._meta.label,
            "download_url": get_full_url(
                context["request"],
                reverse("wagtaildocs_serve", args=(doc.id, doc.filename)),
            ),
        },
        "title": doc.title,
    }


def _create_streamfield_resolver(_field: str):
    return staticmethod(
        lambda page, context: serialize_streamfield(getattr(page, _field), context)
    )


def _create_richtext_resolver(_field: str):
    return staticmethod(lambda page, context: expand_db_html(getattr(page, _field)))


def _create_many_to_one_rel_resolver(_field: str):
    return staticmethod(
        lambda page, context: getattr(page, _field).values_list("id", flat=True)
    )


def _create_cluster_taggable_manager_resolver(_field: str):
    # def get_tags(page, context):
    #     return getattr(page, _field).values_list("slug", flat=True)
    #
    # return staticmethod(get_tags)
    return staticmethod(
        lambda page, context: getattr(page, _field).values("id", "name", "slug")
    )


def _create_foreignkey_image_resolver(_field: str):
    return staticmethod(
        lambda page, context: serialize_image(getattr(page, _field), context)
    )


def _create_foreignkey_document_resolver(_field: str):
    return staticmethod(
        lambda page, context: serialize_document(getattr(page, _field), context)
    )


def _create_method_resolver(_field: str):
    def fn_call(page, context):
        fn = getattr(page, _field)

        if isinstance(getattr(type(page), _field, None), property):
            return fn

        return fn()

    return staticmethod(fn_call)
    # return staticmethod(lambda page, context: getattr(page, _field)())


# typed_table_block
class TypedTableColumn(TypedDict):
    type: str
    heading: str


class TypedTableRow(TypedDict):
    values: list[Any]


class TypedTable(TypedDict):
    caption: str
    columns: list[TypedTableColumn]
    rows: list[TypedTableRow]


WAGTAIL_STRUCT_BLOCKS = {}


def _wagtail_block_map(block: wagtail_blocks.FieldBlock, ident):
    # check if the block has get_api_repr first
    get_api_rep_fn = getattr(block, "get_api_representation", None)
    if get_api_rep_fn and callable(get_api_rep_fn):
        signature = inspect.signature(get_api_rep_fn)
        return_annotation = signature.return_annotation
        if return_annotation is not inspect._empty:
            return return_annotation

        # check for _wagtail_ninja_type_fn
        if _type_fn := getattr(get_api_rep_fn, "_wagtail_ninja_type_fn", None):
            if callable(_type_fn):
                return _type_fn()

    match block:
        case (
            wagtail_blocks.CharBlock()
            | wagtail_blocks.RichTextBlock()
            | wagtail_blocks.TextBlock()
            | wagtail_blocks.EmailBlock()
            | wagtail_blocks.URLBlock()
        ):
            if ident not in WAGTAIL_STRUCT_BLOCKS:
                WAGTAIL_STRUCT_BLOCKS[ident] = str
            return WAGTAIL_STRUCT_BLOCKS[ident]
        case wagtail_blocks.ChoiceBlock():
            return Literal.__getitem__(
                tuple(choice[0] for choice in block.field.choices)
            )
        case wagtail_blocks.BooleanBlock():
            return bool
        case wagtail_blocks.IntegerBlock():
            return int
        case wagtail_blocks.FloatBlock():
            return float
        case wagtail_blocks.DateBlock():
            return date
        case wagtail_blocks.DateTimeBlock():
            return datetime
        case wagtail_blocks.ListBlock():
            return list[_wagtail_block_map(block.child_block, ident)]
        case wagtail_blocks.StreamBlock():
            streamblocks = [
                TypedDict(
                    f"{block.__class__.__name__}_{name}_Value",
                    {"type": Literal[name], "value": _wagtail_block_map(child, name)},
                )
                for name, child in block.child_blocks.items()
            ]

            return list[
                TypedDict(
                    f"{block.__class__.__name__}Value",
                    {"value": list[reduce(or_, streamblocks)]},
                )
            ]
        case wagtail_blocks.StructBlock():
            if ident not in WAGTAIL_STRUCT_BLOCKS:
                props = {
                    name: _wagtail_block_map(child, name)
                    for name, child in block.child_blocks.items()
                }

                WAGTAIL_STRUCT_BLOCKS[ident] = TypedDict(
                    f"{block.__class__.__name__}Value", props
                )

            return WAGTAIL_STRUCT_BLOCKS[ident]

        # wagtail.contrib.typed_table_block
        case typed_table_block_blocks.TypedTableBlock():
            return TypedTable
            # columns = None
            # col_types = []
            # content_types = []
            # for block_name, block_type in block.child_blocks.items():
            #     # ColTypedDict = TypedDict(f"{block_name}Column", {"type": Literal[block_name], "heading": str})
            #     # if not columns:
            #     #     columns = ColTypedDict
            #     #     # columns = TypedDict(f"{block.__class__.__name__}Columns", {"type": Literal[block_name], "heading": str})
            #     # else:
            #     #     columns |= ColTypedDict
            #     col_types.append(block_name)
            #     content_types.append(_wagtail_block_map(block_type, block_name))
            # inner_block_types = block.child_blocks
            # print("MNUSS", inner_block_types)
            # print(col_types, content_types)
            # class MyTypeColumn(TypedDict):
            #     type: str
            #     heading: str
            # MyTypeColumn = TypedDict(
            #     f"{block.__class__.__name__}Column",
            #     {"type": Literal[[Literal[x] for x in col_types]], "heading": str},
            # )

            # class TypedTableColumn(TypedDict):
            #     type: str
            #     heading: str
            #
            # class TypedTableRow(TypedDict):
            #     values: list[Any]
            #
            # return TypedDict(
            #     f"{block.__class__.__name__}Value",
            #     {
            #         "caption": str,
            #         "columns": list[TypedTableColumn],
            #         "rows": list[TypedTableRow],
            #     },
            # )
            # return TypedDict(
            #     f"{block.__class__.__name__}Value",
            #     {
            #         "caption": str,
            #         "columns": list[
            #             TypedDict("TypedTableColumn", {"type": str, "heading": str})
            #         ],
            #         "rows": list[TypedDict("TypedTableRow", {"values": list[Any]})],
            #     },
            # )
        case _:
            logger.warning(f"unhandled block type: {block}")
            return Any


WAGTAIL_STREAMFIELD_TYPES = {}

WAGTAIL_BLOCK_TYPES = {}


def _create_streamfield_schema(
    model_field: StreamField, page_model: Page, fieldname: str
):
    blocks = None

    if isinstance(model_field.block_types_arg, StreamBlock):
        streamblocks = [
            (k, v) for k, v in model_field.block_types_arg.child_blocks.items()
        ]
    else:
        streamblocks = model_field.block_types_arg

    for block_ident, block in streamblocks:
        if getattr(settings, "WAGTAIL_NINJA_TYPE_STREAMFIELDBLOCKS", None):
            value = _wagtail_block_map(block, block_ident)
        else:
            value = Any

        if (block_ident, value) not in WAGTAIL_BLOCK_TYPES:
            WAGTAIL_BLOCK_TYPES[(block_ident, value)] = type(
                block.__class__.__name__,
                (StreamBlockSchema,),
                {"__annotations__": {"type": Literal[block_ident], "value": value}},
            )
        if blocks:
            blocks |= WAGTAIL_BLOCK_TYPES[(block_ident, value)]
        else:
            blocks = WAGTAIL_BLOCK_TYPES[(block_ident, value)]

    if WAGTAIL_STREAMFIELD_TYPES.get(blocks):
        return WAGTAIL_STREAMFIELD_TYPES[blocks]

    custom_stream_field = type(
        f"{page_model.__name__}.{fieldname}.StreamField",
        (StreamFieldSchema,),
        {"__annotations__": {"root": list[blocks]}},
    )
    WAGTAIL_STREAMFIELD_TYPES[blocks] = custom_stream_field
    # print(WAGTAIL_STREAMFIELD_TYPES)
    return custom_stream_field


def _get_method_annotations(fnc: Callable | property):
    if isinstance(fnc, property):
        signature = inspect.signature(fnc.fget)
    else:
        signature = inspect.signature(fnc)
    return_annotation = signature.return_annotation

    _type_fn = getattr(fnc, "_wagtail_ninja_type_fn", None)

    if return_annotation is not inspect._empty:
        ret_type = return_annotation
    elif _type_fn and callable(_type_fn):
        ret_type = _type_fn()
    else:
        ret_type = Any
    return ret_type


def _create_page_schema(page_model: Page) -> type[ModelSchema]:
    props: dict[Any, Any] = {
        "__module__": sys.modules[__name__].__name__,
        "__annotations__": {"content_type": Literal[page_model._meta.label]},
    }

    relevant_fields = []
    for field in getattr(page_model, "api_fields", []):
        if isinstance(field, APIField):
            if field.serializer:
                raise WagtailNinjaException(
                    f"api_fields cannot contain DRF serializers.\n{field} for {page_model}"
                )
            field = field.name
        try:
            model_field = page_model._meta.get_field(field)

            if (
                resolve_fn := getattr(page_model, f"resolve_{field}", None)
            ) and callable(resolve_fn):
                props["__annotations__"][field] = _get_method_annotations(resolve_fn)
                props[f"resolve_{field}"] = _create_method_resolver(f"resolve_{field}")
                continue  # won't register for Django-field mapping

            elif isinstance(model_field, StreamField):
                props["__annotations__"][field] = _create_streamfield_schema(
                    model_field, page_model, field
                )
                props[f"resolve_{field}"] = _create_streamfield_resolver(field)

            elif isinstance(model_field, RichTextField):
                props["__annotations__"][field] = str
                props[f"resolve_{field}"] = _create_richtext_resolver(field)

            elif isinstance(model_field, ForeignKey):
                if issubclass(model_field.related_model, AbstractImage):
                    props["__annotations__"][field] = WagtailImageSchema | None
                    props[f"resolve_{field}"] = _create_foreignkey_image_resolver(field)
                if issubclass(model_field.related_model, Document):
                    props["__annotations__"][field] = WagtailDocumentSchema | None
                    props[f"resolve_{field}"] = _create_foreignkey_document_resolver(
                        field
                    )
            elif isinstance(model_field, ManyToOneRel):
                props["__annotations__"][field] = list[int]
                props[f"resolve_{field}"] = _create_many_to_one_rel_resolver(field)
                continue  # won't register for Django-field mapping

            elif isinstance(model_field, ClusterTaggableManager):
                props["__annotations__"][field] = list[WagtailTagSchema]
                props[f"resolve_{field}"] = _create_cluster_taggable_manager_resolver(
                    field
                )
                continue  # won't register for Django-field mapping

            relevant_fields.append(field)

        except FieldDoesNotExist:
            ex_fnc = getattr(page_model, field, None)

            if isinstance(ex_fnc, Callable | property):
                ret_type = _get_method_annotations(ex_fnc)
                props["__annotations__"][field] = ret_type
                props[f"resolve_{field}"] = _create_method_resolver(field)

    cnfg = type(
        "Config",
        (BasePageDetailSchema.Config,),
        {"model": page_model, "model_fields": relevant_fields or ["title"]},
    )
    props["Config"] = cnfg
    props["__annotations__"]["Config"] = ClassVar[type]

    return cast(
        type[ModelSchema],
        type(str(page_model.__name__), (BasePageDetailSchema, ModelSchema), props),
    )


def create_pages_schemas() -> dict[type[Page], type[ModelSchema]]:
    schemas: dict[type[Page], type[ModelSchema]] = {}
    for model in get_page_models():
        if model == Page:
            continue

        page_schema = _create_page_schema(model)
        schemas[model] = page_schema

    return schemas

from __future__ import annotations

import inspect
import logging
import types
from collections.abc import Callable
from datetime import date, datetime
from functools import reduce
from operator import or_
from typing import (
    Any,
    Literal,
    TypedDict,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from modelcluster.contrib.taggit import ClusterTaggableManager

from django.conf import settings
from django.core.exceptions import FieldDoesNotExist
from django.db.models import ForeignKey, ManyToOneRel
from wagtail import blocks as wagtail_blocks
from wagtail.api import APIField
from wagtail.api.v2.utils import get_full_url
from wagtail.blocks import StreamBlock
from wagtail.contrib.typed_table_block import blocks as typed_table_block_blocks
from wagtail.documents.models import AbstractDocument
from wagtail.fields import RichTextField, StreamField
from wagtail.images.models import AbstractImage
from wagtail.models import Page

from wagtail_ninja import WagtailNinjaException
from wagtail_ninja.schema import (
    StreamBlockSchema,
    StreamFieldSchema,
)

logger = logging.getLogger(__name__)


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
        hints = get_type_hints(fnc.fget)
    else:
        hints = get_type_hints(fnc)

    return_annotation = hints.get("return", inspect._empty)

    _type_fn = getattr(fnc, "_wagtail_ninja_type_fn", None)

    if return_annotation is not inspect._empty:
        ret_type = return_annotation
    elif _type_fn and callable(_type_fn):
        ret_type = _type_fn()
    else:
        ret_type = Any
    return ret_type


def derive_annotations_and_resolvers(page_model: Page):
    relevant_fields = []
    field_annotations = []
    resolvers = []
    imports = set()
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
                print("juchu")
                raise NotImplementedError("resolve_fn not supported yet")
                # props["__annotations__"][field] = _get_method_annotations(resolve_fn)
                # props[f"resolve_{field}"] = _create_method_resolver(f"resolve_{field}")
                continue  # won't register for Django-field mapping

            elif isinstance(model_field, StreamField):
                # xxtyp = _create_streamfield_schema(model_field, page_model, field)
                # TODO
                field_annotations += [f"{field}: Any"]
                # to get a better pydantic matching maybe here something like
                # Union[xx|xx] = Field(discriminator='fieldname')
                resolvers += [
                    f"@staticmethod\n"
                    f"    def resolve_{field}(page, context):\n"
                    f"        return page.{field}.stream_block.get_api_representation(page.{field},context)"
                ]

            elif isinstance(model_field, RichTextField):
                field_annotations += [
                    f"{field}: str = Field(title='{model_field.verbose_name}',description='{model_field.help_text}')"
                ]
                resolvers += [
                    f"@staticmethod\n"
                    f"    def resolve_{field}(page) -> str:\n"
                    f"        return expand_db_html(page.{field})"
                ]
                imports.add("from wagtail.rich_text import expand_db_html")

            elif isinstance(model_field, ForeignKey):
                if issubclass(model_field.related_model, AbstractImage):
                    field_annotations += [
                        f"{field}: WagtailImageSchema | None = Field(title='{model_field.verbose_name}',description='{model_field.help_text}')"
                    ]
                    imports.add("from wagtail_ninja.schema import WagtailImageSchema")

                if issubclass(model_field.related_model, AbstractDocument):
                    field_annotations += [
                        f"{field}: WagtailDocumentSchema | None = Field(title='{model_field.verbose_name}',description='{model_field.help_text}')"
                    ]
                    imports.add(
                        "from wagtail_ninja.schema import WagtailDocumentSchema"
                    )
            elif isinstance(model_field, ManyToOneRel):
                field_annotations += [
                    f"{field}: list[int] = Field(title='{model_field.verbose_name}', description='{model_field.help_text}')"
                ]
                resolvers += [
                    f"@staticmethod\n"
                    f"    def resolve_{field}(page) -> str:\n"
                    f'        return page.{field}.values_list("id", flat=True)',
                ]
                continue  # won't register for Django-field mapping

            elif isinstance(model_field, ClusterTaggableManager):
                field_annotations += [f"{field}: list[WagtailTagSchema]"]

                resolvers += [
                    f"@staticmethod\n"
                    f"    def resolve_{field}(page) -> str:\n"
                    f'        return page.{field}.values("id", "name", "slug")',
                ]
                imports.add("from wagtail_ninja.schema import WagtailTagSchema")

                continue  # won't register for Django-field mapping

            relevant_fields.append(field)

        except FieldDoesNotExist:
            ex_fnc = getattr(page_model, field, None)

            if isinstance(ex_fnc, Callable | property):
                ret_type = _get_method_annotations(ex_fnc)
                new_ret_type = _resolve_type_and_imports(ret_type, imports)

                field_annotations += [f"{field}: {new_ret_type}"]

                if isinstance(ex_fnc, property):
                    resolvers += [
                        f"@staticmethod\n"
                        f"    def resolve_{field}(page):\n"
                        f"        return page.{field}"
                    ]
                else:
                    resolvers += [
                        f"@staticmethod\n"
                        f"    def resolve_{field}(page):\n"
                        f"        return page.{field}()"
                    ]

    return field_annotations, resolvers, imports, relevant_fields


def _resolve_type_and_imports(annotation: Any, imports: set) -> str:

    if annotation is inspect._empty:
        imports.add("from typing import Any")
        return "Any"

    if annotation is Any:
        imports.add("from typing import Any")
        return "Any"

    if annotation is type(None):
        return "None"

    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is None:
        if isinstance(annotation, str):
            return annotation

        mod = getattr(annotation, "__module__", "")
        name = getattr(annotation, "__name__", str(annotation))

        if mod and mod != "builtins":
            imports.add(f"from {mod} import {name}")

        return name

    if origin is Union or origin is types.UnionType:
        formatted_args = [_resolve_type_and_imports(arg, imports) for arg in args]
        return " | ".join(formatted_args)

    origin_mod = getattr(origin, "__module__", "")
    origin_name = getattr(origin, "__name__", str(origin))

    # Handle custom generics or typing structures (like List)
    if origin_mod and origin_mod not in ("builtins", "typing"):
        imports.add(f"from {origin_mod} import {origin_name}")

    # Format inner arguments inside the brackets
    if args:
        formatted_args = [_resolve_type_and_imports(arg, imports) for arg in args]
        return f"{origin_name}[{', '.join(formatted_args)}]"

    return origin_name

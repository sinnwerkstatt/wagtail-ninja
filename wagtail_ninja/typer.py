import inspect
import sys
from collections.abc import Callable
from typing import Any, ClassVar, Literal, TypedDict, cast

from ninja import ModelSchema

from django.conf import settings
from django.core.exceptions import FieldDoesNotExist
from django.db.models import ForeignKey, ManyToOneRel
from django.urls import reverse
from wagtail import blocks as wagtail_blocks
from wagtail.api import APIField
from wagtail.api.v2.utils import get_full_url
from wagtail.documents.models import Document
from wagtail.fields import RichTextField, StreamField
from wagtail.images.models import Image
from wagtail.models import Page, get_page_models
from wagtail.rich_text import expand_db_html

from wagtail_ninja import WagtailNinjaException
from wagtail_ninja.schema import (
    BasePageDetailSchema,
    StreamBlockSchema,
    StreamFieldSchema,
    WagtailDocumentSchema,
    WagtailImageSchema,
)


def serialize_streamfield(sfield: StreamField, context):
    cntnt = sfield.stream_block.get_api_representation(sfield, context)
    return cntnt


def serialize_image(img: Image | None, context):
    if img is None:
        return None

    return {
        "id": img.id,
        "meta": {
            "type": img.__class__._meta.label,
            "download_url": get_full_url(context["request"], img.file.url),
        },
        "title": img.title,
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


WAGTAIL_STRUCT_BLOCKS = {}


def _wagtail_block_map(block: wagtail_blocks.FieldBlock, ident):
    # check if the block has get_api_repr first
    get_api_rep_fn = getattr(block, "get_api_representation", None)
    if get_api_rep_fn and callable(get_api_rep_fn):
        signature = inspect.signature(get_api_rep_fn)
        return_annotation = signature.return_annotation
        if return_annotation is not inspect._empty:
            return return_annotation

    match block:
        case (
            wagtail_blocks.CharBlock()
            | wagtail_blocks.RichTextBlock()
            | wagtail_blocks.TextBlock()
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
        case wagtail_blocks.StreamBlock():
            props = {}
            for name, child in block.child_blocks.items():
                props[name] = _wagtail_block_map(child, name)
            return TypedDict(f"{block.__class__.__name__}Value", props)
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

        case _:
            return Any


WAGTAIL_STREAMFIELD_TYPES = {}

WAGTAIL_BLOCK_TYPES = {}


def _create_streamfield_schema(
    model_field: StreamField, page_model: Page, fieldname: str
):
    blocks = None
    for block_ident, block in model_field.block_types_arg:
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
                if issubclass(model_field.related_model, Image):
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

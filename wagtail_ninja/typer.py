import inspect
import sys
from typing import Any, ClassVar, Literal, TypedDict

from ninja import ModelSchema

from django.core.exceptions import FieldDoesNotExist
from wagtail import blocks as wagtail_blocks
from wagtail.fields import RichTextField, StreamField
from wagtail.models import Page, get_page_models
from wagtail.rich_text import expand_db_html

from wagtail_ninja.schema import (
    BasePageDetailSchema,
    StreamBlockSchema,
    StreamFieldSchema,
)


def _create_richtext_resolver(_field: str):
    return staticmethod(lambda page, context: expand_db_html(getattr(page, _field)))


def _create_method_resolver(_field: str):
    return staticmethod(lambda page, context: getattr(page, _field)())


def _serialize_streamfield(sfield: StreamField, context):
    cntnt = sfield.stream_block.get_api_representation(sfield, context)
    return cntnt


def _create_streamfield_resolver(_field: str):
    return staticmethod(
        lambda page, context: _serialize_streamfield(getattr(page, _field), context)
    )


def _wagtail_block_map(block: wagtail_blocks.FieldBlock):
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
            return str
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
            # print(block)
            # props = {}
            # # print("oki")
            # for name, child in block.child_blocks.items():
            #     props[name] = _wagtail_block_map(child)
            # ic(props)
            # TODO circular dependency incoming. need to solve this nicer.
            return list[Any]
        case wagtail_blocks.StructBlock():
            # TODO circular dependency incoming. need to solve this nicer.
            props = {}
            for name, child in block.child_blocks.items():
                # print(name)
                props[name] = _wagtail_block_map(child)
                # print(_wagtail_block_map(child))
                # print(name, child)
            return TypedDict(f"{block.__class__.__name__}Value", props)

            # return dict
        case _:
            return Any


def _create_streamfield_schema(
    model_field: StreamField, page_model: Page, fieldname: str
):
    blocks = None
    for block_ident, block in model_field.block_types_arg:
        block_schema = type(
            block.__class__.__name__,
            (StreamBlockSchema,),
            {
                "__annotations__": {
                    "type": Literal[block_ident],
                    "value": _wagtail_block_map(block),
                }
            },
        )
        if blocks:
            blocks |= block_schema
        else:
            blocks = block_schema

    custom_stream_field = type(
        f"{page_model.__name__}.{fieldname}.StreamField",
        (StreamFieldSchema,),
        {"__annotations__": {"root": list[blocks]}},
    )
    return custom_stream_field


def _create_page_schema(page_model: Page):
    props = {
        "__module__": sys.modules[__name__].__name__,
        "__annotations__": {"content_type": Literal[page_model._meta.label]},
    }

    relevant_fields = []
    for field in getattr(page_model, "api_fields", []):
        try:
            model_field = page_model._meta.get_field(field)

            if isinstance(model_field, StreamField):
                # props["__annotations__"][field] = _create_streamfield_schema(
                #     model_field, page_model, field
                # )
                props["__annotations__"][field] = StreamFieldSchema
                props[f"resolve_{field}"] = _create_streamfield_resolver(field)

            if isinstance(model_field, RichTextField):
                props["__annotations__"][field] = str
                props[f"resolve_{field}"] = _create_richtext_resolver(field)

            relevant_fields.append(field)

        except FieldDoesNotExist:
            ex_fnc = getattr(page_model, field, None)

            signature = inspect.signature(ex_fnc)
            return_annotation = signature.return_annotation

            if callable(ex_fnc):
                props["__annotations__"][field] = (
                    Any if return_annotation is inspect._empty else return_annotation
                )
                props[f"resolve_{field}"] = _create_method_resolver(field)

    cnfg = type(
        "Config",
        (BasePageDetailSchema.Config,),
        {"model": page_model, "model_fields": relevant_fields or ["title"]},
    )
    props["Config"] = cnfg
    props["__annotations__"]["Config"] = ClassVar[type]

    new_class = type(
        str(page_model.__name__), (BasePageDetailSchema, ModelSchema), props
    )
    return new_class


def create_pages_schemas():
    schemas = None
    for model in get_page_models():
        if model == Page:
            continue

        page_schema = _create_page_schema(model)
        if not schemas:
            schemas = page_schema
        else:
            schemas |= page_schema

    return schemas

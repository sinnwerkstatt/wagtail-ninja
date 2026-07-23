from __future__ import annotations

import inspect
import logging
import types
from collections.abc import Callable
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
from wagtail.blocks import StreamBlock
from wagtail.contrib.typed_table_block import blocks as typed_table_block_blocks
from wagtail.documents.models import AbstractDocument
from wagtail.fields import RichTextField, StreamField
from wagtail.images.models import AbstractImage
from wagtail.models import Page

from wagtail_ninja import WagtailNinjaException
from wagtail_ninja._internal_schema import SchemaModel, SchemasModule, State

logger = logging.getLogger(__name__)


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


def new_block_map(block: wagtail_blocks.FieldBlock, more_class_defs, imports):
    # check if the block has get_api_representation first
    get_api_rep_fn = getattr(block, "get_api_representation", None)
    if get_api_rep_fn and callable(get_api_rep_fn):
        hints = get_type_hints(get_api_rep_fn)
        return_annotation = hints.get("return", inspect._empty)

        if return_annotation is not inspect._empty:
            return return_annotation

        # check for _wagtail_ninja_type_fn
        if _type_fn := getattr(get_api_rep_fn, "_wagtail_ninja_type_fn", None):
            if callable(_type_fn):
                return _type_fn()

    postfix = "" if block.required else " | None"
    match block:
        case (
            wagtail_blocks.CharBlock()
            | wagtail_blocks.RichTextBlock()
            | wagtail_blocks.TextBlock()
            | wagtail_blocks.EmailBlock()
            | wagtail_blocks.URLBlock()
        ):
            return "str" + postfix
        case wagtail_blocks.ChoiceBlock():
            return Literal.__getitem__(
                tuple(choice[0] for choice in block.field.choices)
            )
        case wagtail_blocks.BooleanBlock():
            return "bool" + postfix
        case wagtail_blocks.IntegerBlock():
            return "int" + postfix
        case wagtail_blocks.FloatBlock():
            return "float" + postfix
        case wagtail_blocks.DateBlock():
            return "date" + postfix
        case wagtail_blocks.DateTimeBlock():
            return "datetime" + postfix
        case wagtail_blocks.ListBlock():
            return f"list[{new_block_map(block.child_block, more_class_defs, imports)}]"
        case wagtail_blocks.StreamBlock():
            return "Any"
            print("STREAMBLOCK")

            print(block)
            print(block.child_blocks.items())
            if block.name == "zutaten":
                print("SCHRITT")
                props = {
                    name: new_block_map(child, more_class_defs, imports)
                    for name, child in block.child_blocks.items()
                }
                print(props)

            # streamblocks = [
            #     TypedDict(
            #         f"{block.__class__.__name__}_{name}_Value",
            #         {"type": Literal[name], "value": _wagtail_block_map(child, name)},
            #     )
            #     for name, child in block.child_blocks.items()
            # ]
            #
            # return list[
            #     TypedDict(
            #         f"{block.__class__.__name__}Value",
            #         {"value": list[reduce(or_, streamblocks)]},
            #     )
            # ]
        case wagtail_blocks.StructBlock():
            # print("STRUCTBLOCK")
            # print(block)

            props = {
                name: new_block_map(child, more_class_defs, imports)
                for name, child in block.child_blocks.items()
            }
            more_class_defs += [f"class {block.__class__.__name__}Value(Schema):"]
            more_class_defs += [f"    {x}: {y}" for x, y in props.items()]
            return f"{block.__class__.__name__}Value"

        case wagtail_blocks.StaticBlock():
            return "None"

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
            return "Any"


WAGTAIL_STREAMFIELD_TYPES = {}

WAGTAIL_BLOCK_TYPES = {}


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


global_known_blocks = {}


def big_stream_resolver(model_field, imports: set[str], state):
    ret = []
    if isinstance(model_field.block_types_arg, StreamBlock):
        streamblocks = [
            (k, v) for k, v in model_field.block_types_arg.child_blocks.items()
        ]
    else:
        streamblocks = model_field.block_types_arg

    blocknames = []

    for block in streamblocks:
        if inspect.isclass(block[1]):
            current_block_class = block[1]
        else:
            current_block_class = block[1].__class__

        blockname = f"Gen{current_block_class.__name__}Schema"

        if block[0] in global_known_blocks:
            former_block_class = global_known_blocks[block[0]]

            if former_block_class is not current_block_class:
                x1 = inspect.getfile(former_block_class)
                x2 = inspect.getfile(current_block_class)

                raise Exception(
                    f"Block {block[0]} is defined twice with different types. This is not supported yet.\n"
                    f"{former_block_class} {x1}\n"
                    f"{block[1]} {x2}"
                )

        else:
            more_class_defs = []
            _val = _resolve_type_and_imports(
                new_block_map(block[1], more_class_defs, imports),
                state.schemas_init.imports,
            )

            if more_class_defs:
                state.schemas_init.block_defs += more_class_defs
            state.schemas_init.block_defs += [
                f"class {blockname}(Schema):\n"
                f'    id: str'
                f'    type: Literal["{block[0]}"]\n'
                f"    value: {_val}"
                # f"    value: Any"
            ]

            state.schemas_init.imports.add("import typing")

            global_known_blocks[block[0]] = current_block_class

        blocknames.append(f"{blockname}")

    if len(streamblocks) == 1:
        imports.add(f"from {state.modpath}.schemas import {blocknames[0]}")
        return f"list[{blocknames[0]}]", "\n".join(ret)

    imports.add(f"from {state.modpath}.schemas import {','.join(blocknames)}")

    state.schemas_init.block_names.update(blocknames)

    return (
        f'list[Annotated[{"|".join(blocknames)}, Field(discriminator="type")]]',
        "\n".join(ret),
    )


def derive_annotations_and_resolvers(
    page_model: Page, state: State, schemas_module: SchemasModule
):
    extra_schemas = []
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
                raise NotImplementedError("resolve_fn not supported yet")
                # props["__annotations__"][field] = _get_method_annotations(resolve_fn)
                # props[f"resolve_{field}"] = _create_method_resolver(f"resolve_{field}")
                continue  # won't register for Django-field mapping

            elif isinstance(model_field, StreamField):
                xtype, _xtra_schemas = big_stream_resolver(model_field, imports, state)
                extra_schemas += [_xtra_schemas]
                field_annotations += [f"{field}: {xtype}"]

                resolvers += [
                    f"@staticmethod\n"
                    f"    def resolve_{field}(page, context):\n"
                    f"        return page.{field}.stream_block.get_api_representation(page.{field},context)"
                ]
                imports.add("from ninja import Schema")
                imports.add("from typing import Annotated")
                imports.add("from pydantic import RootModel")

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
                    if hasattr(settings, "WAGTAILIMAGES_IMAGE_MODEL"):
                        imports.add(
                            f"from {state.modpath}.schemas import WagtailImageSchema"
                        )
                    else:
                        imports.add(
                            "from wagtail_ninja.schema import WagtailImageSchema"
                        )

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

    schemas_module.models.append(
        SchemaModel(
            name=page_model.__name__,
            fields=relevant_fields or ["title"],
            annotations=field_annotations,
            resolvers=resolvers,
            extra_schemas=extra_schemas,
        )
    )
    schemas_module.imports.update(imports)


def _resolve_type_and_imports(annotation: Any, imports: set) -> str:
    if isinstance(annotation, str):
        return annotation

    if annotation is inspect._empty:
        imports.add("from typing import Any")
        return "Any"

    if annotation is Any:
        imports.add("from typing import Any")
        return "Any"

    if annotation is type(None):
        return "None"

    if annotation is Literal:
        return annotation

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

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
from django.db.models import CharField, ForeignKey, ManyToOneRel
from wagtail import blocks as wagtail_blocks
from wagtail.api import APIField
from wagtail.blocks import StreamBlock
from wagtail.contrib.typed_table_block import blocks as typed_table_block_blocks
from wagtail.documents.models import AbstractDocument
from wagtail.fields import RichTextField, StreamField
from wagtail.images.models import AbstractImage
from wagtail.models import Page

from wagtail_ninja import WagtailNinjaError
from wagtail_ninja._internal_schema import BlockDef, SchemaModel, SchemasModule, State

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


def new_block_map(block: wagtail_blocks.Block, imports, state: State):
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

        # if "get_api_representation" in block.__class__.__dict__:
        #     # it is specified and not only inherited and there's no return type
        #     return "Any"

    suffix = "" if block.required else " | None"
    match block:
        case (
            wagtail_blocks.CharBlock()
            | wagtail_blocks.RichTextBlock()
            | wagtail_blocks.TextBlock()
            | wagtail_blocks.EmailBlock()
            | wagtail_blocks.URLBlock()
        ):
            return "str"
        case wagtail_blocks.ChoiceBlock():
            ret = f"Literal[{', '.join(repr(choice[0]) for choice in block.field.choices)}]"
            if block._default is None and not block.required:
                return ret + " | None"

            return ret

        case wagtail_blocks.BooleanBlock():
            return "bool" + suffix
        case wagtail_blocks.IntegerBlock():
            return "int" + suffix
        case wagtail_blocks.DecimalBlock():
            return "float" + suffix
        case wagtail_blocks.FloatBlock():
            return "float" + suffix
        case wagtail_blocks.DateBlock():
            return "date" + suffix
        case wagtail_blocks.DateTimeBlock():
            return "datetime" + suffix
        case wagtail_blocks.ChooserBlock():
            return "int" + suffix
        case wagtail_blocks.ListBlock():
            return f"list[{new_block_map(block.child_block, imports, state)}]"
        case wagtail_blocks.StreamBlock():
            current_block_class = block.__class__

            blocknames = []
            for name, child in block.child_blocks.items():
                blockname = f"Gen{child.__class__.__name__}Schema"

                if existing_block := state.schemas_init.stream_blocks.get(
                    child.__class__.__name__
                ):
                    if child.__class__ is not existing_block.model:
                        x1 = inspect.getfile(existing_block.model)
                        x2 = inspect.getfile(child.__class__)

                        raise Exception(
                            f"Block {name} is defined twice with different types. This is not supported yet.\n"
                            f"{existing_block.model} {x1}\n"
                            f"{child} {x2}"
                        )
                else:
                    resolved = _resolve_type_and_imports(
                        new_block_map(child, imports, state),
                        state.schemas_init.imports,
                    )

                    state.schemas_init.stream_blocks[child.__class__.__name__] = (
                        BlockDef(
                            model=child.__class__,
                            definition=(
                                f"class {blockname}(Schema):\n"
                                f"    id: str\n"
                                f"    type: Literal['{name}']\n"
                                f"    value: {resolved}\n"
                            ),
                        )
                    )
                blocknames.append(f"{blockname}")

            return (
                f'list[Annotated[{"|".join(blocknames)}, Field(discriminator="type")]]'
            )

        case wagtail_blocks.StructBlock():
            current_block_class = block.__class__

            if existing_block := state.schemas_init.struct_blocks.get(
                current_block_class.__name__
            ):
                if current_block_class is not existing_block.model:
                    x1 = inspect.getfile(existing_block.model)
                    x2 = inspect.getfile(current_block_class)

                    raise Exception(
                        f"Block is defined twice with different types. This is not supported yet.\n"
                        f"{existing_block.model} {x1}\n"
                        f"{block} {x2}"
                    )
            else:
                props = {
                    name: _resolve_type_and_imports(
                        new_block_map(child, imports, state),
                        state.schemas_init.imports,
                    )
                    for name, child in block.child_blocks.items()
                }

                state.schemas_init.struct_blocks[current_block_class.__name__] = (
                    BlockDef(
                        model=current_block_class,
                        definition=(
                            "\n".join(
                                [
                                    f"class {current_block_class.__name__}Value(Schema):",
                                    *[f"    {x}: {y}" for x, y in props.items()],
                                ]
                            )
                        ),
                    )
                )

            return f"{current_block_class.__name__}Value"

        case wagtail_blocks.StaticBlock():
            return "None"

        case typed_table_block_blocks.TypedTableBlock():
            # wagtail.contrib.typed_table_block
            return TypedTable

        case _:
            try:
                # Get the source file path
                source_file = inspect.getsourcefile(type(block))
                # Get the starting line number of its class/definition
                _, line_num = inspect.getsourcelines(type(block))
                location = f"{source_file}:{line_num}"
            except (TypeError, OSError):
                # Fallback if inspect fails (e.g. built-in types or dynamic objects)
                location = "unknown"

            logger.warning(f"unhandled block type: {block} at {location}")
            return "Any"


def big_stream_resolver(model_field, imports: set[str], state):
    ret = []

    # StreamBlock vs simple python tuple list
    if isinstance(model_field.block_types_arg, StreamBlock):
        streamblocks = [
            (k, v) for k, v in model_field.block_types_arg.child_blocks.items()
        ]
    else:
        streamblocks = model_field.block_types_arg

    blocknames = []

    for block in streamblocks:
        block_tag: str = block[0]
        block_val: wagtail_blocks.Block = block[1]

        current_block_class = block_val.__class__

        blockname = f"Gen{current_block_class.__name__}Schema"

        if existing_block := state.schemas_init.stream_blocks.get(
            current_block_class.__name__
        ):
            if current_block_class is not existing_block.model:
                x1 = inspect.getfile(existing_block.model)
                x2 = inspect.getfile(current_block_class)

                raise Exception(
                    f"Block {block_tag} is defined twice with different types. This is not supported yet.\n"
                    f"{existing_block.model} {x1}\n"
                    f"{block_val} {x2}"
                )

        else:
            _val = _resolve_type_and_imports(
                new_block_map(block_val, imports, state),
                state.schemas_init.imports,
            )

            state.schemas_init.stream_blocks[current_block_class.__name__] = BlockDef(
                model=current_block_class,
                definition=(
                    f"class {blockname}(Schema):\n"
                    f"    id: str\n"
                    f'    type: Literal["{block_tag}"]\n'
                    f"    value: {_val}"
                ),
            )

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
                raise WagtailNinjaError(
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

            elif isinstance(model_field, CharField):
                if choices := model_field.get_choices():
                    ret = f"Literal[{', '.join(repr(choice[0]) for choice in choices)}]"
                    if model_field.null:
                        ret = f"{ret} | None"
                    field_annotations += [f"{field}: {ret}"]
                else:
                    field_annotations += [f"{field}: str"]
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

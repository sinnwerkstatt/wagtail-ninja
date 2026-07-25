from pathlib import Path
from typing import Any

from pydantic import BaseModel


class BlockDef(BaseModel):
    model: Any
    definition: str


class SchemasInit(BaseModel):
    imports: set[str] = {
        "from ninja import ModelSchema",
        "from ninja import Schema",
        "from typing import Any",
        "from typing import Literal",
        "from typing import Annotated",
        "from pydantic import Field",
    }
    stream_blocks: dict[str, BlockDef] = {}
    struct_blocks: dict[str, BlockDef] = {}
    block_names: set[str] = set()
    image_model: str | None = None

    def structblock_defs(self):
        return [x.definition for x in self.struct_blocks.values()]

    def block_defs(self):
        return [x.definition for x in self.stream_blocks.values()]

    def sorted_block_names(self):
        return "|".join(sorted(self.block_names))


class ApiPair(BaseModel):
    model: str
    schema_name: str


class ApiApp(BaseModel):
    app_label: str
    name: str
    pairs: list[ApiPair]


class State(BaseModel):
    outdir: Path
    modpath: str
    schemas_init: SchemasInit = SchemasInit()
    api_apps: list[ApiApp] = []


class SchemaModel(BaseModel):
    name: str
    fields: list[str]
    annotations: list[str] = []
    resolvers: list[str] = []
    extra_schemas: list[str] = []


class SchemasModule(BaseModel):
    app_label: str
    imports: set[str] = set()
    models: list[SchemaModel] = []
    # output: int

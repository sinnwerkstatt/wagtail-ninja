from pathlib import Path

from pydantic import BaseModel


class SchemasInit(BaseModel):
    imports: set[str] = {
        "from ninja import ModelSchema",
        "from ninja import Schema",
        "from typing import Any",
        "from typing import Literal",
    }
    block_defs: list[str] = []
    image_model: str | None = None


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

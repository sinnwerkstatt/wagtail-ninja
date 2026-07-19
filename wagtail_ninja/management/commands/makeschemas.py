from __future__ import annotations

import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand
from django.template.loader import render_to_string
from wagtail.models import Page

from wagtail_ninja._internal_schema import (
    ApiApp,
    ApiPair,
    SchemasModule,
    State,
)
from wagtail_ninja.typer import derive_annotations_and_resolvers


class Command(BaseCommand):
    help = "Generates Django Ninja schemas for Wagtail Pages"

    def handle(self, *args, **options):
        outfolder = "ninja_generated"
        outdir = Path(settings.BASE_DIR) / outfolder
        if not outdir.exists():
            outdir.mkdir()
        schemas_dir = outdir / "schemas"
        if not schemas_dir.exists():
            schemas_dir.mkdir()

        state = State(outdir=outdir, modpath=outfolder)

        if hasattr(settings, "WAGTAILIMAGES_IMAGE_MODEL"):
            state.schemas_init.image_model = settings.WAGTAILIMAGES_IMAGE_MODEL
            state.schemas_init.imports.add(
                "from wagtail.api.v2.utils import get_full_url"
            )

        _generated_files = self.write_schemas(state)

        if state.schemas_init.block_defs or state.schemas_init.image_model:
            output_path = self.write_schemas_init(state)
            _generated_files.append(output_path)

        api_output_path = self.write_api_file(state)
        _generated_files.append(api_output_path)
        self.cleanup(_generated_files)

    def write_schemas_init(self, state: State):
        output_path = state.outdir / "schemas/__init__.py"

        with open(output_path, "w") as f:
            f.write(
                render_to_string("wagtail_ninja/schemas_init.py.j2", {"state": state})
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully generated central schema at {output_path}"
            )
        )
        return output_path

    def write_schemas(self, state: State):
        generated_files = []

        app_models = defaultdict(list)
        for model in apps.get_models():
            if issubclass(model, Page) and model is not Page:
                app_models[model._meta.app_label].append(model)

        for app_label, models in app_models.items():
            app_config = apps.get_app_config(app_label)
            output_path = state.outdir / f"schemas/{app_label}.py"

            schemas_module = SchemasModule(app_label=app_label)
            api_pairs: list[ApiPair] = []

            for model in models:
                derive_annotations_and_resolvers(model, state, schemas_module)

                model_name = model.__name__
                api_pairs.append(
                    ApiPair(model=model_name, schema=f"Gen{model_name}Schema")
                )

            with open(output_path, "w") as f:
                f.write(
                    render_to_string(
                        "wagtail_ninja/schemas_module.py.j2",
                        {
                            "schemas_module": schemas_module,
                        },
                    )
                )
                generated_files.append(str(output_path))

            state.api_apps.append(
                ApiApp(app_label=app_label, name=app_config.name, pairs=api_pairs)
            )

            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully generated schemas for app: {app_label}"
                )
            )

        return generated_files

    def write_api_file(self, state: State):
        api_output_path = state.outdir / "api.py"

        with open(api_output_path, "w") as f:
            f.write(
                render_to_string(
                    "wagtail_ninja/api.py.j2",
                    {"state": state},
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully generated central router at {api_output_path}"
            )
        )
        return api_output_path

    def cleanup(self, generated_files):
        if shutil.which("ruff"):
            self.stdout.write("Running Ruff on generated code...")
            try:
                subprocess.run(  # noqa: S603
                    ["ruff", "check", "--fix", *generated_files],  # noqa: S607
                    check=True,
                    capture_output=True,
                )
                subprocess.run(  # noqa: S603
                    ["ruff", "format", *generated_files],  # noqa: S607
                    check=True,
                    capture_output=True,
                )
                self.stdout.write(
                    self.style.SUCCESS("Successfully formatted with Ruff.")
                )
            except subprocess.CalledProcessError as e:
                self.stdout.write(
                    self.style.WARNING(
                        f"Ruff formatting failed:\n{e.stdout.decode()}\n{e.stderr.decode()}"
                    )
                )
        else:
            self.stdout.write(
                self.style.WARNING(
                    "Ruff is not installed or not in PATH. Skipping formatting."
                )
            )

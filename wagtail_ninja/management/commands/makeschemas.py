from __future__ import annotations

import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand
from django.template import Context
from wagtail.models import Page

from wagtail_ninja.templates import api_template, schema_template
from wagtail_ninja.typer import derive_annotations_and_resolvers


class Command(BaseCommand):
    help = "Generates Django Ninja schemas for Wagtail Pages"

    def handle(self, *args, **options):

        generated_files, api_context_data = self.write_schemas()
        self.write_api_file(api_context_data, generated_files)
        self.cleanup(generated_files)

    def write_schemas(self):
        generated_files = []
        api_context_data = []

        app_models = defaultdict(list)

        for model in apps.get_models():
            if issubclass(model, Page) and model is not Page:
                app_models[model._meta.app_label].append(model)

        for app_label, models in app_models.items():
            app_config = apps.get_app_config(app_label)
            output_path = Path(app_config.path) / "generated_schemas.py"

            context_data = []
            schema_names = []
            api_pairs = []
            imports = set()

            for model in models:
                _annotations, resolvers, _imports, relevant_fields = (
                    derive_annotations_and_resolvers(model)
                )
                imports.update(_imports)

                model_name = model.__name__
                context_data.append(
                    {
                        "name": model_name,
                        "fields": relevant_fields or ["title"],
                        "annotations": _annotations,
                        "resolvers": resolvers,
                    }
                )
                schema_names.append(f"{model_name}Schema")

                api_pairs.append({"model": model_name, "schema": f"{model_name}Schema"})

            rendered_code = schema_template.render(
                Context(
                    {"app_label": app_label, "models": context_data, "imports": imports}
                )
            )

            with open(output_path, "w") as f:
                f.write(rendered_code)
                generated_files.append(str(output_path))

            api_context_data.append({"name": app_config.name, "pairs": api_pairs})

            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully generated schemas for app: {app_label}"
                )
            )
        return generated_files, api_context_data

    def write_api_file(self, api_context_data, generated_files):
        api_output_path = Path(settings.BASE_DIR) / "generated_api.py"

        with open(api_output_path, "w") as f:
            f.write(api_template.render(Context({"api_apps": api_context_data})))
            generated_files.append(str(api_output_path))

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully generated central router at {api_output_path}"
            )
        )

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

import functools
import operator
from importlib.util import find_spec

from ninja import ModelSchema, Router, Schema

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.http import Http404, HttpRequest
from django.shortcuts import get_object_or_404, redirect
from wagtail.contrib.redirects.middleware import get_redirect as wt_get_redirect
from wagtail.contrib.redirects.models import Redirect
from wagtail.models import Locale, Page, PageViewRestriction, Site

from wagtail_ninja.schema import (
    BasePageDetailSchema,
    BasePageModelSchema,
    RedirectSchema,
)
from wagtail_ninja.typer import create_pages_schemas

from . import WagtailNinjaException
from ._django_ninja_patch import apply_django_ninja_operation_result_to_response_patch

apply_django_ninja_operation_result_to_response_patch()


class Http404Response(Schema):
    detail: str


def get_base_queryset(request: HttpRequest):
    queryset = Page.objects.all().live()

    # Exclude pages that the user doesn't have access to
    restricted_pages = [
        restriction.page
        for restriction in PageViewRestriction.objects.all().select_related("page")
        if not restriction.accept_request(request)
    ]
    for restricted_page in restricted_pages:
        queryset = queryset.not_descendant_of(restricted_page, inclusive=True)

    # Check if we have a specific site to look for
    if "site" in request.GET:
        # Optionally allow querying by port
        if ":" in request.GET["site"]:
            (hostname, port) = request.GET["site"].split(":", 1)
            query = {
                "hostname": hostname,
                "port": port,
            }
        else:
            query = {
                "hostname": request.GET["site"],
            }
        try:
            site = Site.objects.get(**query)
        except Site.MultipleObjectsReturned as err:
            raise Exception(
                "Your query returned multiple sites. "
                "Try adding a port number to your site filter."
            ) from err
    else:
        # Otherwise, find the site from the request
        site = Site.find_for_request(request)

    if site:
        base_queryset = queryset
        queryset = base_queryset.descendant_of(site.root_page, inclusive=True)

        # If internationalization is enabled, include pages from other language trees
        if getattr(settings, "WAGTAIL_I18N_ENABLED", False):
            for translation in site.root_page.get_translations():
                queryset |= base_queryset.descendant_of(translation, inclusive=True)
    else:
        # No sites configured
        queryset = queryset.none()

    return queryset


def list_pages(request: HttpRequest):
    qs = get_base_queryset(request)
    return qs


def get_page_wrapper_fn(all_page_schemas: dict[type[Page], type[ModelSchema]]):
    all_schemas = all_page_schemas.values()
    type WagtailPages = functools.reduce(operator.or_, all_schemas)

    def get_page(request: HttpRequest, page_id: int) -> WagtailPages:
        page = get_object_or_404(Page, id=page_id).specific

        for page_type, schema in all_page_schemas.items():
            if type(page) is page_type:
                return schema.from_orm(page, context={"request": request})

        return BasePageDetailSchema.from_orm(page, context={"request": request})

    return get_page


def find_page(request: HttpRequest, html_path: str, locale: str | None = None):
    site = Site.find_for_request(request)
    if not site:
        raise Http404("No site found")

    path_components = [component for component in html_path.split("/") if component]
    root_page = site.root_page

    if locale:
        try:
            locale = get_object_or_404(Locale, language_code=locale)
            root_page = Page.objects.get(
                locale=locale, translation_key=root_page.translation_key
            )
        except (Http404, Page.DoesNotExist):
            pass

    try:
        page, _, _ = root_page.specific.route(request, path_components)
    except Http404 as err:
        raise Http404("Page not found") from err

    if not get_base_queryset(request).order_by("id").filter(id=page.id).exists():
        raise Http404("Page not found")

    # TODO not a great solution
    return redirect(request.build_absolute_uri(f"../{page.id}/"))


def get_page_preview(request: HttpRequest, content_type, token):
    if find_spec("wagtail_headless_preview") is None:
        raise WagtailNinjaException(">>> wagtail_headless_preview not installed <<<")

    from wagtail_headless_preview.models import PagePreview  # noqa: PLC0415

    app_label, model = content_type.split(".")
    content_type = ContentType.objects.get(app_label=app_label, model=model)

    page_preview = PagePreview.objects.get(content_type=content_type, token=token)
    page = page_preview.as_page()
    if not page.pk:
        # fake primary key to stop API URL routing from complaining
        page.pk = 0

    return page


class WagtailNinjaPagesRouter(Router):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._autodetect()

    def _autodetect(self, **kwargs):
        all_page_schemas = create_pages_schemas()
        all_schemas = all_page_schemas.values()
        type WagtailPages = functools.reduce(operator.or_, all_schemas)

        self.add_api_operation(
            "/", ["GET"], list_pages, response=list[BasePageModelSchema]
        )
        self.add_api_operation(
            "/find/",
            ["GET"],
            find_page,
            response={301: None, 302: None, 404: Http404Response},
        )
        self.add_api_operation(
            "/preview/", ["GET"], get_page_preview, response=WagtailPages
        )
        self.add_api_operation(
            "/{page_id}/",
            ["GET"],
            get_page_wrapper_fn(all_page_schemas),
            response=WagtailPages,
        )


class WagtailNinjaRedirectsRouter(Router):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._autodetect()

    @staticmethod
    def list_redirects(request: HttpRequest):
        return Redirect.objects.all()

    @staticmethod
    def find_redirect(request: HttpRequest, html_path):
        _redirect = wt_get_redirect(request, html_path)
        if _redirect:
            return _redirect

        raise Http404("No redirect found")

    @staticmethod
    def get_redirect(request: HttpRequest, redirect_id: int):
        return get_object_or_404(Redirect, id=redirect_id)

    def _autodetect(self, **kwargs):
        self.add_api_operation(
            "/", ["GET"], self.list_redirects, response=list[RedirectSchema]
        )
        self.add_api_operation(
            "/find/",
            ["GET"],
            self.find_redirect,
            response=RedirectSchema,
        )
        self.add_api_operation(
            "/{redirect_id}/",
            ["GET"],
            self.get_redirect,
            response=RedirectSchema,
        )

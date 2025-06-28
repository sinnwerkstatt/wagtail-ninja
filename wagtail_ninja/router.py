import functools
import operator

from ninja import ModelSchema, Router, Schema

from django.conf import settings
from django.http import Http404, HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from wagtail.models import Locale, Page, PageViewRestriction, Site

from wagtail_ninja.schema import BasePageDetailSchema, BasePageSchema
from wagtail_ninja.typer import create_pages_schemas

from .django_ninja_patch import apply_django_ninja_operation_result_to_response_patch

apply_django_ninja_operation_result_to_response_patch()


class Http404Response(Schema):
    class Http404ResponseContent(Schema):
        code: int = 404
        message: str
        path: str

    error: Http404ResponseContent


class Http404Json(JsonResponse):
    status_code = 404

    def __init__(self, path, *args, **kwargs):
        kwargs["data"] = {
            "error": {
                "code": 404,
                "message": "Not Found",
                "path": path,
            }
        }

        super().__init__(*args, **kwargs)


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
                "Your query returned multiple sites. Try adding a port number to your site filter."
            ) from err
    else:
        # Otherwise, find the site from the request
        site = Site.find_for_request(request)

    if site:
        base_queryset = queryset
        queryset = base_queryset.descendant_of(site.root_page, inclusive=True)

        # If internationalisation is enabled, include pages from other language trees
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


def get_page_wrapper_fn(schemas: dict[type[Page], type[ModelSchema]]):
    type WagtailPages = functools.reduce(operator.or_, schemas.values())

    def get_page(request: HttpRequest, page_id: int) -> WagtailPages:
        page = get_object_or_404(Page, id=page_id).specific

        for page_type, schema in schemas.items():
            if isinstance(page, page_type):
                return schema.from_orm(page, context={"request": request})

        return BasePageDetailSchema.from_orm(page, context={"request": request})

    return get_page


def find_page(request: HttpRequest, html_path, locale=None):
    site = Site.find_for_request(request)
    if not site:
        return Http404Json(request.get_full_path())

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
    except Http404:
        return Http404Json(request.get_full_path())

    if not get_base_queryset(request).order_by("id").filter(id=page.id).exists():
        return Http404Json(request.get_full_path())

    # TODO not a great solution
    return redirect(request.build_absolute_uri(f"../{page.id}/"))


class WagtailNinjaPagesRouter(Router):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._autodetect()

    def _autodetect(self, **kwargs):
        all_page_schemas = create_pages_schemas()
        type WagtailPages = functools.reduce(operator.or_, all_page_schemas.values())

        self.add_api_operation("/", ["GET"], list_pages, response=list[BasePageSchema])
        self.add_api_operation(
            "/find/",
            ["GET"],
            find_page,
            response={301: None, 302: None, 404: Http404Response},
        )
        self.add_api_operation(
            "/{page_id}/",
            ["GET"],
            get_page_wrapper_fn(all_page_schemas),
            response=WagtailPages,
        )

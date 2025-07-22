

# wagtail-ninja: Seamless API Integration for Wagtail with Django Ninja

**`wagtail-ninja`** is an alpha-version package designed to effortlessly expose your Wagtail pages and redirects via a robust [Django Ninja](https://django-ninja.rest-framework.com/) API. Leverage the power of type hints and fast API development that Django Ninja provides, bringing a modern API experience to your Wagtail projects.



## Installation

`wagtail-ninja` is available on PyPI. You can install it using pip:

```bash
pip install wagtail-ninja
```

## Quick Usage

Integrate `wagtail-ninja` into your Wagtail project with just a few lines of code.

### 1. Define Your API Routers

Create an `api.py` file within your Django app to set up your Django Ninja API and include the `wagtail-ninja` routers:

```python
# some/path/api.py
from ninja import NinjaAPI
from wagtail_ninja.router import WagtailNinjaPagesRouter, WagtailNinjaRedirectsRouter

# Initialize your Django Ninja API - see https://django-ninja.dev/tutorial/ for more information
api = NinjaAPI()

# Add the Wagtail Pages and Redirects routers
api.add_router("/pages/", WagtailNinjaPagesRouter())
api.add_router("/redirects/", WagtailNinjaRedirectsRouter())
```

### 2\. Include API URLs

Link your new Ninja API to your project's `urls.py`:

```python
# your_project/urls.py
from django.urls import path

# Import your Ninja API instance
from some.path.api import api as ninja_api

urlpatterns = [
    # ... other Wagtail and Django paths
    
    # Expose your Wagtail Ninja API
    path("api/wagtail/v3/", ninja_api.urls),
]
```

## Features & Benefits

Once integrated, you'll immediately gain:

  * **Interactive API Documentation:** Access the OpenAPI Ninja UI at `http://localhost:8000/api/wagtail/v3/docs` (adjust port and path as per your configuration). This provides a user-friendly interface to explore your API endpoints.
  * **Programmatic Schema Access:** Retrieve your API's OpenAPI schema (for code generation, testing, etc.) from `http://localhost:8000/api/wagtail/v3/openapi.json`.
  * **Type Hinting Benefits:** Leverage Django Ninja's core strength for better code clarity and developer experience.



## Configuration

### StreamField Block Type Hinting (⚠ experimental)

`wagtail-ninja` includes an experimental feature to provide more specific type hints for StreamField blocks in your API schema.

To enable this, set the following in your Django settings:

```python
# settings.py
WAGTAIL_NINJA_TYPE_STREAMFIELDBLOCKS = True
```

If this setting is `False` (the default) or not set, StreamField values will be typed as `unknown` in the OpenAPI schema. Enabling it should work for the most part, but it will likely give you many errors because any custom `get_api_reprensentation` you have will likely clash with the then expected result type.


## Migrating from DRF-Specific Code

If you're transitioning from a `djangorestframework` (DRF) approach or have custom API field definitions, here are key considerations:

  * **DRF Serializers are Not Supported:** `wagtail-ninja` is built on Django Ninja, which uses Pydantic for serialization. Therefore, traditional DRF serializers (e.g., `APIField("myfield", serializer=MySerializer())`) are **not** compatible.
  * **Custom Field Resolution:** For fields requiring custom serialization logic, Wagtail-Ninja will look for a `resolve_<field_name>` method within your custom `MyPage(Page)`. This allows you to define how a specific field's value is processed before being returned by the API.

## Annotating `api_fields` for Type Hinting

When using `api_fields` on your Wagtail Page models to expose custom methods, you might encounter circular import issues when trying to type-hint the return values with Django Ninja `ModelSchema`s.

Consider this common scenario:

```python
# schema.py
from ninja import ModelSchema
from .models import MyPage, OtherPage

class OtherPageSchema(ModelSchema):
    class Meta:
        model = OtherPage

class MyPageSchema(ModelSchema):
    class Meta:
        model = MyPage
```

```python
# models.py
# This will cause a circular import if OtherPageSchema is imported at the top level
# from .schema import OtherPageSchema 

class MyPage(Page):
    api_fields = ['related_otherpage']

    # Attempting to type-hint directly leads to circular dependency
    # def related_otherpage(self) -> OtherPageSchema: 
    #    related = OtherPage.objects.first() # Example logic
    #    return OtherPageSchema.from_orm(related)
```

The current "best" solution to avoid circular dependencies while still providing accurate type hints for Django Ninja is to define a static method that returns the schema class, and then attach it to your custom field method:

```python
# models.py
from wagtail.models import Page
from django.db import models

class OtherPage(Page):
    # Your fields for OtherPage
    pass

class MyPage(Page):
    # Your fields for MyPage
    api_fields = ['related_otherpage']

    def related_otherpage(self):
        # Local import to avoid circular dependency at module load time
        from .schema import OtherPageSchema 
        related = OtherPage.objects.first() # Your logic to get the related object(s)
        return OtherPageSchema.from_orm(related)

    @staticmethod
    def type_fn():
        # This static method returns the schema class itself
        from .schema import OtherPageSchema
        return OtherPageSchema

    # Attach the type_fn to your method with a special attribute
    related_otherpage._wagtail_ninja_type_fn = type_fn
```

This pattern ensures that `wagtail-ninja` (and Django Ninja) can correctly infer the response schema for your custom `api_fields` without causing import errors.


## Contributing & Support

`wagtail-ninja` is currently in an alpha state. Your contributions, feedback, and bug reports are highly welcome to help shape its development\!


## AI
yes, some LLM helped with the README.

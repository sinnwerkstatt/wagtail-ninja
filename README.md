
# wagtail-ninja

alpha version.

## usage

in your wagtail project do something like this:
```python
# api.py
from ninja import NinjaAPI
from wagtail_ninja.router import WagtailNinjaPagesRouter

pages_router = WagtailNinjaPagesRouter()

api = NinjaAPI()

api.add_router("/pages/", pages_router)
```

and then in your urls:
```python
# urls.py
from some.path.api import api as ninja_api
urlpatterns = [
    ...
    path("api/wagtail/v3/", ninja_api.urls),
    ...
]
```

et voila.


you should be able to:

- find the OpenAPI Ninja UI under e.g. http://localhost:8000/api/wagtail/v3/docs
- get the schema (for further processing) from http://localhost:8000/api/wagtail/v3/openapi.json
- 
- 

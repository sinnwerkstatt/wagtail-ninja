from ninja import ModelSchema

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


## annotating api_fields

just like with the original wagtail api, you can put arbitrary functions in `api_fields` and they will be evaluated.
to annotate these, you will quickly run into circular dependency hell, i.e.:

```python
# schema.py
class MyPageSchema(ModelSchema):
    class Meta:
        model = MyPage

class OtherPageSchema(ModelSchema):
    class Meta:
        model = OtherPage

# models.py
class MyPage(Page):
    api_fields = ['something']
    
    def related_otherpage(self) -> OtherPageSchema:
        related = ...
        return  OtherPageSchema.from_orm(related)
        
```

The "best" solution right now is to solve it like so:

```python
    def related_otherpage(self):
        from .schema import OtherPageSchema
        related = ...
        return  OtherPageSchema.from_orm(related)

    @staticmethod
    def type_fn():
        from .schema import OtherPageSchema
        return OtherPageSchema
    related_otherpage._wagtail_ninja_type_fn = type_fn
```



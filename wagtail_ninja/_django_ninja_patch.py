import importlib
import logging
from typing import Any

from ninja.constants import NOT_SET
from ninja.errors import ConfigError
from ninja.operation import ResponseObject
from ninja.schema import Schema, pydantic_version

from django.http import HttpRequest, HttpResponse
from django.http.response import HttpResponseBase

logger = logging.getLogger(__name__)

LIBRARY_NAME = "ninja"
BUGGED_VERSION_MIN = "1.4.3"
BUGGED_VERSION_MAX = "1.4.5"


def apply_django_ninja_operation_result_to_response_patch():
    """
    Applies a patch to some_third_party_lib to fix a specific bug.
    """
    try:
        # Dynamically import the library
        lib_module = importlib.import_module(LIBRARY_NAME)

        # Check the version before patching
        lib_version = getattr(
            lib_module, "__version__", "0.0.0"
        )  # Get version, default to '0.0.0' if not found

        if BUGGED_VERSION_MIN <= lib_version <= BUGGED_VERSION_MAX:
            logger.warning(
                f"Applying patch for {LIBRARY_NAME} version {lib_version}. "
                # f"Please update to {LIBRARY_NAME}>={FIXED_VERSION_MIN} when available."
            )

            # --- THE ACTUAL MONKEY PATCHING LOGIC GOES HERE ---
            # Assume the bug is in some_third_party_lib.some_module.SomeClass.problematic_method
            from ninja.operation import Operation

            # original_method = Operation._result_to_response

            def patched_method(
                self, request: HttpRequest, result: Any, temporal_response: HttpResponse
            ) -> HttpResponseBase:
                """
                The protocol for results
                 - if HttpResponse - returns as is
                 - if tuple with 2 elements - means http_code + body
                 - otherwise it's a body
                """
                if isinstance(result, HttpResponseBase):
                    return result

                status: int = 200
                if len(self.response_models) == 1:
                    status = next(iter(self.response_models))

                if isinstance(result, tuple) and len(result) == 2:
                    status = result[0]
                    result = result[1]

                if status in self.response_models:
                    response_model = self.response_models[status]
                elif Ellipsis in self.response_models:
                    response_model = self.response_models[Ellipsis]
                else:
                    raise ConfigError(
                        f"Schema for status {status} is not set in response"
                        f" {self.response_models.keys()}"
                    )

                temporal_response.status_code = status

                if response_model is NOT_SET:
                    return self.api.create_response(
                        request, result, temporal_response=temporal_response
                    )

                if response_model is None:
                    # Empty response.
                    return temporal_response

                model_dump_kwargs: dict[str, Any] = dict(
                    by_alias=self.by_alias,
                    exclude_unset=self.exclude_unset,
                    exclude_defaults=self.exclude_defaults,
                    exclude_none=self.exclude_none,
                )
                if pydantic_version >= [2, 7]:
                    # pydantic added support for serialization context at 2.7
                    model_dump_kwargs.update(
                        context={"request": request, "response_status": status}
                    )

                if isinstance(result, Schema):
                    # if the result is already a Schema, just return it
                    return self.api.create_response(
                        request,
                        result.model_dump(**model_dump_kwargs),
                        temporal_response=temporal_response,
                    )

                resp_object = ResponseObject(result)
                # ^ we need object because getter_dict seems work only with model_validate
                validated_object = response_model.model_validate(
                    resp_object, context={"request": request, "response_status": status}
                )

                result = validated_object.model_dump(**model_dump_kwargs)["response"]
                return self.api.create_response(
                    request, result, temporal_response=temporal_response
                )

            # Replace the original method with our patched version
            Operation._result_to_response = patched_method
            # --- END OF MONKEY PATCHING LOGIC ---

        # elif lib_version >= FIXED_VERSION_MIN:
        #     logger.info(
        #         f"Skipping patch for {LIBRARY_NAME} (version {lib_version} is >= {FIXED_VERSION_MIN}). "
        #         "The bug is likely fixed."
        #     )

    except ImportError:
        logger.error(
            f"Could not import '{LIBRARY_NAME}'. Skipping patch application.",
            exc_info=True,
        )
    except AttributeError:
        logger.error(
            f"Could not find required attributes/methods in '{LIBRARY_NAME}' for patching. "
            "Has the library structure changed?",
            exc_info=True,
        )
    except Exception as e:
        logger.error(
            f"An unexpected error occurred while applying patch for '{LIBRARY_NAME}': {e}",
            exc_info=True,
        )

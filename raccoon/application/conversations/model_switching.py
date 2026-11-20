"""Build model request parameters from a configured profile."""
from raccoon.config.models import ModelCatalog, ModelProfile
from raccoon.config.settings import AppConfig
from raccoon.integrations.providers.factory import create_provider_for_model
from raccoon.runtime.model_provider import ChatProvider
from raccoon.runtime.model_types import MainRequestOptions


ROUTING_MODEL_REF = "deepseek/deepseek-flash"


def model_options(catalog: ModelCatalog) -> list[dict]:
    return [
        {'ref': model.ref, 'label': model.label, 'effort': model.request.reasoning_effort,
         'supported_reasoning_efforts': list(model.supported_reasoning_efforts),
         'input_capabilities': {'images': model.vision, 'pdf': model.pdf_input}}
        for model in catalog.models.values()
    ]


def model_request_options(profile: ModelProfile | None) -> MainRequestOptions:
    if profile is None:
        return MainRequestOptions()
    request = profile.request
    extra_body = dict(request.extra_body)
    if request.reasoning_effort is not None:
        extra_body["reasoning_effort"] = request.reasoning_effort
    return MainRequestOptions(
        temperature=request.temperature,
        max_tokens=request.max_tokens,
        extra_body=extra_body,
    )


def create_routing_model(
    config: AppConfig, profile: ModelProfile | None
) -> tuple[ChatProvider | None, MainRequestOptions | None]:
    if profile is None or not config.get_env(profile.provider.api_key_env):
        return None, None
    return create_provider_for_model(config, profile), model_request_options(profile)



"""Build model request parameters from a configured profile."""
from raccoon.config.models import ModelCatalog, ModelCatalogError, ModelProfile, build_model_catalog
from raccoon.config.settings import AppConfig
from raccoon.integrations.providers.factory import create_provider_for_model
from raccoon.runtime.model_provider import AuxiliaryModels, ChatProvider
from raccoon.runtime.model_types import MainRequestOptions


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


def _auxiliary_provider(config: AppConfig, role: str) -> ChatProvider | None:
    prefix = "RACCOON_" + role.upper()
    key_name = prefix + "_API_KEY"
    url_name = prefix + "_BASE_URL"
    model_name = prefix + "_MODEL"
    fields = {name: (config.get_env(name) or "").strip() for name in (key_name, url_name, model_name)}
    if not any(fields.values()):
        return None
    missing = [name for name, value in fields.items() if not value]
    if missing:
        raise ModelCatalogError("辅助模型配置缺少 " + "、".join(missing))
    model_ref = role + "/" + fields[model_name]
    catalog = build_model_catalog({
        "providers": {role: {"base_url": fields[url_name], "api_key_env": key_name}},
        "models": {model_ref: {}},
    })
    return create_provider_for_model(config, catalog.models[model_ref])


def create_auxiliary_models(config: AppConfig) -> AuxiliaryModels:
    return AuxiliaryModels(
        message_routing=_auxiliary_provider(config, "message_routing"),
        permission_review=_auxiliary_provider(config, "permission_review"),
        context_summary=_auxiliary_provider(config, "context_summary"),
    )



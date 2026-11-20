"""全局模型目录的数据结构与校验；不读取密钥、不合并项目配置。"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit
import math
import re

from raccoon.modules.context.budget_defaults import DEFAULT_CONTEXT_WINDOW, DEFAULT_OUTPUT_RESERVE


class ModelCatalogError(ValueError):
    """模型目录配置不合法。"""


class ProviderConfigError(ValueError):
    """模型调用所需的配置缺失。"""


@dataclass(frozen=True, slots=True)
class ModelRequestOptions:
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning_effort: str | None = None
    extra_body: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderProfile:
    id: str
    base_url: str
    api_key_env: str
    label: str = ""


@dataclass(frozen=True, slots=True)
class ModelProfile:
    ref: str
    provider_id: str
    model_id: str
    label: str
    provider: ProviderProfile
    request: ModelRequestOptions
    context_window: int = DEFAULT_CONTEXT_WINDOW
    vision: bool = False
    pdf_input: bool = False
    parallel_tool_calls: bool = True
    supported_reasoning_efforts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ModelCatalog:
    default_ref: str | None
    models: dict[str, ModelProfile]


def _string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ModelCatalogError(f"{field_name} 必须是非空字符串")
    return value.strip()


def _boolean(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ModelCatalogError(f"{field_name} 必须是布尔值")
    return value


def _positive_integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ModelCatalogError(f"{field_name} 必须是大于 0 的整数")
    return value


def _request_options(config: dict, model_ref: str, supported_efforts: tuple[str, ...]) -> ModelRequestOptions:
    if not isinstance(config, dict):
        raise ModelCatalogError(f"模型 {model_ref}.request 必须是表")
    unknown = set(config) - {"temperature", "max_tokens", "reasoning_effort", "extra_body"}
    if unknown:
        raise ModelCatalogError(f"模型 {model_ref}.request 包含未知字段：{', '.join(sorted(unknown))}")
    temperature = config.get("temperature")
    if temperature is not None:
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not math.isfinite(temperature):
            raise ModelCatalogError(f"模型 {model_ref}.temperature 必须是有限数字")
    max_tokens = config.get("max_tokens")
    if max_tokens is not None:
        max_tokens = _positive_integer(max_tokens, f"模型 {model_ref}.max_tokens")
    reasoning_effort = config.get("reasoning_effort")
    if reasoning_effort is not None and reasoning_effort not in supported_efforts:
        raise ModelCatalogError(f"模型 {model_ref} 的默认推理强度必须属于 supported_reasoning_efforts")
    extra_body = config.get("extra_body", {})
    if not isinstance(extra_body, dict):
        raise ModelCatalogError(f"模型 {model_ref}.extra_body 必须是表")
    reserved = {"model", "messages", "input", "tools", "tool_choice", "stream", "temperature",
                "max_tokens", "max_output_tokens", "max_completion_tokens", "reasoning_effort", "reasoning"}
    forbidden = reserved.intersection(extra_body)
    if forbidden:
        raise ModelCatalogError(f"模型 {model_ref}.extra_body 不得覆盖：{', '.join(sorted(forbidden))}")
    return ModelRequestOptions(
        temperature=temperature, max_tokens=max_tokens,
        reasoning_effort=reasoning_effort, extra_body=deepcopy(extra_body),
    )


def build_model_catalog(config: dict | None = None) -> ModelCatalog:
    """按服务商、模型、默认选择的顺序解析一份全局模型配置。"""
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise ModelCatalogError("模型配置必须是表")
    unknown = set(config) - {"providers", "models", "default_model"}
    if unknown:
        raise ModelCatalogError(f"模型配置包含未知字段：{', '.join(sorted(unknown))}")
    provider_configs = config.get("providers", {})
    model_configs = config.get("models", {})
    if not isinstance(provider_configs, dict) or not isinstance(model_configs, dict):
        raise ModelCatalogError("providers 和 models 必须是表")

    providers = {}
    for provider_id, provider_config in provider_configs.items():
        if not isinstance(provider_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", provider_id):
            raise ModelCatalogError("服务商标识只能包含字母、数字、点、下划线和短横线")
        if not isinstance(provider_config, dict):
            raise ModelCatalogError(f"服务商 {provider_id} 必须是表")
        unknown = set(provider_config) - {"label", "base_url", "api_key_env"}
        if unknown:
            raise ModelCatalogError(f"服务商 {provider_id} 包含未知字段：{', '.join(sorted(unknown))}")
        base_url = _string(provider_config.get("base_url", "https://api.openai.com/v1"), "base_url")
        address = urlsplit(base_url)
        if address.scheme not in {"http", "https"} or not address.netloc or address.username or address.password or address.query or address.fragment:
            raise ModelCatalogError("base_url 必须是不含凭据、查询参数的 HTTP 地址")
        api_key_env = _string(provider_config.get("api_key_env"), "api_key_env")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", api_key_env):
            raise ModelCatalogError("api_key_env 必须是有效的环境变量名称")
        providers[provider_id] = ProviderProfile(
            id=provider_id, base_url=base_url, api_key_env=api_key_env,
            label=_string(provider_config.get("label", provider_id), "服务商名称"),
        )

    models = {}
    for model_ref, model_config in model_configs.items():
        if not isinstance(model_ref, str) or "/" not in model_ref:
            raise ModelCatalogError(f"模型引用无效：{model_ref}")
        provider_id, model_id = model_ref.split("/", 1)
        if not model_id or any(character.isspace() for character in model_id):
            raise ModelCatalogError(f"模型引用无效：{model_ref}")
        if provider_id not in providers:
            raise ModelCatalogError(f"模型 {model_ref} 指向的服务商不存在")
        if not isinstance(model_config, dict):
            raise ModelCatalogError(f"模型 {model_ref} 必须是表")
        unknown = set(model_config) - {"label", "context_window", "vision", "pdf_input", "parallel_tool_calls", "supported_reasoning_efforts", "request"}
        if unknown:
            raise ModelCatalogError(f"模型 {model_ref} 包含未知字段：{', '.join(sorted(unknown))}")
        efforts = model_config.get("supported_reasoning_efforts", [])
        if not isinstance(efforts, list) or any(not isinstance(value, str) or not value.strip() or value != value.strip() for value in efforts):
            raise ModelCatalogError(f"模型 {model_ref} 的推理强度选项必须是非空字符串列表")
        if len(efforts) != len(set(efforts)):
            raise ModelCatalogError(f"模型 {model_ref} 的推理强度选项不能重复")
        request = _request_options(model_config.get("request", {}), model_ref, tuple(efforts))
        context_window = _positive_integer(model_config.get("context_window", DEFAULT_CONTEXT_WINDOW), "context_window")
        output_reserve = request.max_tokens
        if output_reserve is None:
            output_reserve = DEFAULT_OUTPUT_RESERVE
        if output_reserve >= int(context_window * 0.95):
            raise ModelCatalogError(f"模型 {model_ref}.request.max_tokens 必须小于 context_window 的 95% 可用窗口")
        models[model_ref] = ModelProfile(
            ref=model_ref, provider_id=provider_id, model_id=model_id,
            label=_string(model_config.get("label", model_id), "模型名称"),
            provider=providers[provider_id], request=request, context_window=context_window,
            vision=_boolean(model_config.get("vision", False), "vision"),
            pdf_input=_boolean(model_config.get("pdf_input", False), "pdf_input"),
            parallel_tool_calls=_boolean(model_config.get("parallel_tool_calls", True), "parallel_tool_calls"),
            supported_reasoning_efforts=tuple(efforts),
        )

    default_ref = config.get("default_model")
    if default_ref is not None:
        default_ref = _string(default_ref, "default_model")
        if default_ref not in models:
            raise ModelCatalogError(f"默认模型未配置：{default_ref}")
    return ModelCatalog(default_ref=default_ref, models=models)

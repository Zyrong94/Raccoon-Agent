"""根据模型配置创建 provider。"""

from __future__ import annotations

from dataclasses import replace

from raccoon.config import AppConfig
from raccoon.config.models import ModelProfile, ProviderConfigError
from raccoon.runtime.model_provider import ChatProvider
from raccoon.integrations.providers.openai_responses import OpenAIResponsesProvider, RESPONSES_CAPABILITIES


# 读取配置和密钥，创建 Responses provider。
def create_provider_for_model(config: AppConfig, profile: ModelProfile) -> ChatProvider:
    """检查模型配置、读取密钥并创建 Responses provider；不发送模型请求。"""
    provider_config = profile.provider
    environment_variable_name = provider_config.api_key_env
    api_key = config.get_env(environment_variable_name)
    if not api_key:
        raise ProviderConfigError(f"缺少环境变量：{environment_variable_name}")

    # 能力对象不可变；创建替换副本，避免修改所有模型共用的默认能力。
    capabilities = replace(
        RESPONSES_CAPABILITIES,
        supports_parallel_tool_calls=profile.parallel_tool_calls,
        supports_vision=profile.vision,
        supports_pdf_input=profile.pdf_input,
    )

    return OpenAIResponsesProvider(
        name=provider_config.id,
        model=profile.model_id,
        api_key=api_key,
        base_url=provider_config.base_url,
        capabilities=capabilities,
    )

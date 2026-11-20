"""Provider 根据模型目录条目读取环境变量并构造调用对象。"""
from dataclasses import replace

import pytest

from raccoon.config import AppConfig
from raccoon.config.models import ModelProfile, ModelRequestOptions, ProviderProfile
from raccoon.integrations.providers.factory import ProviderConfigError, create_provider_for_model
from raccoon.integrations.providers.openai_responses import RESPONSES_CAPABILITIES


@pytest.fixture
def model_profile():
    return ModelProfile(
        ref="test/model", provider_id="test", model_id="model", label="Test",
        provider=ProviderProfile(id="test", base_url="https://example.test/v1", api_key_env="TEST_API_KEY"),
        request=ModelRequestOptions(),
    )


def test_factory_reads_the_named_environment_variable(model_profile):
    provider = create_provider_for_model(AppConfig(env={"TEST_API_KEY": "offline"}), model_profile)
    assert provider.name == "test"
    assert provider.model == "model"
    assert provider.base_url == "https://example.test/v1"


def test_missing_named_environment_variable_does_not_fall_back(model_profile):
    with pytest.raises(ProviderConfigError, match="TEST_API_KEY"):
        create_provider_for_model(AppConfig(env={"OPENAI_API_KEY": "wrong"}), model_profile)


def test_explicit_false_capabilities_do_not_modify_shared_defaults(model_profile):
    profile = replace(model_profile, vision=False, pdf_input=False, parallel_tool_calls=False)
    provider = create_provider_for_model(AppConfig(env={"TEST_API_KEY": "offline"}), profile)
    assert provider.capabilities.supports_parallel_tool_calls is False
    assert provider.capabilities.supports_vision is False
    assert provider.capabilities.supports_pdf_input is False
    assert RESPONSES_CAPABILITIES.supports_parallel_tool_calls is True
    assert RESPONSES_CAPABILITIES.supports_vision is True
    assert RESPONSES_CAPABILITIES.supports_pdf_input is True

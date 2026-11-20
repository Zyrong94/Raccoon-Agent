"""应用配置与独立模型文件的基础校验。"""
from copy import deepcopy

import pytest

from raccoon.config import AppConfig, load_config
from raccoon.config.models import ModelCatalogError, build_model_catalog
from raccoon.config.settings import default_global_config_path


def model_config():
    return {"providers": {"custom": {"api_key_env": "CUSTOM_API_KEY"}},
            "models": {"custom/model": {"context_window": 128000}},
            "default_model": "custom/model"}


def test_no_model_file_has_no_implicit_model(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert load_config(project_root=tmp_path, env={}).model_catalog().models == {}


def test_default_global_config_path_respects_xdg_config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert default_global_config_path() == tmp_path / "raccoon" / "config.toml"


def test_mcp_configuration_still_merges_global_and_project_servers():
    config = AppConfig(env={}, global_config={"mcp": {"a": {"url": "global"}, "b": {"url": "shared"}}},
                       project_config={"mcp": {"a": {"url": "project"}}})
    assert config.mcp_config() == {"a": {"url": "project"}, "b": {"url": "shared"}}


@pytest.mark.parametrize("value", [0, -1, True, "128000", 1.5])
def test_invalid_context_window_is_rejected(value):
    config = model_config()
    config["models"]["custom/model"]["context_window"] = value
    with pytest.raises(ModelCatalogError, match="context_window"):
        build_model_catalog(config)


def test_output_budget_cannot_exhaust_context_window():
    config = model_config()
    config["models"]["custom/model"]["request"] = {"max_tokens": 128000}
    with pytest.raises(ModelCatalogError, match="95%"):
        build_model_catalog(config)


@pytest.mark.parametrize("field,value", [("max_tokens", True), ("temperature", True), ("temperature", float("nan")), ("extra_body", {"model": "override"})])
def test_request_parameters_are_validated(field, value):
    config = model_config()
    config["models"]["custom/model"]["request"] = {field: value}
    with pytest.raises(ModelCatalogError):
        build_model_catalog(config)


def test_models_require_a_declared_provider():
    config = model_config()
    config["providers"] = {}
    with pytest.raises(ModelCatalogError, match="服务商不存在"):
        build_model_catalog(config)


def test_defaults_and_requested_effort_are_not_written_back_to_input():
    config = model_config()
    config["models"]["custom/model"].update(supported_reasoning_efforts=["custom"])
    config["models"]["custom/model"]["request"] = {"reasoning_effort": "custom"}
    before = deepcopy(config)
    catalog = build_model_catalog(config)
    assert catalog.models["custom/model"].request.reasoning_effort == "custom"
    assert catalog.models["custom/model"].request.extra_body == {}
    assert config == before

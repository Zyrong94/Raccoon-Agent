"""读写全局唯一的 models.toml；文件中只保存配置和环境变量名称。"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import tomllib

import portalocker
import tomlkit

from raccoon.config.models import build_model_catalog


class ModelConfigStore:
    def __init__(self, path: Path | None = None):
        if path is None:
            from raccoon.config.settings import default_global_config_path
            path = default_global_config_path().with_name("models.toml")
        self.path = Path(path)

    def read(self) -> dict:
        """Read TOML; consumers build the catalog once to validate and use it."""
        if not self.path.exists():
            return {}
        with self.path.open("rb") as source:
            config = tomllib.load(source)
        return config

    def _write(self, config: dict) -> None:
        build_model_catalog(config)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 在同一目录原子替换，避免中途退出留下半份配置。
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, delete=False) as temporary:
            temporary_path = Path(temporary.name)
            try:
                temporary.write(tomlkit.dumps(config))
                temporary.flush()
                os.fsync(temporary.fileno())
            except BaseException:
                temporary.close()
                temporary_path.unlink(missing_ok=True)
                raise
        try:
            os.replace(temporary_path, self.path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def records(self, config: dict | None = None) -> list[dict]:
        if config is None:
            config = self.read()
        catalog = build_model_catalog(config)
        records = []
        for profile in catalog.models.values():
            provider = profile.provider
            fields = deepcopy(config["models"][profile.ref])
            fields.update(
                provider_id=provider.id, provider_label=provider.label,
                base_url=provider.base_url, api_key_env=provider.api_key_env,
                model_id=profile.model_id, default=profile.ref == catalog.default_ref,
            )
            revision = hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()
            records.append({"id": profile.ref, "revision": revision, "config": fields, "source": "managed", "read_only": False})
        return records

    def validate(self, fields: dict) -> tuple[str, dict, dict]:
        if not isinstance(fields, dict):
            raise ValueError("模型配置必须是对象")
        allowed = {"provider_id", "provider_label", "base_url", "api_key_env", "model_id", "default",
                   "label", "context_window", "vision", "pdf_input", "parallel_tool_calls", "supported_reasoning_efforts", "request"}
        if set(fields) - allowed:
            raise ValueError("模型配置包含未知字段；密钥只支持环境变量名称")
        provider_id = fields.get("provider_id")
        model_id = fields.get("model_id")
        if not isinstance(provider_id, str) or not isinstance(model_id, str) or not model_id:
            raise ValueError("请填写服务商标识和模型 ID")
        if "default" in fields and not isinstance(fields["default"], bool):
            raise ValueError("default 必须是布尔值")
        model_ref = provider_id + "/" + model_id
        provider = {}
        for source, target in (("provider_label", "label"), ("base_url", "base_url"), ("api_key_env", "api_key_env")):
            if source in fields:
                provider[target] = fields[source]
        model = {}
        for name in ("label", "context_window", "vision", "pdf_input", "parallel_tool_calls", "supported_reasoning_efforts", "request"):
            if name in fields:
                model[name] = deepcopy(fields[name])
        build_model_catalog({"providers": {provider_id: provider}, "models": {model_ref: model}})
        return model_ref, provider, model

    def save(self, fields: dict, *, identifier=None, revision=None) -> dict:
        from raccoon.config.management import ConfigurationConflict
        model_ref, provider, model = self.validate(fields)
        if identifier is not None and identifier != model_ref:
            raise ValueError("已有模型标识不可修改")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(str(self.path) + ".lock", timeout=5):
            config = self.read()
            records = {record["id"]: record for record in self.records(config)}
            if identifier is not None:
                if identifier not in records or records[identifier]["revision"] != revision:
                    raise ConfigurationConflict("模型配置已修改或不存在，请刷新后重试")
            elif model_ref in records:
                raise ConfigurationConflict("模型标识已存在，请编辑已有模型")
            config.setdefault("providers", {})[fields["provider_id"]] = provider
            config.setdefault("models", {})[model_ref] = model
            if fields.get("default"):
                config["default_model"] = model_ref
            elif config.get("default_model") == model_ref:
                config.pop("default_model")
            self._write(config)
            return next(record for record in self.records(config) if record["id"] == model_ref)

    def delete(self, identifier: str, revision: str) -> None:
        from raccoon.config.management import ConfigurationConflict
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(str(self.path) + ".lock", timeout=5):
            config = self.read()
            records = {record["id"]: record for record in self.records(config)}
            if identifier not in records or records[identifier]["revision"] != revision:
                raise ConfigurationConflict("模型配置已修改或不存在，请刷新后重试")
            del config["models"][identifier]
            provider_id = identifier.split("/", 1)[0]
            if not any(reference.split("/", 1)[0] == provider_id for reference in config["models"]):
                config["providers"].pop(provider_id)
            if config.get("default_model") == identifier:
                config.pop("default_model")
            self._write(config)

    def import_config(self, incoming: dict) -> list[dict]:
        from raccoon.config.management import ConfigurationConflict
        catalog = build_model_catalog(incoming)
        if not catalog.models:
            raise ValueError("文件中没有可导入的模型")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(str(self.path) + ".lock", timeout=5):
            config = self.read()
            providers = config.setdefault("providers", {})
            models = config.setdefault("models", {})
            if set(models).intersection(catalog.models):
                raise ConfigurationConflict("导入包含已有模型，请在界面编辑已有模型")
            for provider_id, provider in incoming["providers"].items():
                if provider_id in providers and providers[provider_id] != provider:
                    raise ConfigurationConflict("同名服务商连接配置不同，请先修改服务商标识")
                providers[provider_id] = deepcopy(provider)
            models.update(deepcopy(incoming["models"]))
            if catalog.default_ref is not None:
                config["default_model"] = catalog.default_ref
            self._write(config)
            return [record for record in self.records(config) if record["id"] in catalog.models]

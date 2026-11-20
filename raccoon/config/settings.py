"""应用配置加载。

配置层是 Raccoon 的统一入口：Web、QQ 和 provider factory 都应该通过这里拿到
运行配置，而不是在各处直接读取文件或环境变量。
"""

from __future__ import annotations

import os
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import tomllib

from dotenv import load_dotenv

from raccoon.config.models import ModelCatalog, build_model_catalog

PROJECT_CONFIG_NAME = "raccoon.toml"


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Raccoon 的应用级配置。

    `env` 保留 RACCOON_* 和厂商环境变量；`project_config` / `global_config`
    承载 TOML 配置。provider factory 只依赖这个对象的方法，不需要知道配置来自哪里。
    """

    env: dict[str, str]
    project_config: dict[str, Any] | None = None
    global_config: dict[str, Any] | None = None
    project_config_path: Path | None = None
    global_config_path: Path | None = None
    model_config: dict[str, Any] | None = None

    def get_env(self, name: str, default: str | None = None) -> str | None:
        """读取配置中的环境变量值。

        Provider secrets are resolved by an explicit Catalog profile.
        """

        return self.env.get(name, default)

    def get_config_value(self, name: str, *, default: str | None = None) -> str | None:
        """读取顶层配置字段，项目配置覆盖全局配置。"""

        for config in (self.project_config, self.global_config):
            value = _string_value(config, name)
            if value is not None:
                return value
        return default

    def compaction_settings(self):
        from raccoon.modules.context.compaction import CompactionSettings
        values = {}
        for config in (self.global_config, self.project_config):
            section = (config or {}).get("compaction", {})
            if not isinstance(section, dict):
                raise ValueError("[compaction] must be a table")
            values.update(section)
        return CompactionSettings(**values)

    def mcp_config(self) -> dict[str, Any]:
        """返回按服务器名合并的原始 MCP 配置，项目配置完整覆盖同名全局配置。

        MCP 只来自 TOML 配置文件；个人库不再承载 MCP，避免同一 server 存在两份定义。
        """

        merged: dict[str, Any] = {}
        for config in (self.global_config, self.project_config):
            if not config or "mcp" not in config:
                continue
            raw_mcp = config["mcp"]
            if not isinstance(raw_mcp, dict):
                raise ValueError("[mcp] 配置必须是表")
            for name, server_config in raw_mcp.items():
                merged[name] = deepcopy(server_config)
        return merged

    def model_catalog(self) -> ModelCatalog:
        """模型只来自全局 models.toml 的快照，不读取项目配置。"""
        return build_model_catalog(self.model_config)


def load_config(
    *,
    project_root: Path | str | None = None,
    env: dict[str, str] | None = None,
) -> AppConfig:
    """从配置文件、`.env` 和系统环境变量加载应用配置。

    Provider 和模型选择只来自标准 Model Catalog；旧的单 provider 环境变量不再
    参与选择。
    """

    load_dotenv()
    env_snapshot = dict(os.environ if env is None else env)
    root = Path(project_root or os.getcwd()).resolve()
    global_path = default_global_config_path()
    project_path = root / PROJECT_CONFIG_NAME
    global_config = _read_toml_file(global_path)
    project_config = _read_toml_file(project_path)
    from raccoon.config.model_store import ModelConfigStore
    model_config = ModelConfigStore().read()

    return AppConfig(
        env=env_snapshot,
        project_config=project_config,
        global_config=global_config,
        project_config_path=project_path if project_config is not None else None,
        global_config_path=global_path if global_config is not None else None,
        model_config=model_config,
    )


def default_global_config_path() -> Path:
    """返回当前平台的全局 Raccoon 配置路径。"""

    config_home = os.getenv("XDG_CONFIG_HOME")
    if config_home:
        return Path(config_home) / "raccoon" / "config.toml"
    return Path.home() / ".config" / "raccoon" / "config.toml"


def _read_toml_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    return data


def _string_value(config: dict[str, Any] | None, name: str) -> str | None:
    if not config:
        return None
    value = config.get(name)
    if value is None:
        return None
    return str(value)

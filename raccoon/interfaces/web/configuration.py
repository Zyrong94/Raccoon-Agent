from raccoon.interfaces.web import conversation_actions as actions
"""Authenticated personal-library API; explicit probes are separate from writes."""

import asyncio
from time import perf_counter
from typing import Literal
from types import SimpleNamespace

from fastapi import HTTPException, Request
from raccoon.interfaces.web.project_requests import ProjectWriteBody, submit_once
from pydantic import BaseModel, Field

from raccoon.config.management import ManagedConfigStore, ConfigurationConflict, DOMAINS, public_record
from raccoon.config.settings import AppConfig, load_config
from raccoon.config.management_sources import source_records
from pathlib import Path


class SaveBody(ProjectWriteBody):
    id: str | None = None
    revision: str | None = None
    config: dict
    scope: Literal["global", "project", "shared"] = "global"
    content: str = Field(default="", max_length=1000000)
    model_config = {"extra": "forbid"}


class ImportBody(ProjectWriteBody):
    scope: Literal["global", "project"] = "global"
    path: str | None = Field(default=None, min_length=1, max_length=4096)
    text: str | None = Field(default=None, max_length=2000000)
    format: Literal["toml", "json", "yaml"] = "toml"
    content: str = Field(default="", max_length=1000000)


class CatalogBody(BaseModel):
    project_id: str = Field(min_length=1, max_length=64)
    session_id: str = Field(min_length=1, max_length=200)
    domains: list[Literal["models", "mcp", "skills", "subagents"]] | None = None


class RevisionBody(ProjectWriteBody):
    scope: Literal["global", "project", "shared"] = "global"
    revision: str


class TestBody(RevisionBody):
    id: str
    prompt: str = Field(default="Reply with OK.", max_length=2000)


def configuration_records(store, domain, sources):
    store._domain(domain)
    if domain == "models":
        return store.models.records()
    sources = sources[domain]
    if domain == "skills":
        return sources
    combined = {store._identity(domain, record["id"]): record for record in sources}
    # MCP lives only in TOML; personal-library records and tombstones are ignored.
    deleted = set() if domain == "mcp" else store.deleted_ids(domain)
    if domain != "mcp":
        combined.update({store._identity(domain, record["id"]): record for record in store.records(domain)})
    return [record for key, record in combined.items() if key not in deleted]


def require_record(store, domain, identifier, revision, scope, project_path, data_root):
    sources = {} if domain == "models" else source_records(
        store, project_path=project_path, data_root=data_root, domains=(domain,))
    record = next((record for record in configuration_records(store, domain, sources)
                   if record["id"] == identifier and (domain != "skills" or record["scope"] == scope)), None)
    if record is None or record["revision"] != revision:
        raise ConfigurationConflict("配置已修改或不存在，请刷新后重试")
    return record


def configuration_snapshot(store, *, app_config, project_path, data_root, domains=None, owner=None):
    # Reading every domain scans skill and subagent packages; callers can ask for the tabs they show.
    selected = tuple(domain for domain in DOMAINS if domains is None or domain in domains)
    sources = source_records(store, app_config=app_config, project_path=project_path,
                             data_root=data_root, domains=selected)
    result = {"root": str(store.root), "models_path": str(store.models.path), "warnings": sources.get("warnings", [])}
    for domain in DOMAINS:
        result[domain] = [public_record(record, domain) for record in configuration_records(store, domain, sources)] if domain in selected else []
    tools = []
    if owner is not None:
        runner = owner.runtime
        tools = runner.tools_provider() if runner.tools_provider else runner.tools
    result["options"] = {"tools": [{"name": tool.name, "description": tool.definition.description} for tool in tools],
                         "skill_names": sorted({record["id"] for record in result["skills"]}),
                         "provider_types": ["openai-responses"]}
    for record in result["mcp"]:
        project_mcp = (app_config.project_config or {}).get("mcp", {}) if app_config else {}
        global_mcp = (app_config.global_config or {}).get("mcp", {}) if app_config else {}
        if record["id"] in project_mcp:
            record["origin"], path = "project", getattr(app_config, "project_config_path", None)
        elif record["id"] in global_mcp:
            record["origin"], path = "global", getattr(app_config, "global_config_path", None)
        else:
            record["origin"], path = "unknown", None
        record["origin_path"] = str(path) if path else None
    status = getattr(owner.runtime, "mcp_status", None) if owner is not None else None
    result["mcp_status"] = status() if callable(status) else {"servers": []}
    return result


async def test_configuration(workbench, store, domain, record, prompt):
    if domain == "subagents":
        from raccoon.config.testing import test_managed_subagent
        return await test_managed_subagent(workbench, record["id"], prompt)

    from raccoon.integrations.providers.factory import create_provider_for_model
    from raccoon.runtime.model_types import ChatMessage, ChatRequest

    started = perf_counter()
    app_config = AppConfig(env=dict(workbench.runtime.app_config.env), model_config=store.models.read())
    profile = app_config.model_catalog().models[record["id"]]
    provider = create_provider_for_model(app_config, profile)
    request = ChatRequest(
        messages=[ChatMessage(
            role="user",
            text="Reply with OK.",
        )],
        reasoning_effort=profile.reasoning_effort,
        tool_choice="none",
    )
    response = await asyncio.wait_for(provider.acomplete(request), timeout=20)
    if getattr(response, "tool_calls", ()) or getattr(response, "finish_reason", None) in {"error", "content_filter"}:
        raise ValueError("模型探测未返回正常响应")
    # A connectivity probe never echoes model text, which may contain provider metadata.
    elapsed = round((perf_counter() - started) * 1000)
    return {"ok": True, "message": f"模型最小请求成功，耗时 {elapsed} ms。", "elapsed_ms": elapsed}


def install_configuration_routes(app, workbench):
    store = ManagedConfigStore()
    app.state.configuration_store = store

    def call(operation):
        try:
            return operation()
        except ConfigurationConflict as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except OSError:
            raise HTTPException(400, "配置文件无法读写，请检查目录权限与磁盘空间") from None

    def writable_project(body, domain):
        workbench.resolve_session(body.project_id, session_id=body.session_id)
        workbench.require_active_project(body.project_id)
        call(lambda: store._domain(domain))
        if domain == "mcp":
            raise HTTPException(403, "MCP 配置由 raccoon.toml 或全局 config.toml 管理；配置页只读展示。")
        return Path(workbench.workspace.projects[body.project_id]['path'])

    async def subagent_reference_check(body, project_path):
        from raccoon.config.testing import validate_subagent_references
        config = SimpleNamespace(tools=body.config.get("tools"), skills=body.config.get("skills"))
        runtime = None
        if config.tools:
            owner = await workbench.prepare_session(body.project_id, body.session_id)
            runtime = owner.runtime
        return lambda: validate_subagent_references(project_path, config, runtime)

    def apply_saved_configuration(domain, project_path, scope):
        """Update only the resource explicitly edited through this API."""
        from raccoon.modules.skills.discovery import discover_all_skills
        from raccoon.modules.tools.load_skill import create_load_skill_tool
        from raccoon.config.subagents_config import discover_subagent_configs

        runtimes = {id(view.runtime): view.runtime for view in workbench.displays.values()}
        startup = workbench.configuration_runtime
        if startup is not None:
            runtimes[id(startup)] = startup
        try:
            model_config = store.models.read() if domain == "models" else None
            for runtime in runtimes.values():
                if domain == "skills" and scope == "project" and runtime.project_path.resolve() != project_path.resolve():
                    continue
                session = runtime.session
                if domain == "models":
                    runtime.update_model_catalog(model_config)
                elif domain == "skills":
                    catalog = discover_all_skills(runtime.project_path).resolved()
                    session.available_skill_catalog = catalog
                    session.skill_catalog = catalog
                    registry = session.tool_registry
                    if registry.get("load_skill") is not None:
                        registry.unregister("load_skill")
                        registry.register(create_load_skill_tool(catalog, session.store, session.data))
                elif domain == "subagents":
                    session.subagent_configs = discover_subagent_configs(data_root=session.data_root)
            if domain == "models":
                workbench.update_model_catalog(model_config)
                for view in workbench.displays.values():
                    view.publish({"kind": "snapshot", "state": view.snapshot()})
                if workbench.selected_session not in workbench.displays:
                    workbench.events.publish({"kind": "snapshot", "state": workbench.selected_snapshot()})
        except (ValueError, OSError):
            raise HTTPException(400, "配置已保存，但更新运行配置失败；请检查配置后重新保存或重启后端。") from None

    async def catalog_snapshot(project_id, session_id, domains):
        workspace = workbench
        workspace.resolve_session(project_id, session_id=session_id)
        selected = tuple(domain for domain in DOMAINS if domains is None or domain in domains)
        owner = workspace.displays.get((project_id, session_id))
        # Subagent editing needs the actual tool catalog, including connected MCP tools.
        if "subagents" in selected:
            owner = await workspace.prepare_session(project_id, session_id)
        project_path = Path(workspace.workspace.projects[project_id]['path'])
        data_root = workspace.workspace.catalog(project_id).root
        if owner is not None:
            app_config = owner.runtime.app_config
        elif workspace.workspace.projects[project_id].get('projectless'):
            app_config = workspace.app_config
        else:
            app_config = call(lambda: load_config(project_root=project_path))
        return call(lambda: configuration_snapshot(
            store, app_config=app_config, project_path=project_path, data_root=data_root,
            domains=selected, owner=owner))

    @app.post("/api/configuration")
    async def catalog_with_domains(body: CatalogBody):
        return await catalog_snapshot(body.project_id, body.session_id, body.domains)

    @app.post("/api/configuration/{domain}/save")
    async def save(request: Request, domain: str, body: SaveBody):
        async def execute():
            project_path = writable_project(body, domain)
            check = await subagent_reference_check(body, project_path) if domain == "subagents" else None
            record = call(lambda: store.save(domain, body.config, body.content, identifier=body.id, revision=body.revision,
                                             source=lambda: require_record(store, domain, body.id, body.revision, body.scope,
                                                                           project_path, workbench.workspace.catalog(body.project_id).root),
                                             scope=body.scope, project_path=project_path, check_subagent_references=check))
            apply_saved_configuration(domain, project_path, body.scope)
            return public_record(record, domain)

        return await submit_once(request, body, execute)

    @app.post("/api/configuration/subagents/validate")
    async def validate_subagent(request: Request, body: SaveBody):
        async def execute():
            project_path = writable_project(body, "subagents")
            call(lambda: store.validate("subagents", body.config, body.content))
            check = await subagent_reference_check(body, project_path)
            call(check)
            return {"ok": True, "message": "配置校验通过；未执行模型或工具。"}

        return await submit_once(request, body, execute)

    @app.post("/api/configuration/{domain}/import")
    async def import_configuration(request: Request, domain: str, body: ImportBody):
        async def execute():
            project_path = writable_project(body, domain)
            if (body.path is None) == (body.text is None):
                raise HTTPException(400, "请选择文件路径或粘贴配置其中一种导入方式")
            records = call(lambda: store.import_path(domain, body.path, scope=body.scope, project_path=project_path) if body.path is not None else store.import_text(domain, body.text, format=body.format, content=body.content))
            apply_saved_configuration(domain, project_path, body.scope if body.path is not None else "global")
            return {"records": [public_record(record, domain) for record in records]}

        return await submit_once(request, body, execute)

    @app.post("/api/configuration/{domain}/test")
    async def test(request: Request, domain: Literal["models", "subagents"], body: TestBody):
        async def execute():
            project_path = writable_project(body, domain)
            record = call(lambda: require_record(store, domain, body.id, body.revision, body.scope,
                                                 project_path, workbench.workspace.catalog(body.project_id).root))
            owner = await workbench.prepare_session(body.project_id, body.session_id)
            actions.require_session_change(owner)
            try:
                return await asyncio.wait_for(test_configuration(owner, store, domain, record, body.prompt), timeout=30)
            except TimeoutError:
                return {"ok": False, "message": "测试超时，请检查服务地址或网络。"}
            except Exception as exc:
                from raccoon.integrations.providers.factory import ProviderConfigError
                from raccoon.integrations.providers.errors import ProviderError, ProviderErrorKind
                if isinstance(exc, ProviderConfigError):
                    return {"ok": False, "message": str(exc)}
                if isinstance(exc, ProviderError):
                    messages = {ProviderErrorKind.AUTH_ERROR: "模型授权失败，请检查 API Key。", ProviderErrorKind.RATE_LIMIT: "模型服务限流，请稍后重试。",
                                ProviderErrorKind.TIMEOUT: "模型请求超时。", ProviderErrorKind.NETWORK_ERROR: "模型连接失败，请检查服务地址和网络。"}
                    return {"ok": False, "message": messages.get(exc.kind, "模型服务请求失败，请检查提供商配置与服务状态。")}
                if isinstance(exc, ValueError) and str(exc).startswith("子代理引用不存在的"):
                    return {"ok": False, "message": str(exc)}
                return {"ok": False, "message": "测试未成功；请检查连接、环境变量、权限或服务状态。秘密与服务原始错误不会显示。"}

        return await submit_once(request, body, execute)

    @app.delete("/api/configuration/{domain}/{identifier:path}")
    async def delete(request: Request, domain: str, identifier: str, body: RevisionBody):
        async def execute():
            project_path = writable_project(body, domain)
            call(lambda: store.delete(domain, identifier, body.revision, source=lambda: require_record(
                store, domain, identifier, body.revision, body.scope, project_path, workbench.workspace.catalog(body.project_id).root),
                scope=body.scope, project_path=project_path))
            apply_saved_configuration(domain, project_path, body.scope)
            return {"ok": True, "warning": "已删除所选位置的技能；若有其他来源的同名技能，将按优先级生效。" if domain == "skills" else "已从个人库删除；后续任务不再使用此配置。"}

        return await submit_once(request, body, execute)

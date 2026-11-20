"""Explicit, bounded prompt trials without tool execution or session writes."""

import asyncio

from raccoon.config.subagents_config import load_subagent_config
from raccoon.runtime.model_types import ChatMessage, ChatRequest
from raccoon.modules.skills.discovery import discover_all_skills


def validate_subagent_references(project_path, config, runtime=None):
    missing_tools = []
    missing_skills = []
    if config.tools:
        available_tools = runtime.tools_provider() if runtime.tools_provider else (runtime.tools or [])
        tool_names = {tool.name for tool in available_tools}
        tool_names.update(runtime.session.tool_registry.names())
        missing_tools = sorted(set(config.tools) - tool_names)
    if config.skills:
        skill_names = {skill.name for skill in discover_all_skills(project_path).resolved().skills}
        missing_skills = sorted(set(config.skills) - skill_names)
    if missing_tools or missing_skills:
        details = []
        if missing_tools:
            details.append("工具：" + "、".join(missing_tools))
        if missing_skills:
            details.append("技能：" + "、".join(missing_skills))
        raise ValueError("子代理引用不存在的" + "；".join(details))


async def test_managed_subagent(workbench, identifier, prompt):
    runtime = workbench.runtime
    runner = runtime
    config = load_subagent_config(identifier, data_root=runtime.catalog.root)
    validate_subagent_references(runtime.project_path, config, runtime)
    options = runner.request_options.as_chat_request_kwargs()
    options["max_tokens"] = min(options.get("max_tokens") or 512, 512)
    request = ChatRequest(
        messages=[
            ChatMessage(role="system", content=config.system_prompt),
            ChatMessage(role="user", content=prompt.strip() or "Reply with OK."),
        ],
        tools=[], tool_choice="none", **options,
    )
    response = await asyncio.wait_for(runner.provider.acomplete(request), timeout=min(config.max_turn_seconds, 20))
    if response.finish_reason not in {None, "stop", "length", "tool_calls"}:
        return {"ok": False, "message": "提示词试运行未完成；模型返回失败、中断或内容限制。未执行工具。", "output": ""}
    if response.tool_calls:
        return {"ok": False, "message": "提示词试运行请求了工具；未执行工具，未验证完整工具工作流。", "output": response.content[:8000]}
    if not response.content.strip():
        return {"ok": False, "message": "提示词试运行未返回文本；可能已达到输出上限。未执行工具。", "output": ""}
    if response.finish_reason == "length":
        return {"ok": True, "message": "提示词试运行达到输出上限；未执行工具，未验证完整工具工作流。", "output": response.content[:8000]}
    return {"ok": True, "message": "提示词试运行完成；未执行工具，未验证完整工具工作流。", "output": response.content[:8000]}

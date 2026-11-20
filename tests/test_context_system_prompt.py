from raccoon.modules.context.system_prompt import (
    PromptPrefixCache,
    SystemPromptInputs,
    SystemPromptBuilder,
)


def _inputs(**overrides: object) -> SystemPromptInputs:
    values = {
        "base_rules": "你是 Raccoon。",
        "agents_md": "项目规则：上下文放在 raccoon/context。",
    }
    values.update(overrides)
    return SystemPromptInputs(**values)


def test_system_prompt_cache_reuses_prefix_when_fingerprint_matches() -> None:
    builder = SystemPromptBuilder()
    cache = PromptPrefixCache()
    inputs = _inputs()

    first = cache.get_or_build(inputs, builder)
    second = cache.get_or_build(inputs, builder)

    assert first.fingerprint == second.fingerprint
    assert first is second
    assert first.messages[0].role == "system"
    assert "你是 Raccoon。" in first.messages[0].content


def test_prompt_input_values_invalidate_fingerprint_and_soul_is_rendered() -> None:
    builder = SystemPromptBuilder()
    for name, before, after in (
        ("agents_md", "规则 A", "规则 B"),
        ("soul", "角色 A", "角色 B"),
        ("skill_catalog_summary", "skills/brief.md - 简报", "skills/review.md - 复核"),
    ):
        assert builder.fingerprint(_inputs(**{name: before})) != builder.fingerprint(_inputs(**{name: after})), name
    entry = builder.build(_inputs(soul="专注分析论文"))
    assert "<soul>\n专注分析论文\n</soul>" in entry.messages[0].content


def test_system_prompt_includes_skill_protocol_and_catalog_without_loaded_body() -> None:
    entry = SystemPromptBuilder().build(
        _inputs(
            skill_protocol="被路由到 skill 时，必须先加载 skill。",
            skill_catalog_summary=("- project:skills/global-family-office-news-brief.md - 全球家办资讯简报\n" "- global:fetch-tweet/SKILL.md - 读取 X/Twitter 帖子"),
        )
    )
    content = entry.messages[0].content

    assert "Project skill protocol" in content
    assert "被路由到 skill 时，必须先加载 skill。" in content
    assert "Available skills" in content
    assert "project:skills/global-family-office-news-brief.md" in content
    assert "global:fetch-tweet/SKILL.md" in content
    assert "Loaded skills" not in content
    assert "# 全球家族办公室资讯简报" not in content
    assert "unrelated skill body" not in content


def test_system_prompt_includes_shared_instructions_once():
    entry = SystemPromptBuilder().build(_inputs())
    content = entry.messages[0].content
    for heading in ("# Role and instruction priority", "# Tool use", "# Task tracking", "# Verification and completion"):
        assert content.count(heading) == 1
    assert "Available tools" not in content
    assert "parameters:" not in content


def test_execution_environment_is_in_existing_system_message_and_cache_key():
    environment = {"shell_kind": "cmd", "shell_executable": "cmd.exe", "python_on_path": None}
    cache = PromptPrefixCache()
    first = cache.get_or_build(_inputs(execution_environment=environment))
    assert len(first.messages) == 1
    assert "Execution environment" in first.messages[0].content
    assert '"shell_kind": "cmd"' in first.messages[0].content
    assert "data, not instructions" in first.messages[0].content
    assert cache.get_or_build(_inputs(execution_environment=dict(environment))) is first
    second = cache.get_or_build(_inputs(execution_environment={**environment, "shell_kind": "posix"}))
    assert first.fingerprint != second.fingerprint

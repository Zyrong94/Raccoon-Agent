"""Fake MCP transport and model catalog for offline tests."""

from raccoon.config.settings import AppConfig


class FakeMcpManager:
    def __init__(self, tools=(), statuses=()) -> None:
        self.tools_value = tools
        self.statuses_value = statuses
        self.connect_calls = 0
        self.close_calls = 0

    def connect_all(self) -> None:
        self.connect_calls += 1

    def connect_all_in_background(self) -> None:
        self.connect_calls += 1

    def tools(self):
        return self.tools_value

    def statuses(self):
        return self.statuses_value

    def doctor(self, name: str):
        return next((status for status in self.statuses_value if status.name == name), None)

    def call_tool(self, server: str, tool: str, arguments: dict[str, object]):
        return {"content": [{"type": "text", "text": "ok"}]}

    def close(self) -> None:
        self.close_calls += 1


def _catalog_config(*, default_model: str | None = "yuren/main") -> AppConfig:
    project = {
        "providers": {
            "yuren": {
                "base_url": "https://example.test/v1",
                "api_key_env": "YUREN_KEY",
            },
            "mimo": {
                "base_url": "https://mimo.example/v1",
                "api_key_env": "MIMO_KEY",
            },
        },
        "models": {
            "yuren/main": {"context_window": 128_000, "request": {"temperature": 0.2}},
            "mimo/pro": {"context_window": 200_000},
        },
    }
    if default_model is not None:
        project["default_model"] = default_model
    return AppConfig(
        env={"YUREN_KEY": "test-key", "MIMO_KEY": "mimo-key"},
        model_config=project,
    )

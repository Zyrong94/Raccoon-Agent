from raccoon.modules.agent.telemetry import AgentTurnTelemetry
from raccoon.modules.tools.types import ToolResult
from raccoon.runtime.model_types import ToolCall


def test_turn_telemetry_aggregates_repeated_calls_mutation_and_validation() -> None:
    telemetry = AgentTurnTelemetry()
    telemetry.begin()
    telemetry.observe_provider_call()
    telemetry.observe_provider_call()

    telemetry.observe_tool_result(
        ToolCall(id="write", name="write", arguments={"path": "README.md", "content": "ok"}),
        ToolResult(name="write", ok=True, content="written"),
        elapsed_seconds=2.5,
    )
    failed_validation = ToolResult(
        name="shell",
        ok=False,
        content="1 failed",
        error="exit 1",
    )
    validation_call = ToolCall(
        id="test-1",
        name="shell",
        arguments={"command": "pytest -q"},
    )
    telemetry.observe_tool_result(validation_call, failed_validation, elapsed_seconds=4.0)
    telemetry.observe_tool_result(validation_call, failed_validation, elapsed_seconds=5.0)

    payload = telemetry.snapshot(
        status="limited",
        stop_reason="turn_timeout",
        elapsed_seconds=9.25,
        finalize=True,
    )

    assert payload is not None
    assert payload["provider_calls"] == 2
    assert payload["tool_calls"] == 3
    assert payload["tool_failures"] == 2
    assert payload["repeated_tool_calls"] == 1
    assert payload["max_identical_tool_calls"] == 2
    assert payload["first_mutation_sequence"] == 1
    assert payload["first_mutation_elapsed_seconds"] == 2.5
    assert payload["validation_count"] == 2
    assert payload["latest_validation_ok"] is False
    assert telemetry.active is False

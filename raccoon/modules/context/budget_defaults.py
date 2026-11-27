"""Provider-neutral context budget defaults shared by config and runtime."""

DEFAULT_CONTEXT_WINDOW = 1_050_000
# Local headroom for reasoning and output; this is not an API output limit.
DEFAULT_OUTPUT_RESERVE = 32_768

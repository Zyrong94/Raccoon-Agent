import httpx
import pytest
from openai import APIConnectionError, APIStatusError, APITimeoutError

from raccoon.integrations.providers.errors import (
    ProviderError,
    ProviderErrorKind,
    classify_provider_exception,
    classify_response_error,
)


@pytest.mark.parametrize("status_code, expected", [
    (400, ProviderErrorKind.API_ERROR),
    (401, ProviderErrorKind.AUTH_ERROR),
    (403, ProviderErrorKind.AUTH_ERROR),
    (408, ProviderErrorKind.TIMEOUT),
    (429, ProviderErrorKind.RATE_LIMIT),
    (500, ProviderErrorKind.SERVER_ERROR),
    (504, ProviderErrorKind.SERVER_ERROR),
])
def test_sdk_status_error_uses_http_status(status_code, expected):
    response = httpx.Response(status_code, request=httpx.Request("POST", "https://example.test/responses"))
    error = APIStatusError("Output limit is 500 tokens", response=response, body=None)
    assert classify_provider_exception(error) == expected


@pytest.mark.parametrize("code, expected", [
    ("context_length_exceeded", ProviderErrorKind.PROMPT_TOO_LONG),
    ("max_prompt_tokens_exceeded", ProviderErrorKind.PROMPT_TOO_LONG),
    ("rate_limit_exceeded", ProviderErrorKind.RATE_LIMIT),
    ("server_error", ProviderErrorKind.SERVER_ERROR),
    ("internal_error", ProviderErrorKind.SERVER_ERROR),
    ("unknown_code", ProviderErrorKind.API_ERROR),
    (None, ProviderErrorKind.API_ERROR),
])
def test_structured_error_code_is_shared_by_sdk_and_failed_response(code, expected):
    response = httpx.Response(400, request=httpx.Request("POST", "https://example.test/responses"))
    error = APIStatusError("Request failed", response=response, body={"code": code})
    assert classify_provider_exception(error) == expected
    assert classify_response_error(code) == expected


def test_sdk_timeout_is_distinguished_from_connection_error():
    request = httpx.Request("POST", "https://example.test/responses")
    assert classify_provider_exception(APITimeoutError(request)) == ProviderErrorKind.TIMEOUT
    assert classify_provider_exception(APIConnectionError(request=request)) == ProviderErrorKind.NETWORK_ERROR


@pytest.mark.parametrize("message", ["500 tokens", "429 rate limit", "connection timed out", "cancel request"])
def test_unknown_exception_is_not_classified_by_message(message):
    assert classify_provider_exception(RuntimeError(message)) == ProviderErrorKind.UNKNOWN


def test_provider_error_exposes_retry_policy_flags() -> None:
    assert ProviderError(ProviderErrorKind.PROMPT_TOO_LONG, "too long").retryable is False
    assert ProviderError(ProviderErrorKind.PROMPT_TOO_LONG, "too long").requires_compaction is True
    assert ProviderError(ProviderErrorKind.SERVER_ERROR, "server down").retryable is True
    assert ProviderError(ProviderErrorKind.API_ERROR, "bad request").retryable is False
    assert ProviderError(ProviderErrorKind.UNKNOWN, "unknown").retryable is False
    assert ProviderError(ProviderErrorKind.AUTH_ERROR, "bad key").retryable is False
    assert ProviderError(ProviderErrorKind.CONFIG_ERROR, "bad config").retryable is False
    assert ProviderError(ProviderErrorKind.UNSUPPORTED, "not supported").retryable is False
    assert ProviderError(ProviderErrorKind.USER_ABORT, "cancelled").retryable is False

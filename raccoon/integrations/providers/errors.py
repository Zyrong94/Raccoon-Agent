"""将官方 OpenAI SDK 异常和 Responses 错误码转换为项目错误分类。"""

from openai import APIConnectionError, APIStatusError, APITimeoutError

from raccoon.runtime.model_errors import ProviderError, ProviderErrorKind


# 根据 SDK 异常类型和状态码判断错误类别。
def classify_provider_exception(error: Exception) -> ProviderErrorKind:
    # 超时异常也是连接异常，必须先判断超时。
    if isinstance(error, APITimeoutError):
        return ProviderErrorKind.TIMEOUT
    if isinstance(error, APIConnectionError):
        return ProviderErrorKind.NETWORK_ERROR
    if isinstance(error, APIStatusError):
        error_kind = classify_response_error(error.code)
        if error_kind != ProviderErrorKind.API_ERROR:
            return error_kind

        status_code = error.status_code
        if status_code in {401, 403}:
            return ProviderErrorKind.AUTH_ERROR
        if status_code == 408:
            return ProviderErrorKind.TIMEOUT
        if status_code == 429:
            return ProviderErrorKind.RATE_LIMIT
        if 500 <= status_code <= 599:
            return ProviderErrorKind.SERVER_ERROR
        return ProviderErrorKind.API_ERROR
    return ProviderErrorKind.UNKNOWN


# 将 Responses 错误码转换为项目的错误类别。
def classify_response_error(code: str | None) -> ProviderErrorKind:
    if code in {"context_length_exceeded", "max_prompt_tokens_exceeded"}:
        return ProviderErrorKind.PROMPT_TOO_LONG
    if code == "rate_limit_exceeded":
        return ProviderErrorKind.RATE_LIMIT
    if code in {"server_error", "internal_error"}:
        return ProviderErrorKind.SERVER_ERROR
    return ProviderErrorKind.API_ERROR

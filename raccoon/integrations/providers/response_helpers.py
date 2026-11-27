"""完整模型响应的用量转换"""

from raccoon.runtime.model_types import TokenUsage


# 整理 token 用量，缺少总数时按输入输出相加。
def token_usage(
    input_tokens: int | None,
    output_tokens: int | None,
    total_tokens: int | None = None,
) -> TokenUsage | None:
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = int(input_tokens) + int(output_tokens)
    if input_tokens is None and output_tokens is None and total_tokens is None:
        return None
    return TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=total_tokens)

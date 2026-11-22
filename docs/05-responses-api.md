# Responses API

跟踪一次非流式模型请求，从内部请求对象到 API 参数，再从响应中读出文本和工具调用。

## 源码入口

- [请求与响应](../raccoon/runtime/model_types.py)：`ChatRequest`、`ChatResponse` 和 `ToolCall` 定义主循环与适配器交换的数据。
- [模型适配器](../raccoon/integrations/providers/openai_responses.py)：`OpenAIResponsesProvider.acomplete()` 调用模型。`_build_response_params()` 和 `_parse_response()` 负责两端的数据转换。

[返回模块指南](../README.md#模块指南)

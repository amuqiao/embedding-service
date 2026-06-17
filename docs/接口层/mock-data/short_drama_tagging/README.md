# 短剧打标 HTTP Mock 数据

本目录保存短剧打标流程中 HTTP 交互的本地 mock 数据和格式样例。Mock 文件只用于联调、smoke 测试和人工检查；真实运行时字段校验仍以代码和接口文档为准。

上线后将 `ENABLE_MOCK_INTERFACES=false`，并把 RS 对接切到 HTTP：

```env
ENABLE_MOCK_INTERFACES=false
SHORT_DRAMA_RS_SCHEMA_SOURCE=http
SHORT_DRAMA_RS_RESULT_SINK=http
```

| HTTP 交互 | 文件 |
| --- | --- |
| CPP -> AI 创建 Job | `cpp_create_tagging_job_request.json` |
| AI -> RS 获取标签体系请求 | `rs_tag_schema_request.zh.json` |
| AI -> RS 获取标签体系响应 | `tag_schema_snapshot.zh.json`、`tag_schema_snapshot.en.json` |
| AI -> 模型服务剧情概览响应 | `model_story_overview_response.json` |
| AI -> 模型服务候选打标响应 | `model_candidate_tagging_response.json` |
| AI -> 模型服务最终打标响应 | `model_finalize_response.json` |
| AI -> CPP 成功 callback 请求 | `cpp_callback_request.succeeded.json` |
| CPP callback 成功响应 | `cpp_callback_response.success.json` |
| AI -> RS 写打标结果请求 | `rs_ai_tag_results_request.compat.json` |
| RS 写打标结果成功响应 | `rs_write_result_response.success.json` |

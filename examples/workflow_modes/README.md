# Workflow Mode Examples

本目录提供 6 种内置 `example_workflow` 模式的真实 API 调用示例：`chain`、`group`、`chord`、`map`、`starmap` 和 `chunks`。

先启动本地服务：

```bash
./scripts/dev.sh start
```

运行全部模式：

```bash
uv run examples/workflow_modes/run.py
```

只运行一种模式：

```bash
uv run examples/workflow_modes/run.py --mode chain
```

使用非默认 API 地址：

```bash
uv run examples/workflow_modes/run.py --api-url http://127.0.0.1:8100 --mode chunks
```

脚本会读取根目录 `.env` 中的 `SERVICE_API_KEY`、`DISABLE_HTTP_AUTH_HEADER`、`DISABLE_CALLER_ID_HEADER` 和 `SERVICE_API_PREFIX`，提交 Job 后轮询到终态并打印每个模式的 root job id。

默认 API 地址来自运行时 `API_URL`，或 `.env` / 运行时环境中的 `API_HOST` / `API_PORT`，最后才使用 `http://127.0.0.1:8100`。

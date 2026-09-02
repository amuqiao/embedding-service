# Business Extension Examples

本目录存放业务或供应商相关示例工具，不属于 `scripts/` 模板核心入口。

这些脚本不会被 `./scripts/verify.sh check` 默认执行。使用前先完成本地依赖安装：

```bash
./scripts/dev.sh bootstrap
```

## Aliyun OSS Connectivity

```bash
uv run python examples/business/check_aliyun_oss.py --env-file .env.dev
```

用于验证 Aliyun OSS `PUT`、`GET`、`HEAD` 和删除流程。

## Mock OpenAI Server

```bash
uv run python examples/business/mock_openai_server.py 18200
```

用于业务项目恢复 mock 模型冒烟验证时作为本地 OpenAI 兼容服务。

## Default Tag Schema Fetch

```bash
uv run python examples/business/fetch_default_tag_schema.py
```

用于从业务测试接口拉取默认 tag schema；模板核心不依赖该接口。

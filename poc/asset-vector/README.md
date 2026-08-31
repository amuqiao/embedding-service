# Asset Vector POC

这个目录用于验证以图搜图业务链路，不是正式项目实现。

从资源上传到生成报告的完整复现步骤见：[resource-to-report-runbook.md](resource-to-report-runbook.md)。

POC 验证的链路：

```text
本地素材目录 / manifest
  -> POC OSS adapter
  -> app/object_storage
  -> Aliyun OSS public_url
  -> DashScope multimodal embedding
  -> pgvector 临时表
  -> search-image / search-text / search-resource
  -> CLI 结果或 HTML 报告
```

## 边界

本 POC 会做：

- 上传本地图片到 OSS。
- 调用 `tongyi-embedding-vision-flash` 生成 768 维 `asset` 主检索向量，匹配 `company_intl` 账号当前可用能力。
- 创建 `poc_asset_vectors` 临时表。
- 支持图片搜图、文字搜图、库内资源搜相似图。
- 支持 `exists`、`ids` 和 `batch-delete`，用于验证对账与删除闭环。
- 输出 `resource_id / group_id / score / public_url`，可生成 HTML 报告。

本 POC 不做：

- 不新增 FastAPI route。
- 不新增正式 Job type。
- 不新增 Alembic migration。
- 不接 billing ledger / callback / operation registry。
- 不实现正式 `exists` / `ids` 对账接口。
- 不替代后端资源服务的权限、分类、标签、上下架和收藏过滤。
- 不接入 rerank；`company_intl` 当前不支持 `qwen3-vl-rerank`。

## 前置条件

只跑已入库资源查询和批量 resource 报告时，`.env` 至少需要：

```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:25440/embedding_service
```

调用 DashScope 的命令还需要：

```env
DASHSCOPE_API_KEY=
```

会写 OSS 的命令还需要：

```env

OSS_BUCKET=
OSS_REGION=
OSS_ACCESS_KEY_ID=
OSS_ACCESS_KEY_SECRET=
OSS_PROJECT_ROOT=
OSS_PUBLIC_ENDPOINT=
```

可选：

```env
POC_DASHSCOPE_BASE_URL=https://dashscope-intl.aliyuncs.com/api/v1
POC_ASSET_VECTOR_IMAGE_MAX_BYTES=10485760
```

调用 DashScope 的命令包括 `index-dir`、`index-manifest`、`search-image`、`search-text`、`generate-image-reports` 和 `generate-text-reports`。如果使用百炼 workspace 专属域名，把 `POC_DASHSCOPE_BASE_URL` 配成对应的 native `api/v1` 地址。不要使用 `compatible-mode/v1`，多模态向量需要 DashScope 原生接口。

会写 OSS 的命令包括 `upload_assets_to_oss.py --confirm-upload`、`index-dir`、`search-image --query-image`，以及 manifest 里只有 `local_path` 没有 `public_url` 时的 `index-manifest`。`search-resource` 和 `generate-resource-reports` 只读取本地 DB，不会使用 DashScope key 或 base URL。批量报告的完整复现命令见 [resource-to-report-runbook.md](resource-to-report-runbook.md)。

PostgreSQL 需要来自 `pgvector/pgvector:0.8.6-pg16` 或已经安装 `vector` extension 的实例。

## 快速开始

启动本地 dev recipe：

```bash
./scripts/run.sh up dev
```

`run.sh up dev` 会按仓库标准 recipe 启动 `compose-deps`、执行 migration，并启动宿主机 API / worker。POC 实际只依赖其中的 PostgreSQL/pgvector。

检查环境：

```bash
uv run python poc/asset-vector/asset_vector_poc.py check-env
```

初始化 POC 表：

```bash
uv run python poc/asset-vector/asset_vector_poc.py init-db
```

索引本地素材目录：

```bash
uv run python poc/asset-vector/asset_vector_poc.py index-dir .data/物件 --limit 20 --confirm-remote
```

上传 `.data/assets` 图片资源到 OSS/CDN，并生成可复用 JSONL manifest：

```bash
uv run python poc/asset-vector/upload_assets_to_oss.py --dry-run
uv run python poc/asset-vector/upload_assets_to_oss.py --confirm-upload
```

默认输出：

```text
poc/asset-vector/reports/manifests/assets-oss-manifest.jsonl
```

这个 JSONL 保留本地相对目录、图片名称、OSS object key、公网 URL、`sha256` 和 `content_type`，并且可以直接作为 `index-manifest` 输入：

```bash
uv run python poc/asset-vector/asset_vector_poc.py index-manifest poc/asset-vector/reports/manifests/assets-oss-manifest.jsonl --confirm-remote
```

用库内资源搜相似图：

```bash
uv run python poc/asset-vector/asset_vector_poc.py search-resource '物件/hwic_champagne' --top-k 10 --html-report poc/asset-vector/reports/html/resource/search-resource-hwic-champagne.html
```

用本地图片搜图：

```bash
uv run python poc/asset-vector/asset_vector_poc.py search-image --query-image .data/assets/物件/hwic_champagne.png --top-k 10 --html-report poc/asset-vector/reports/html/image/search-image-hwic-champagne.html --confirm-remote
```

用文字搜图：

```bash
uv run python poc/asset-vector/asset_vector_poc.py search-text "champagne bottle" --top-k 10 --html-report poc/asset-vector/reports/html/text/search-text-champagne.html --confirm-remote
```

批量生成库内资源搜相似图报告：

```bash
uv run python poc/asset-vector/asset_vector_poc.py generate-resource-reports --limit 20 --top-k 10
```

批量生成 image / text 报告：

```bash
uv run python poc/asset-vector/asset_vector_poc.py generate-image-reports --manifest poc/asset-vector/reports/manifests/assets-oss-manifest.jsonl --limit 3 --top-k 10 --confirm-remote
uv run python poc/asset-vector/asset_vector_poc.py generate-text-reports --text "champagne bottle" --text "golden dice" --top-k 10 --confirm-remote
```

打开报告总入口：

```text
poc/asset-vector/reports/html/index.html
```

每次通过 `--html-report poc/asset-vector/reports/html/<query-mode>/<name>.html` 生成单个搜索报告后，脚本会自动刷新同查询方式目录的 `index.html`。如果目录位于 `reports/html/resource`、`reports/html/image` 或 `reports/html/text` 下，还会刷新 `reports/html/index.html` 总入口。

`reports/html/index.html` 是总入口，下面按查询方式进入 `resource/index.html`、`image/index.html`、`text/index.html`。每个查询方式目录允许保留多份报告，并由自己的 `index.html` 汇总。

查看已索引资源：

```bash
uv run python poc/asset-vector/asset_vector_poc.py list-assets
```

检查一批资源是否已建向量：

```bash
uv run python poc/asset-vector/asset_vector_poc.py exists --resource-id '物件/hwic_champagne' --resource-id not_exists --json
```

导出 POC 向量库里的资源 ID：

```bash
uv run python poc/asset-vector/asset_vector_poc.py ids --limit 100 --json
```

删除一批资源向量投影：

```bash
uv run python poc/asset-vector/asset_vector_poc.py batch-delete --resource-id '物件/hwic_champagne' --confirm-delete --json
```

结束 POC 后停止本地 dev recipe：

```bash
./scripts/run.sh down dev
```

## manifest 格式

`index-manifest` 支持 JSONL，每行一个资源：

```json
{"resource_id":"物件/hwic_champagne","group_id":"物件","local_path":".data/assets/物件/hwic_champagne.png","text_payload":"champagne bottle"}
```

也可以直接传已有公网 URL：

```json
{"resource_id":"物件/hwic_champagne","group_id":"物件","public_url":"https://cdn.example.com/assets/物件/hwic_champagne.png","text_payload":"champagne bottle"}
```

规则：

- `resource_id` 必填。
- `group_id` 可选。
- `local_path`、`public_url`、`text_payload` 至少有一个。
- 有 `local_path` 但没有 `public_url` 时，默认上传到 OSS。
- 同时有 `public_url` 和 `text_payload` 时，生成 image+text 融合输入的 `asset` 主向量。

`upload_assets_to_oss.py` 生成的 JSONL 是 `index-manifest` 的兼容超集。每行会额外包含以下对齐字段：

```json
{
  "resource_id": "物件/hwic_champagne",
  "group_id": "物件",
  "public_url": "https://cdn.example.com/aicg/dev_root/cms_poster_title/asset-vector-poc/assets/物件/hwic_champagne.png",
  "local_path": ".data/assets/物件/hwic_champagne.png",
  "relative_path": "物件/hwic_champagne.png",
  "relative_dir": "物件",
  "file_name": "hwic_champagne.png",
  "content_type": "image/png",
  "oss_key": "aicg/dev_root/cms_poster_title/asset-vector-poc/assets/物件/hwic_champagne.png",
  "sha256": "..."
}
```

`.data/assets` 里包含图片、BGM、音效等不同资源类型。当前向量 POC 只索引图片，因此上传脚本默认只上传图片，`.mp3` 和无扩展名文件会显示在 skipped 里，不会写入给 `index-manifest` 使用的 JSONL。内容损坏或伪装成图片扩展名的文件也会进入 skipped。

`index-dir`、`index-manifest`、`search-image` 和 `search-text` 都会调用 DashScope；其中 `index-dir` 和本地图片 `search-image` 还会上传 OSS，因此必须显式传 `--confirm-remote`。

`batch-delete` 只删除 POC 表中的向量投影，不删除 OSS 对象，也不修改后端资源事实源；执行时必须显式传 `--confirm-delete`。

## 评估重点

跑完以后重点看：

- `image -> image` 的 Top 结果是否符合肉眼相似。
- `resource_id -> image` 是否能找出同类素材。
- `text -> image` 是否能召回预期素材。
- `score` 分布是否支持业务阈值，不要直接把 `0.90` 当成百分比。
- `text_payload` 加入后是提升还是干扰相似度。

POC 结论稳定后，再把 adapter、repository、Job、route、正式 API 合同沉淀到项目实现。

# poster-title-image 真实流程本地验证 Runbook

本文说明如何在本地实测 `./scripts/real-flow.sh poster-title-image`，以及如何理解等待、参考图、输出下载、图片检测和常见失败。

## 先理解这条命令

`poster-title-image` 不是“提交 Job 后立刻退出”的命令。它是一个同步验证入口，会：

1. 准备参考图。
2. 调用本地 API 创建真实 `poster_title_image` Job。
3. 等 worker 执行到终态。
4. 查询 billing。
5. 按需下载输出图并做本地检测。
6. 输出 summary 和原始 HTTP envelope。

所以命令不会立即返回是正常的。默认最长等待 `900s`，每 `2s` 轮询一次，直到 Job 进入 `succeeded` 或 `failed`。

这条命令会触发真实模型调用，必须显式传 `--confirm-cost`。

## 前置条件

启动本地服务并执行迁移：

```bash
./scripts/dev.sh restart
```

查看服务状态：

```bash
./scripts/dev.sh status
```

如果刚改过迁移或 Job workflow，至少确认：

```bash
./scripts/verify.sh check
./scripts/verify.sh migration-roundtrip
```

## 最小实测命令

使用默认参考图 `.data/title/英语.png`：

```bash
./scripts/real-flow.sh poster-title-image \
  --confirm-cost \
  --language es \
  --title-text "Cuando el amor se alejo" \
  --download-outputs \
  --json
```

指定本地参考图：

```bash
./scripts/real-flow.sh poster-title-image \
  --confirm-cost \
  --reference .data/title/标题2.png \
  --language ja \
  --title-text "愛が終わりを告げたとき" \
  --download-outputs \
  --json
```

生成后下载输出图，并把下载检测结果写入 summary：

```bash
./scripts/real-flow.sh poster-title-image \
  --confirm-cost \
  --reference .data/title/英语.png \
  --language es \
  --title-text "Cuando el amor se alejo" \
  --download-outputs \
  --json
```

## 构建 items-json 示例

当一次要生成多个语种或多套标题时，使用 `--items-json`。传入 `--items-json` 后，脚本会忽略单 item 的 `--reference`、`--language`、`--title-text`、`--item-id` 等参数，改用 JSON 文件里的 `items[]`。

每个 item 至少需要：

- `item_id`：同一个 Job 内唯一。
- `language`：必须来自共享业务语种目录；同一个 Job 内允许重复。
- `title_text`：要渲染到标题图里的目标文案。
- `reference`：参考标题图，可以是本地图片，也可以是完整 OSS URL Ref。

可选字段：

- `model_id`：默认不传，由服务端 `poster_title_image` 默认生图模型决定。
- `size`：默认使用命令行 `--size`。
- `quality`：默认使用命令行 `--quality`。
- `draw_count`：默认使用命令行 `--draw-count`，范围是 `1` 到 `4`。

可复制示例：

```bash
mkdir -p .data/title
cat > .data/title/poster-items.json <<'JSON'
{
  "items": [
    {
      "item_id": "es",
      "language": "es",
      "title_text": "Cuando el amor se alejo",
      "reference": {
        "image": ".data/title/Silent_Heart_Stolen_Love.png",
        "content_type": "image/png"
      },
      "draw_count": 1
    },
    {
      "item_id": "pt",
      "language": "pt",
      "title_text": "Quando o amor se afastou",
      "reference": {
        "image": ".data/title/True_Heiress_Never_Lies.png",
        "content_type": "image/png"
      },
      "draw_count": 1
    }
  ]
}
JSON
```

执行多 item 真实流程：

```bash
./scripts/real-flow.sh poster-title-image \
  --confirm-cost \
  --confirm-upload \
  --items-json .data/title/poster-items.json \
  --download-outputs \
  --json
```

如果不同语种要使用不同参考图，可以在每个 item 里指定不同本地图片：

```json
{
  "items": [
    {
      "item_id": "es",
      "language": "es",
      "title_text": "Cuando el amor se alejo",
      "reference": {
        "image": ".data/title/es-reference.png",
        "content_type": "image/png"
      }
    },
    {
      "item_id": "ja",
      "language": "ja",
      "title_text": "愛が終わりを告げたとき",
      "reference": {
        "image": ".data/title/ja-reference.png",
        "content_type": "image/png"
      }
    }
  ]
}
```

如果 item 使用已有 OSS URL Ref，`reference` 可以直接写四字段：

```json
{
  "items": [
    {
      "item_id": "es",
      "language": "es",
      "title_text": "Cuando el amor se alejo",
      "reference": {
        "public_url": "https://bucket.oss-region.aliyuncs.com/path/title.png",
        "internal_url": "https://bucket.oss-region-internal.aliyuncs.com/path/title.png",
        "content_type": "image/png",
        "sha256": "<64位小写sha256>"
      }
    }
  ]
}
```

`STORAGE_BACKEND=aliyun_oss` 且 item 里使用本地 `reference.image` 时，也需要在执行命令中加 `--confirm-upload`。

## 参考图如何进入 API 参数

脚本最终会把参考图转成 API 需要的 `reference_image` URL Ref：

```json
{
  "public_url": "...",
  "internal_url": "...",
  "content_type": "image/png",
  "sha256": "..."
}
```

参考图选择顺序：

```text
显式 --reference-public-url / --reference-internal-url / --reference-content-type / --reference-sha256
  |
  v
scripts/.env 中的 POSTER_TITLE_IMAGE_REFERENCE_* 配置
  |
  v
--reference 指定的本地图片
  |
  v
默认 .data/title/英语.png
```

本地图片处理规则：

- `STORAGE_BACKEND=local`：脚本把本地图片 stage 到 `LOCAL_OBJECT_STORAGE_PATH`，再生成 URL Ref。
- `STORAGE_BACKEND=aliyun_oss`：脚本会上传本地图片到 OSS，必须额外传 `--confirm-upload`。
- 传完整 OSS URL Ref 时，不会 stage 或上传本地图片。

参考图必须是透明背景 PNG 标题图层，不是完整海报图。

## --download-outputs 做什么

有 `--download-outputs` 时，Job 成功后脚本会处理 `job_result.items[].images[]`：

```text
读取 output object
  |
  v
下载到本地目录
  |
  v
校验 sha256
  |
  v
检测图片透明背景
  |
  v
写入 summary.artifacts 和 summary.image_inspection
```

默认下载目录：

```text
.data/real-flow/poster-title-image/<job_id>/<item_id>-<language>/
```

图片检测复用 `./scripts/verify.sh image-inspect` 的核心逻辑，等价于对下载后的本地图片要求：

```bash
./scripts/verify.sh image-inspect <local_path> --require-transparent-background
```

如果图片不是透明背景，真实流程会失败，命令返回非 0。

## JSON summary 怎么看

成功时重点看：

```json
{
  "summary": {
    "job_status": "succeeded",
    "billing_status": "estimated",
    "output_count": 1,
    "outputs": [],
    "artifacts": [],
    "image_inspection": {
      "enabled": true,
      "require_transparent_background": true,
      "checked_count": 1,
      "passed_count": 1,
      "failed_count": 0
    }
  }
}
```

字段含义：

- `outputs`：服务端 Job 结果里的 OSS object 信息。
- `artifacts`：本地下载后的文件信息，只在 `--download-outputs` 时有值。
- `artifacts[].sha256_verified`：下载文件与服务端 `sha256` 是否一致。
- `artifacts[].image_inspection`：本地图片检测详情，包括格式、尺寸、alpha 和透明背景。
- `image_inspection`：本次下载图片检测的整体汇总。

如果没有传 `--download-outputs`，脚本不会下载图片，也不会生成 `artifacts` 或下载图片检测汇总。

## 完整执行流程

客户端脚本流程：

```text
你执行 real-flow.sh
  |
  v
读取 .env / scripts/.env
  |
  v
解析本地 API 地址、鉴权 header、参考图来源
  |
  v
准备 reference_image
  |
  +-- 已有 OSS URL Ref：直接使用
  |
  +-- STORAGE_BACKEND=local：stage 本地图片到本地对象存储
  |
  +-- STORAGE_BACKEND=aliyun_oss：需要 --confirm-upload，上传本地图片到 OSS
  |
  v
POST /api/v1/ai-jobs/jobs
  |
  v
轮询 GET /jobs/{job_id}
  |
  +-- queued / running：等待后继续轮询
  |
  +-- failed：查询 billing，输出 JSON，exit 4
  |
  +-- succeeded：查询 billing
         |
         +-- 有 --download-outputs：下载、校验 sha256、检测透明背景
         |
         v
       输出 JSON，exit 0
```

服务端 workflow：

```text
root Job: poster_title_image
  |
  v
style probe 子 Job
  - 读取参考图
  - 调用 multimodal/text 模型分析标题字形风格
  |
  v
generate item 子 Job
  - 使用 language 和 title_text 生成标题图
  - 调用 gpt-image-2
  - 后处理透明背景
  - 写入对象存储
  |
  v
join 子 Job
  - 汇总每个 item 的结果
  - root Job 标记 succeeded / failed
```

## 常见问题

### 命令为什么不会立即返回

这是预期行为。脚本会等待 Job 终态，而不是只返回 `job_id`。

另开一个终端看状态：

```bash
./scripts/dev.sh status
./scripts/dev.sh logs worker
```

已知 `job_id` 时查看详情：

```bash
./scripts/jobs.sh inspect <job_id>
```

查看近期运行中的 Job：

```bash
./scripts/jobs.sh list --status queued,running --since 10m
```

### 报 job scope\_id must equal job\_id

这是旧应用层校验不支持 workflow 子 Job 记账到 root Job 的表现。

处理方式：

```bash
./scripts/dev.sh restart
```

确认本地服务加载了支持 `scope_job_id` 的代码后再重跑真实流程。

### 报 ck\_ai\_call\_ledger\_entries\_job\_scope\_context

这是数据库约束仍停留在旧规则：`scope_id = job_id::text`。workflow 子 Job 现在会写：

```text
scope_id = root_job_id
job_id   = child_job_id
```

处理方式：

```bash
./scripts/dev.sh migrate
./scripts/dev.sh restart api
./scripts/dev.sh restart worker
```

如果要验证迁移链：

```bash
./scripts/verify.sh migration-roundtrip
```

### 报 image inspection failed

说明输出图已下载并通过 `sha256`，但不是透明背景图片。常见原因：

- 生成结果是完整海报或不透明 PNG。
- 后处理透明背景失败。
- 模型输出格式与预期不一致。

查看 summary 中对应 `artifacts[].local_path`，单独检查：

```bash
./scripts/verify.sh image-inspect <local_path> --require-transparent-background
```

### STORAGE\_BACKEND=aliyun\_oss 时为什么需要 --confirm-upload

传本地 `--reference` 且对象存储是 `aliyun_oss` 时，脚本会把参考图上传到 OSS。为了避免误上传，必须显式传：

```bash
--confirm-upload
```

如果不想上传，改用完整 OSS URL Ref：

```bash
./scripts/real-flow.sh poster-title-image \
  --confirm-cost \
  --reference-public-url "https://bucket.oss-region.aliyuncs.com/path/title.png" \
  --reference-internal-url "https://bucket.oss-region-internal.aliyuncs.com/path/title.png" \
  --reference-content-type image/png \
  --reference-sha256 "<64位小写sha256>" \
  --language es \
  --title-text "Cuando el amor se alejo" \
  --json
```

## 维护规则

修改 `poster-title-image` 真实流程后，同步检查本文：

- CLI 参数是否变化。
- 默认参考图和下载目录是否变化。
- `summary` 字段是否变化。
- `--download-outputs` 的下载、sha256 校验和图片检测语义是否变化。
- 常见失败是否仍是当前实现事实。

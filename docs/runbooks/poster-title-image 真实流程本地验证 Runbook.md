# poster-title-image 真实流程验证 Runbook

本文说明如何用 `./scripts/real-flow.sh` 验证 `poster_title_image`，覆盖本地开发和远端测试环境。推荐心智模型是：先检查配置，再准备 `reference_image` URL Ref，最后创建真实 Job。

## 先理解这件事

`poster-title-image` 是真实业务流程验证入口，不是单纯的 HTTP 请求示例。它会创建真实 `poster_title_image` Job，等待 worker 执行到终态，查询 billing，并按需下载输出图做本地检测。

完整链路是：

```text
配置检查
  |
  v
准备 reference_image URL Ref
  |
  v
POST /api/v1/ai-jobs/jobs
  |
  v
轮询 Job 终态
  |
  v
查询 billing
  |
  v
可选下载输出图并校验 sha256 / 透明背景
```

所以命令不会立即返回是正常的。默认最长等待 `900s`，每 `2s` 轮询一次，直到 Job 进入 `succeeded` 或 `failed`。

这条链路会触发真实模型调用，必须显式传 `--confirm-cost`。涉及上传本地图片到 OSS 时，必须显式传 `--confirm-upload`。

## 选择验证模式

优先按目标选择路径：

| 目标 | 推荐路径 | 说明 |
|---|---|---|
| 验证远端测试环境 | `doctor` -> `oss-upload-image --json-ref-only` -> `poster-title-image --reference-url-ref-json` | 最接近调用方传 URL Ref 的真实形态，问题定位最清楚。 |
| 本地开发快速验证 | `poster-title-image --reference 本地图片` | 适合本地 API/worker 联调。 |
| 复现调用方入参 | `poster-title-image --reference-url-ref-json` 或手动四字段 | 避免本地上传路径影响判断。 |

旧的“一条 `poster-title-image` 命令里直接传本地 `--reference` 并上传到 OSS”仍可用，但不作为远端测试推荐路径。远端测试更推荐先单独上传得到 URL Ref，再用 URL Ref 创建 Job。

## 推荐流程：远端测试环境

远端测试环境建议拆成三步。这样每一步失败时都能明确定位问题，不需要在一个长命令里猜是配置、上传、URL Ref 还是 Job 创建失败。

### 1. 检查 real-flow 上下文

```bash
./scripts/real-flow.sh doctor \
  --env-file env_test/.env \
  --allow-remote-api \
  --api-url http://test-cms-poster-title.epubgame.com \
  --json
```

重点看：

```json
{
  "ready": true,
  "problems": [],
  "api_url_source": "cli",
  "service_api_key_source": "env_file",
  "storage_backend": "aliyun_oss",
  "oss_bucket": "aigc-datas",
  "oss_region": "us-west-1",
  "oss_public_endpoint": "aigc-datas.epubgame.com"
}
```

判断规则：

- `ready=true` 且 `problems=[]` 才继续。
- `service_api_key_source=env_file` 表示 `SERVICE_API_KEY` 已从 `env_test/.env` 加载，不需要在命令前再写 `SERVICE_API_KEY=...`。
- 如果是 `service_api_key_source=runtime_env`，说明当前 shell 的环境变量覆盖了 `env_test/.env`。
- `api_url_source=cli` 是因为命令里显式传了 `--api-url`，这是预期行为。

### 2. 上传参考图并生成 URL Ref

```bash
mkdir -p .run

./scripts/real-flow.sh oss-upload-image \
  --env-file env_test/.env \
  --confirm-upload \
  --image .data/title/True_Heiress_Never_Lies.png \
  --json-ref-only > .run/reference-image.json
```

检查输出文件：

```bash
cat .run/reference-image.json
```

期望结构：

```json
{
  "public_url": "https://aigc-datas.epubgame.com/test-cms-poster-title/ai-jobs/...",
  "internal_url": "https://aigc-datas.oss-us-west-1-internal.aliyuncs.com/test-cms-poster-title/ai-jobs/...",
  "content_type": "image/png",
  "sha256": "..."
}
```

这里 `public_url` 应使用 CDN 域名，`internal_url` 仍是 OSS 内网地址。服务端当前读取参考图使用 `public_url`。

### 3. 使用 URL Ref 创建真实 Job

```bash
./scripts/real-flow.sh poster-title-image \
  --allow-remote-api \
  --env-file env_test/.env \
  --api-url http://test-cms-poster-title.epubgame.com \
  --x-ai-service-caller-id default \
  --confirm-cost \
  --reference-url-ref-json .run/reference-image.json \
  --language es \
  --title-text "Cuando el amor se alejo" \
  --download-outputs \
  --json
```

如果 `SERVICE_API_KEY` 不在 `env_test/.env`，优先在执行前通过当前 shell 会话、密钥管理器或 CI secret 注入。共享机器不要把真实 token 直接写进命令行。

```bash
export SERVICE_API_KEY='<测试环境 API token>'
```

只有一次性临时排查、且确认 shell history 不会记录敏感值时，才使用内联环境变量前缀：

```bash
SERVICE_API_KEY='<测试环境 API token>' \
./scripts/real-flow.sh poster-title-image \
  --allow-remote-api \
  --env-file env_test/.env \
  --api-url http://test-cms-poster-title.epubgame.com \
  --x-ai-service-caller-id default \
  --confirm-cost \
  --reference-url-ref-json .run/reference-image.json \
  --language es \
  --title-text "Cuando el amor se alejo" \
  --download-outputs \
  --json
```

共享机器或 CI 不建议使用内联环境变量或命令行 `--service-api-key`，因为它们可能进入 shell history、CI 日志或进程参数。

## 本地开发流程

本地验证前启动依赖、API 和 worker：

```bash
./scripts/dev.sh restart
./scripts/dev.sh status
```

如果刚改过迁移、Job workflow 或对象存储逻辑，先跑：

```bash
./scripts/verify.sh check
./scripts/verify.sh migration-roundtrip
```

本地最小命令：

```bash
./scripts/real-flow.sh poster-title-image \
  --confirm-cost \
  --reference .data/title/True_Heiress_Never_Lies.png \
  --language es \
  --title-text "Cuando el amor se alejo" \
  --api-url http://127.0.0.1:18200 \
  --caller-id default \
  --download-outputs \
  --json
```

如果本地配置也是 `STORAGE_BACKEND=aliyun_oss`，并且传的是本地 `--reference` 图片，需要额外加：

```bash
--confirm-upload
```

## 可选旧路径：一条命令上传并创建 Job

如果你只是临时验证远端测试环境，也可以在 `poster-title-image` 里直接传本地图片，让脚本先上传参考图再创建 Job：

```bash
./scripts/real-flow.sh poster-title-image \
  --allow-remote-api \
  --env-file env_test/.env \
  --api-url http://test-cms-poster-title.epubgame.com \
  --x-ai-service-caller-id default \
  --confirm-cost \
  --confirm-upload \
  --reference .data/title/True_Heiress_Never_Lies.png \
  --language es \
  --title-text "Cuando el amor se alejo" \
  --download-outputs \
  --json
```

这个路径步骤更少，但排障边界更粗：上传、URL Ref 构造、Job 创建和轮询都混在一条命令里。遇到 400 或 500 时，优先切回推荐三步流程。

## reference_image 来源

`poster-title-image` 最终传给 API 的参考图固定是 URL Ref：

```json
{
  "public_url": "...",
  "internal_url": "...",
  "content_type": "image/png",
  "sha256": "..."
}
```

推荐来源有三种，只能选一种，混用会直接报错：

| 来源 | 适用场景 | 命令 |
|---|---|---|
| 本地图片 | 本地开发最快 | `--reference .data/title/xxx.png` |
| 上传后复用 URL Ref JSON | 远端测试推荐 | `--reference-url-ref-json .run/reference-image.json` |
| 手动四字段 URL Ref | 调用方已给完整对象引用 | `--reference-public-url ... --reference-internal-url ... --reference-content-type image/png --reference-sha256 ...`；`--reference-content-type` 也可使用 `image/jpeg` 或 `image/webp` |

`--reference-url-ref-json` 可以读取两种结构：

```json
{
  "public_url": "...",
  "internal_url": "...",
  "content_type": "image/png",
  "sha256": "..."
}
```

或 `oss-upload-image --json` 的完整输出：

```json
{
  "url_ref": {
    "public_url": "...",
    "internal_url": "...",
    "content_type": "image/png",
    "sha256": "..."
  }
}
```

参考图必须是透明背景 PNG 标题图层，不是完整海报图。

## items-json 多语种流程

当一次要生成多个语种或多套标题时，使用 `--items-json`。传入 `--items-json` 后，脚本会忽略单 item 的 `--reference`、`--language`、`--title-text`、`--item-id` 等参数，改用 JSON 文件里的 `items[]`。

每个 item 至少需要：

- `item_id`：同一个 Job 内唯一；首字符必须是字母或数字，后续只允许字母、数字、`.`、`_`、`-`。
- `language`：必须来自共享业务语种目录；同一个 Job 内允许重复。
- `title_text`：要渲染到标题图里的目标文案。
- `reference`：参考标题图，可以是本地图片、URL Ref JSON 文件，也可以是完整 URL Ref 四字段。

可选字段：

- `model_id`：默认不传，由服务端 `poster_title_image` 默认生图模型决定。
- `size`：默认使用命令行 `--size`。
- `quality`：默认使用命令行 `--quality`。
- `draw_count`：默认使用命令行 `--draw-count`，范围是 `1` 到 `4`。

本地图片示例：

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

执行：

```bash
./scripts/real-flow.sh poster-title-image \
  --confirm-cost \
  --confirm-upload \
  --items-json .data/title/poster-items.json \
  --download-outputs \
  --json
```

URL Ref JSON 示例：

```json
{
  "items": [
    {
      "item_id": "es",
      "language": "es",
      "title_text": "Cuando el amor se alejo",
      "reference": {
        "url_ref_json": ".run/reference-image.json"
      }
    }
  ]
}
```

完整 URL Ref 四字段示例：

```json
{
  "items": [
    {
      "item_id": "es",
      "language": "es",
      "title_text": "Cuando el amor se alejo",
      "reference": {
        "public_url": "https://aigc-datas.epubgame.com/test-cms-poster-title/ai-jobs/reference.png",
        "internal_url": "https://aigc-datas.oss-us-west-1-internal.aliyuncs.com/test-cms-poster-title/ai-jobs/reference.png",
        "content_type": "image/png",
        "sha256": "<64位小写sha256>"
      }
    }
  ]
}
```

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

图片检测复用 `./scripts/verify.sh image-inspect` 的核心逻辑，等价于：

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

## 常见问题

### doctor ready=false

先看 `problems`。常见原因：

- `SERVICE_API_KEY` 未配置，且 `DISABLE_HTTP_AUTH_HEADER=false`。
- `STORAGE_BACKEND=aliyun_oss` 时缺少 `OSS_BUCKET`、`OSS_REGION`、`OSS_ACCESS_KEY_ID`、`OSS_ACCESS_KEY_SECRET` 或 `OSS_PROJECT_ROOT`。

如果 `--api-url` 指向远端但没有传 `--allow-remote-api`，`doctor` 会在解析 URL 阶段直接 exit 2，而不是输出 `ready=false`。

### poster title image reference invalid

这是 API 创建 Job 阶段的参考图校验失败，还没有进入 worker 下载图片。优先检查远端 API Pod 是否和本地配置一致。

在 API Pod 内执行：

```bash
grep -n "public_endpoint=settings.storage.oss_public_endpoint" /mnt/app/jobs/types/poster_title_image/executor.py
test -f /mnt/app/jobs/adapters/oss_url_ref.py && echo "has oss_url_ref adapter"
```

再检查运行时配置：

```bash
python - <<'PY'
from app.core.config import settings

print("OSS_PUBLIC_ENDPOINT=", repr(settings.storage.oss_public_endpoint))
print("allowed_buckets=", settings.job.poster_title_image_allowed_oss_buckets)
print("allowed_regions=", settings.job.poster_title_image_allowed_oss_regions)
PY
```

判断：

- 没有 `oss_url_ref.py`：远端 API 镜像代码没更新。
- `OSS_PUBLIC_ENDPOINT` 为空：远端没有注入 CDN 域名配置。
- `allowed_buckets` 缺 `aigc-datas`：远端白名单没更新。
- `allowed_regions` 缺 `us-west-1`：远端白名单没更新。

如果 `public_url` 是 CDN 域名，远端服务端必须支持 `OSS_PUBLIC_ENDPOINT`，否则会把 CDN 地址当成普通 OSS public endpoint 解析并拒绝。

### public_url 403 或不可读

worker 当前读取参考图使用 `public_url`。如果 Job 已创建但 worker 报 `reference image public_url is not readable`，说明 URL 可被 API 接受，但实际 HTTP 读取失败。

处理方向：

- 确认 `public_url` 使用可公开读取的 CDN 地址。
- 确认 CDN 已回源到对应 bucket 和前缀。
- 确认该对象路径对 CDN 可读。

`internal_url` 用于保留对象身份和内网访问地址，不代表 worker 一定会优先读取它。

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

### 报 job scope_id must equal scope_job_id

这是旧应用层校验不支持 workflow 子 Job 记账到 root Job 的表现。

处理方式：

```bash
./scripts/dev.sh restart
```

确认本地服务加载了支持 `scope_job_id` 的代码后再重跑真实流程。

### 报 ck_ai_call_ledger_entries_job_scope_context

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

### STORAGE_BACKEND=aliyun_oss 时为什么需要 --confirm-upload

传本地 `--reference` 且对象存储是 `aliyun_oss` 时，脚本会把参考图上传到 OSS。为了避免误上传，必须显式传：

```bash
--confirm-upload
```

如果不想上传，先用 `oss-upload-image --json-ref-only` 生成 URL Ref，再用 `--reference-url-ref-json` 创建 Job。

## 维护规则

修改 `poster-title-image` 真实流程后，同步检查本文：

- `doctor` 输出字段是否变化。
- `oss-upload-image --json-ref-only` 输出结构是否变化。
- `poster-title-image --reference-url-ref-json` 行为是否变化。
- 远端测试环境推荐命令是否仍能直接复制执行。
- `summary` 字段是否变化。
- `--download-outputs` 的下载、sha256 校验和图片检测语义是否变化。
- 常见失败是否仍是当前实现事实。

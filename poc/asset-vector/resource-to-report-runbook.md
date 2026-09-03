# Asset Vector POC 资源到报告 Runbook

本文是一份可复制执行的操作手册，用于从 `.data/assets` 资源目录开始，完成图片上传、向量入库、以图搜图验证和 HTML 报告生成。

## 先理解流程

这个 POC 有三类对象，先把它们分清：

```text
本地资源目录
  .data/assets
  里面是原始图片、音频、.DS_Store 等文件
  这是输入源

资源 manifest
  reports/manifests/assets-oss-manifest.jsonl
  里面是一行一张图片的映射关系
  记录：local_path -> oss_key -> public_url -> resource_id
  这是给程序读的中间清单

搜索报告
  reports/html/resource/*.html
  reports/html/image/*.html
  reports/html/text/*.html
  里面是一次搜索的 Top K 结果和图片预览
  这是给人看的验收结果

报告入口
  reports/html/index.html
  reports/html/resource/index.html
  reports/html/image/index.html
  reports/html/text/index.html
  这是 HTML 报告的导航入口
```

完整链路是：

```text
[阶段 1] 本地资源
  .data/assets
    |
    | upload_assets_to_oss.py --confirm-upload
    v
[阶段 2] 公网资源清单
  reports/manifests/assets-oss-manifest.jsonl
    |
    | asset_vector_poc.py index-manifest ... --confirm-remote
    v
[阶段 3] 向量库
  PostgreSQL / pgvector / poc_asset_vectors_intl_flash
    |
    | search-resource / search-image / search-text
    v
[阶段 4] 搜索报告
  reports/html/resource/<name>.html
  reports/html/image/<name>.html
  reports/html/text/<name>.html
  reports/html/index.html
```

关键点：

- `upload_assets_to_oss.py` 只负责上传图片和生成公网 URL 清单，不生成向量。
- `index-manifest` 负责读取公网 URL 清单，调用 DashScope 生成向量并写入 pgvector。
- 建向量和搜索必须使用同一个 `--model`、同一张 `--table`，不同模型生成的向量不能混在一起比较。
- `--html-report` 只在搜索命令里生效，用于生成 HTML 搜索报告。
- HTML 报告按查询方式分到 `resource`、`image`、`text` 三个目录。
- `reports/html/index.html` 是报告总入口，会指向三类查询方式的入口。

按阶段看输入和输出：

| 阶段 | 输入 | 执行动作 | 输出 | 后续用途 |
| --- | --- | --- | --- | --- |
| 资源扫描 | `.data/assets` | `upload_assets_to_oss.py --dry-run` | 本地扫描结果 | 确认哪些文件会上传、哪些会 skipped |
| 资源上传 | `.data/assets` | `upload_assets_to_oss.py --confirm-upload` | `reports/manifests/assets-oss-manifest.jsonl` | 保存本地图片和公网 URL 的映射 |
| 向量入库 | `reports/manifests/assets-oss-manifest.jsonl` | `asset_vector_poc.py index-manifest` | `poc_asset_vectors_intl_flash` 表数据 | 后续相似度搜索 |
| 搜索验证 | `poc_asset_vectors_intl_flash` | `search-resource` / `search-image` / `search-text` | CLI Top K 结果 | 快速看排序 |
| 报告生成 | 搜索结果 | 搜索命令加 `--html-report` 或批量报告命令 | `reports/html/**/*.html` | 肉眼验收效果 |

如果你不知道现在该跑哪一步，按这个判断：

```text
还没有公网 URL
  -> 跑 upload_assets_to_oss.py --confirm-upload

已经有 reports/manifests/assets-oss-manifest.jsonl
  -> 跑 asset_vector_poc.py index-manifest ... --confirm-remote

已经 index-manifest 成功
  -> 跑 search-resource / search-image / search-text

想看可视化结果
  -> 搜索命令里加 --html-report poc/asset-vector/reports/html/<query-mode>/<name>.html

想一次生成多份报告
  -> resource 跑 generate-resource-reports
  -> image 跑 generate-image-reports
  -> text 跑 generate-text-reports
```

本 runbook 后续命令默认使用这组参数：

```text
env_file: .env
table: poc_asset_vectors_intl_flash
model: tongyi-embedding-vision-flash
```

这组参数适合当前公司国际配置：

```bash
uv run python poc/asset-vector/asset_vector_poc.py \
  --dashscope-base-url https://dashscope-intl.aliyuncs.com/api/v1 \
  check-env
```

如果要用个人国内已经测通的定版模型，必须把所有 `asset_vector_poc.py` 命令里的 `--table` 和 `--model` 一起替换，例如：

```text
table: poc_asset_vectors_cn_flash_20260306
model: tongyi-embedding-vision-flash-2026-03-06
base_url: https://dashscope.aliyuncs.com/api/v1
```

不要只换 `.env`，却继续复用旧表。否则会出现：

```text
库内向量来自模型 A
query 向量来自模型 B
  -> pgvector 仍然会排序
  -> 但相似度结果不可信
  -> 同图也可能不是 Top 1
```

## 1. 准备环境

在仓库根目录执行：

```bash
cd /Users/admin/Code/cms/embedding-service
```

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

如果使用阿里云国际或 workspace 专属地址，命令里显式传 DashScope 原生 `api/v1` 地址：

```bash
--dashscope-base-url https://dashscope-intl.aliyuncs.com/api/v1
```

普通 `DASHSCOPE_BASE_URL` 可以给 OpenAI 兼容接口使用，但这个 POC 的多模态向量调用需要 native base URL。不要用 `compatible-mode/v1` 跑多模态向量。

调用 DashScope 的命令包括 `index-dir`、`index-manifest`、`search-image`、`search-text`、`generate-image-reports` 和 `generate-text-reports`。会写 OSS 的命令包括 `upload_assets_to_oss.py --confirm-upload`、`index-dir`、`search-image --query-image`，以及 manifest 里只有 `local_path` 没有 `public_url` 时的 `index-manifest`。

`search-resource` 和 `generate-resource-reports` 只读取本地 DB，不会使用 DashScope key 或 base URL。

启动本地依赖和服务：

```bash
./scripts/run.sh up dev
```

检查 POC 环境：

```bash
uv run python poc/asset-vector/asset_vector_poc.py \
  --env-file .env \
  --table poc_asset_vectors_intl_flash \
  --model tongyi-embedding-vision-flash \
  check-env
```

初始化 pgvector POC 表：

```bash
uv run python poc/asset-vector/asset_vector_poc.py \
  --env-file .env \
  --table poc_asset_vectors_intl_flash \
  --model tongyi-embedding-vision-flash \
  init-db
```

## 2. 扫描资源目录

默认资源目录是：

```text
/Users/admin/Code/cms/embedding-service/.data/assets
```

先 dry-run，只检查哪些图片会上传：

```bash
uv run python poc/asset-vector/upload_assets_to_oss.py \
  --env-file .env \
  --dry-run
```

也可以指定其它 assets 目录：

```bash
uv run python poc/asset-vector/upload_assets_to_oss.py \
  /path/to/assets \
  --env-file .env \
  --dry-run
```

输出里的含义：

```text
image_count
  可上传并进入图片向量流程的图片数量

skipped_count
  被跳过的文件数量
  常见原因：.DS_Store、音频、无扩展名文件、损坏图片、伪装成图片扩展名的非图片文件
```

`skipped` 不等于异常。只要被跳过的是非图片文件，说明脚本在避免把非图片送进图向量链路。

这一阶段只读本地文件，不上传、不建向量：

```text
输入：.data/assets
动作：识别可用图片，跳过非图片
输出：终端里的 image_count / skipped_count / image preview
远端调用：无
```

## 3. 上传图片并生成 manifest

确认 dry-run 结果符合预期后，执行真实上传：

```bash
uv run python poc/asset-vector/upload_assets_to_oss.py \
  --env-file .env \
  --confirm-upload
```

默认会生成：

```text
poc/asset-vector/reports/manifests/assets-oss-manifest.jsonl
```

这份文件是 JSONL，一行一张图片。每行会包含：

```text
resource_id
group_id
public_url
local_path
relative_path
relative_dir
file_name
content_type
oss_key
sha256
```

可以快速看前 3 行：

```bash
head -3 poc/asset-vector/reports/manifests/assets-oss-manifest.jsonl
```

如果你想输出到另一份 manifest：

```bash
uv run python poc/asset-vector/upload_assets_to_oss.py \
  --env-file .env \
  --manifest poc/asset-vector/reports/manifests/assets-oss-manifest-test.jsonl \
  --confirm-upload
```

这一阶段会写 OSS，并验证 CDN 公网访问：

```text
输入：.data/assets
动作：上传图片到 OSS，生成 public_url，校验 public_url 内容 hash
输出：reports/manifests/assets-oss-manifest.jsonl
远端调用：OSS 写入 + CDN 读取校验
```

manifest 每一行可以理解成一张图片的身份证：

```text
resource_id
  这张图片在向量库里的主键

relative_path / file_name
  这张图片原来在资源目录里的位置和名字

oss_key
  这张图片上传到 OSS 后的对象 key

public_url
  DashScope 后续能访问到的公网图片地址

sha256
  用来确认 CDN 读到的图片内容和本地上传内容一致
```

## 4. 用 manifest 生成向量

上传完成后，用 manifest 建向量：

```bash
uv run python poc/asset-vector/asset_vector_poc.py \
  --env-file .env \
  --table poc_asset_vectors_intl_flash \
  --model tongyi-embedding-vision-flash \
  index-manifest \
  poc/asset-vector/reports/manifests/assets-oss-manifest.jsonl \
  --confirm-remote
```

这一步会做：

```text
读取 assets-oss-manifest.jsonl
  -> 使用每行的 public_url
  -> 校验 public_url 可访问
  -> 调用 DashScope 多模态向量模型
  -> 写入 pgvector 表 poc_asset_vectors_intl_flash
```

因为 manifest 里已经有 `public_url`，所以这一步不会重复上传图片。

这一阶段只消费 manifest，不重新扫描资源目录：

```text
输入：reports/manifests/assets-oss-manifest.jsonl
动作：读取 public_url，调用 DashScope 多模态向量模型
输出：poc_asset_vectors_intl_flash 表里的 embedding
远端调用：DashScope embedding
不会做：重复上传 manifest 里已有 public_url 的图片
```

`index-manifest` 默认会把逐条进度输出到 stderr，最后把 JSON 汇总输出到 stdout：

```text
index progress: total=267 table=poc_asset_vectors_intl_flash model=tongyi-embedding-vision-flash ...
[1/267] START 物件/CBTB_Cris_7_p1
[1/267] OK 物件/CBTB_Cris_7_p1 input=image uploaded=false elapsed=0.42s
...
index progress: DONE indexed=267 failed=0 elapsed=98.3s
```

如果只想要最后的 JSON 汇总，可以关闭进度：

```bash
uv run python poc/asset-vector/asset_vector_poc.py \
  --env-file .env \
  --table poc_asset_vectors_intl_flash \
  --model tongyi-embedding-vision-flash \
  index-manifest \
  poc/asset-vector/reports/manifests/assets-oss-manifest.jsonl \
  --no-progress \
  --confirm-remote
```

查看已入库资源：

```bash
uv run python poc/asset-vector/asset_vector_poc.py \
  --env-file .env \
  --table poc_asset_vectors_intl_flash \
  --model tongyi-embedding-vision-flash \
  list-assets
```

## 5. 生成 resource 查询报告

`resource` 查询表示：用一个已经入库的 `resource_id` 当 query，去找库里相似图片。

单条 resource 查询适合临时检查，不会上传新的 query 图片，也不会调用 DashScope：

```bash
uv run python poc/asset-vector/asset_vector_poc.py \
  --env-file .env \
  --table poc_asset_vectors_intl_flash \
  --model tongyi-embedding-vision-flash \
  search-resource \
  '物件/hwic_champagne' \
  --top-k 12 \
  --html-report poc/asset-vector/reports/html/resource/search-resource-hwic-champagne.html
```

`search-resource` 默认会排除 query 自己。如果要验证“同一个资源能否把自己召回到 Top 1”，加 `--include-self`：

```bash
uv run python poc/asset-vector/asset_vector_poc.py \
  --env-file .env \
  --table poc_asset_vectors_intl_flash \
  --model tongyi-embedding-vision-flash \
  search-resource \
  '物件/hwic_champagne' \
  --include-self \
  --top-k 12 \
  --html-report poc/asset-vector/reports/html/resource/search-resource-hwic-champagne-include-self.html
```

这会生成：

```text
poc/asset-vector/reports/html/resource/search-resource-hwic-champagne.html
poc/asset-vector/reports/html/resource/index.html
poc/asset-vector/reports/html/index.html
```

`search-resource` 的关系是：

```text
输入：一个已经入库的 resource_id
动作：从 pgvector 取出这个 resource_id 的向量作为 query
输出：Top K 相似图片
远端调用：无
适合：验证库内图片之间的相似度
```

推荐用下面这条命令批量生成一组 resource 报告。它一次生成多份 HTML，并刷新 `resource/index.html` 和总入口 `reports/html/index.html`：

```bash
uv run python poc/asset-vector/asset_vector_poc.py \
  --env-file .env \
  --table poc_asset_vectors_intl_flash \
  --model tongyi-embedding-vision-flash \
  generate-resource-reports \
  --resource-id '物件/hwic_champagne' \
  --resource-id '物件/CBTB_Cris_7_p1' \
  --resource-id '物件/hwic_fathers_wedding_ring' \
  --include-self \
  --top-k 12
```

这会给上面 3 个 `resource_id` 各生成一份报告，并刷新：

```text
poc/asset-vector/reports/html/resource/index.html
poc/asset-vector/reports/html/index.html
```

也可以从已入库资源里取前 20 个批量生成：

```bash
uv run python poc/asset-vector/asset_vector_poc.py \
  --env-file .env \
  --table poc_asset_vectors_intl_flash \
  --model tongyi-embedding-vision-flash \
  generate-resource-reports \
  --limit 20 \
  --include-self \
  --top-k 12
```

如果你确认要给全部已入库资源生成报告：

```bash
uv run python poc/asset-vector/asset_vector_poc.py \
  --env-file .env \
  --table poc_asset_vectors_intl_flash \
  --model tongyi-embedding-vision-flash \
  generate-resource-reports \
  --all \
  --include-self \
  --top-k 12
```

也可以只给指定资源生成报告：

```bash
uv run python poc/asset-vector/asset_vector_poc.py \
  --env-file .env \
  --table poc_asset_vectors_intl_flash \
  --model tongyi-embedding-vision-flash \
  generate-resource-reports \
  --resource-id '物件/hwic_champagne' \
  --resource-id '物件/CBTB_Cris_7_p1' \
  --include-self \
  --top-k 12
```

批量命令不是另一套报告格式。它只是一次循环执行多次 resource 查询，复用单条报告生成逻辑：

```text
generate-resource-reports
  -> 选择一批 resource_id
  -> 每个 resource_id 生成一份 reports/html/resource/*.html
  -> 刷新 reports/html/resource/index.html
  -> 刷新 reports/html/index.html
```

## 6. 生成 image 查询报告

`image` 查询表示：用一张本地图片或公网图片 URL 当 query，去找库里相似图片。

推荐用下面这条命令批量生成一组 image 报告。它从 manifest 读取 query 图片的 `public_url`，不会重复上传 query 图片：

```bash
uv run python poc/asset-vector/asset_vector_poc.py \
  --env-file .env \
  --table poc_asset_vectors_intl_flash \
  --model tongyi-embedding-vision-flash \
  generate-image-reports \
  --manifest poc/asset-vector/reports/manifests/assets-oss-manifest.jsonl \
  --resource-id '物件/hwic_champagne' \
  --resource-id '物件/CBTB_Cris_7_p1' \
  --resource-id '物件/hwic_fathers_wedding_ring' \
  --top-k 12 \
  --confirm-remote
```

这条命令会生成：

```text
poc/asset-vector/reports/html/image/search-image-*.html
poc/asset-vector/reports/html/image/index.html
poc/asset-vector/reports/html/index.html
```

如果你确认要对 manifest 里的全部图片生成 image 查询报告：

```bash
uv run python poc/asset-vector/asset_vector_poc.py \
  --env-file .env \
  --table poc_asset_vectors_intl_flash \
  --model tongyi-embedding-vision-flash \
  generate-image-reports \
  --manifest poc/asset-vector/reports/manifests/assets-oss-manifest.jsonl \
  --all \
  --top-k 12 \
  --confirm-remote
```

`generate-image-reports --all` 会按 manifest 数量调用 DashScope。当前 manifest 是 267 张图，就会产生 267 次 query embedding 调用。

先用本地资源目录里的图片做查询。下面这条可以直接复制执行：

```bash
uv run python poc/asset-vector/asset_vector_poc.py \
  --env-file .env \
  --table poc_asset_vectors_intl_flash \
  --model tongyi-embedding-vision-flash \
  search-image \
  --query-image .data/assets/物件/CBTB_Cris_7_p1.png \
  --top-k 12 \
  --html-report poc/asset-vector/reports/html/image/search-image-CBTB_Cris_7_p1.html \
  --confirm-remote
```

注意：`search-image --query-image` 会把查询图片上传到 OSS，然后调用 DashScope 生成查询向量。

如果你想多生成几份 image 报告，可以继续复制下面这些命令：

```bash
uv run python poc/asset-vector/asset_vector_poc.py \
  --env-file .env \
  --table poc_asset_vectors_intl_flash \
  --model tongyi-embedding-vision-flash \
  search-image \
  --query-image .data/assets/物件/hwic_champagne.png \
  --top-k 12 \
  --html-report poc/asset-vector/reports/html/image/search-image-hwic-champagne.html \
  --confirm-remote

uv run python poc/asset-vector/asset_vector_poc.py \
  --env-file .env \
  --table poc_asset_vectors_intl_flash \
  --model tongyi-embedding-vision-flash \
  search-image \
  --query-image .data/assets/物件/hwic_fathers_wedding_ring.png \
  --top-k 12 \
  --html-report poc/asset-vector/reports/html/image/search-image-hwic-fathers-wedding-ring.html \
  --confirm-remote
```

`search-image --query-image` 的关系是：

```text
输入：一张本地 query 图片
动作：上传 query 图片，调用 DashScope 生成 query 向量，再搜 pgvector
输出：Top K 相似图片
远端调用：OSS 写入 + DashScope embedding
适合：模拟用户上传一张图来搜相似图
```

如果你想直接复用 `assets-oss-manifest.jsonl` 里的公网 URL，可以复制下面这组命令。它会先从 manifest 里取出 `物件/CBTB_Cris_7_p1` 对应的 `public_url`，再用这个 URL 做查询：

```bash
QUERY_URL=$(
  uv run python -c 'import json; p="poc/asset-vector/reports/manifests/assets-oss-manifest.jsonl"; rid="物件/CBTB_Cris_7_p1"; print(next(item["public_url"] for item in (json.loads(line) for line in open(p, encoding="utf-8")) if item["resource_id"] == rid))'
)

uv run python poc/asset-vector/asset_vector_poc.py \
  --env-file .env \
  --table poc_asset_vectors_intl_flash \
  --model tongyi-embedding-vision-flash \
  search-image \
  --query-url "$QUERY_URL" \
  --top-k 12 \
  --html-report poc/asset-vector/reports/html/image/search-image-url-CBTB_Cris_7_p1.html \
  --confirm-remote
```

`search-image --query-url` 的关系是：

```text
输入：一张已经公网可访问的 query 图片 URL
动作：调用 DashScope 生成 query 向量，再搜 pgvector
输出：Top K 相似图片
远端调用：DashScope embedding
适合：query 图片已经在 CDN 上，不想重复上传
```

## 7. 生成 text 查询报告

`text` 查询表示：用一段文本当 query，去找库里语义相关的图片。

推荐用下面这条命令批量生成一组 text 报告。每个 `--text` 会生成一份 HTML，并刷新 `text/index.html` 和总入口 `reports/html/index.html`：

```bash
uv run python poc/asset-vector/asset_vector_poc.py \
  --env-file .env \
  --table poc_asset_vectors_intl_flash \
  --model tongyi-embedding-vision-flash \
  generate-text-reports \
  --text 'champagne bottle' \
  --text 'golden dice' \
  --text 'red apple' \
  --text 'pistol with silencer' \
  --top-k 12 \
  --confirm-remote
```

这条命令会生成：

```text
poc/asset-vector/reports/html/text/search-text-*.html
poc/asset-vector/reports/html/text/index.html
poc/asset-vector/reports/html/index.html
```

如果你想把文本查询放进文件复用，可以准备一个每行一条 query 的文本文件，然后这样跑：

```bash
uv run python poc/asset-vector/asset_vector_poc.py \
  --env-file .env \
  --table poc_asset_vectors_intl_flash \
  --model tongyi-embedding-vision-flash \
  generate-text-reports \
  --text-file poc/asset-vector/reports/manifests/text-report-queries.txt \
  --top-k 12 \
  --confirm-remote
```

单条 text 查询适合临时检查：

```bash
uv run python poc/asset-vector/asset_vector_poc.py \
  --env-file .env \
  --table poc_asset_vectors_intl_flash \
  --model tongyi-embedding-vision-flash \
  search-text \
  'champagne bottle' \
  --top-k 12 \
  --html-report poc/asset-vector/reports/html/text/search-text-champagne.html \
  --confirm-remote
```

这一步会调用 DashScope 生成文本向量，然后在同一张 pgvector 表里检索图片资源。

`search-text` 的关系是：

```text
输入：一段文本
动作：调用 DashScope 生成文本向量，再搜 pgvector
输出：Top K 相关图片
远端调用：DashScope embedding
适合：验证文字搜图、标签搜图、描述搜图
```

## 8. 查看报告

报告生成关系是：

```text
search-resource ... --html-report reports/html/resource/a.html
  -> 生成 reports/html/resource/a.html
  -> 刷新 reports/html/resource/index.html
  -> 刷新 reports/html/index.html

generate-resource-reports ...
  -> 生成多份 reports/html/resource/*.html
  -> 刷新 reports/html/resource/index.html
  -> 刷新 reports/html/index.html

search-image ... --html-report reports/html/image/b.html
  -> 生成 reports/html/image/b.html
  -> 刷新 reports/html/image/index.html
  -> 刷新 reports/html/index.html

generate-image-reports ...
  -> 生成多份 reports/html/image/*.html
  -> 刷新 reports/html/image/index.html
  -> 刷新 reports/html/index.html

search-text ... --html-report reports/html/text/c.html
  -> 生成 reports/html/text/c.html
  -> 刷新 reports/html/text/index.html
  -> 刷新 reports/html/index.html

generate-text-reports ...
  -> 生成多份 reports/html/text/*.html
  -> 刷新 reports/html/text/index.html
  -> 刷新 reports/html/index.html
```

单份报告路径来自 `--html-report`：

```text
poc/asset-vector/reports/html/resource/search-resource-hwic-champagne.html
poc/asset-vector/reports/html/image/search-image-CBTB_Cris_7_p1.html
poc/asset-vector/reports/html/text/search-text-champagne.html
```

报告总入口是：

```text
poc/asset-vector/reports/html/index.html
```

`reports/html/index.html` 会指向三类查询报告入口：

```text
reports/html/resource/index.html
reports/html/image/index.html
reports/html/text/index.html
```

每个查询方式目录里的 `index.html` 会按卡片展示该目录下已有的 HTML 报告，包括：

```text
中文标题
报告文件名
更新时间
内嵌预览
打开完整报告链接
```

## 9. 如何判断以图搜图效果

先看 `search-resource` 或 `search-image` 报告里的 Top K：

```text
Top 1 - Top 3
  是否和 query 图片最接近

Top 10 / Top 12
  是否大体属于同类素材

score
  只用于排序和观察分布
  不要直接理解成百分比准确率
```

建议用一批人工知道答案的 query 图片做验证：

```text
query 图片 A
  期望：同一角色 / 同一物件 / 同一风格排在前面

query 图片 B
  期望：不同类别不要大量混入前 3

query 文本 C
  期望：语义相关素材能进入 Top K
```

如果要做更正式的准确率评估，需要先准备标注集，例如：

```text
query_resource_id
expected_resource_id 或 expected_group_id
```

然后统计：

```text
Recall@K
  期望结果是否出现在 Top K

Top-1 命中率
  第一名是否符合预期

同组命中率
  Top K 里有多少结果属于同一 group_id
```

当前 POC 的 HTML 报告主要用于人工肉眼验收，不是自动评测系统。

## 10. 常见问题

### `assets-oss-manifest.jsonl` 是报告吗

不是。它是资源清单，用于后续建向量。

```text
assets-oss-manifest.jsonl
  给机器读
  输入给 index-manifest

reports/html/**/*.html
  给人看
  用于检查搜索结果
```

### 为什么 dry-run 有 skipped

因为 `.data/assets` 里可能包含 `.DS_Store`、音频或其它非图片文件。当前 POC 只做图片向量，所以这些文件会被跳过。

### 如何避免重复上传

上传一次后保留：

```text
poc/asset-vector/reports/manifests/assets-oss-manifest.jsonl
```

后续重复建向量时只跑：

```bash
uv run python poc/asset-vector/asset_vector_poc.py \
  --env-file .env \
  --table poc_asset_vectors_intl_flash \
  --model tongyi-embedding-vision-flash \
  index-manifest \
  poc/asset-vector/reports/manifests/assets-oss-manifest.jsonl \
  --confirm-remote
```

不要重复跑 `upload_assets_to_oss.py --confirm-upload`，除非资源文件或 OSS/CDN 映射需要刷新。

### 为什么同一张图不是 Top 1

先确认不是模型效果问题，而是数据链路是否一致。

`search-resource` 默认排除 query 自己，所以它不会把同一个 `resource_id` 返回为 Top 1。POC 验证时如果想同时看“自己是否 Top 1”和“Top 2 以后是否相似”，使用 `--include-self`，并把 `--top-k` 调大一点。

`search-image` 的真实流程是：

```text
本地 query 图片
  -> 上传到 OSS query 目录
  -> DashScope 生成 query 向量
  -> PostgreSQL / pgvector 用 embedding <=> query_embedding 排序
  -> 输出 Top K
```

所以它确实使用了向量数据库。只是 pgvector 只负责数学距离排序，不知道两个向量是不是来自同一个模型。

如果同一张图没有排到前面，优先检查这三件事：

```text
1. 表是否是这次 manifest 新建出来的表
   旧表可能只有 24 条，resource_id 还是 CBTB_Cris_7_p1 这种旧格式
   新 manifest 是 物件/CBTB_Cris_7_p1 这种带子目录的格式

2. index-manifest 和 search-image/search-text 是否用了同一个 --model
   库内向量来自模型 A，query 向量来自模型 B 时，排序不可信

3. index-manifest 和搜索命令是否用了同一个 --table
   不要把旧表的向量和新 manifest 的 query 混在一起看
```

可以先用下面命令确认当前表里有没有新 `resource_id`：

```bash
uv run python poc/asset-vector/asset_vector_poc.py \
  --env-file .env \
  --table poc_asset_vectors_intl_flash \
  --model tongyi-embedding-vision-flash \
  exists \
  --resource-id '物件/CBTB_Cris_7_p1'
```

再看当前表里的资源数量和 URL 形态：

```bash
uv run python poc/asset-vector/asset_vector_poc.py \
  --env-file .env \
  --table poc_asset_vectors_intl_flash \
  --model tongyi-embedding-vision-flash \
  list-assets \
  --limit 20
```

正常的新链路应该能看到：

```text
resource_id: 物件/CBTB_Cris_7_p1
public_url: .../asset-vector-poc/assets/物件/CBTB_Cris_7_p1.png
```

如果看到的是：

```text
resource_id: CBTB_Cris_7_p1
public_url: .../asset-vector-poc/CBTB_Cris_7_p1.png
local_path: .data/物件/CBTB_Cris_7_p1.png
```

说明你正在查旧表或旧索引数据。此时应重新用当前 manifest 建一张新表，再用同一张表、同一个模型生成报告。

### 还需要 `reindex-hwic-champagne.jsonl` 吗

不需要长期保留。现在全量事实源是：

```text
poc/asset-vector/reports/manifests/assets-oss-manifest.jsonl
```

如果只想重建某一个资源，优先从全量 manifest 派生临时输入，或者直接用全量 manifest 重跑 `index-manifest`。手写单条 reindex manifest 容易和全量 manifest 的 `resource_id`、`public_url`、`sha256` 不一致。

### 哪些命令会产生远端调用

```text
upload_assets_to_oss.py --confirm-upload
  写 OSS，并验证 CDN public_url

asset_vector_poc.py index-manifest --confirm-remote
  调 DashScope 生成向量，写 pgvector

asset_vector_poc.py search-image --confirm-remote
  调 DashScope；如果使用 --query-image，还会上传 query 图片

asset_vector_poc.py generate-image-reports --confirm-remote
  调 DashScope；从 manifest 读取 public_url，不上传 query 图片

asset_vector_poc.py search-text --confirm-remote
  调 DashScope

asset_vector_poc.py generate-text-reports --confirm-remote
  调 DashScope
```

`search-resource` 使用库里已有向量作为 query，不会调用 DashScope。

`generate-resource-reports` 批量复用库内已有向量，也不会调用 DashScope 或 OSS。

## 11. 收尾

结束后停止本地 dev recipe：

```bash
./scripts/run.sh down dev
```

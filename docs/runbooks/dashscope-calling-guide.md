# DashScope 调用方式说明

本文解释本项目里 DashScope 的 `DASHSCOPE_API_KEY`、`DASHSCOPE_BASE_URL`、国内/国际 endpoint、OpenAI-compatible 协议、DashScope 原生协议、多模态向量和 rerank 之间的关系，并给出可直接复用的验证和调用示例。

## 先理解这件事

DashScope 调用地址由三层组成：

```text
region / workspace 选择 host
        +
protocol 选择 path
        +
model 选择具体能力
```

不要把 `DASHSCOPE_BASE_URL` 理解成“某个模型专属地址”。更准确的心智模型是：

```text
同一个 API Key 所属 region / workspace
  -> 选择同区域 host
     -> OpenAI-compatible 调用使用 /compatible-mode/v1
     -> DashScope 原生调用使用 /api/v1
        -> 再拼具体服务路径
```

例如国际新加坡共享域名：

```text
https://dashscope-intl.aliyuncs.com/compatible-mode/v1
        |---------------------| |------------------|
                 host                 protocol path

https://dashscope-intl.aliyuncs.com/api/v1
        |---------------------| |----|
                 host          native protocol path
```

国内/国际的区别主要是 `host` 和 API Key 所属地域；chat、文本向量、多模态向量和 rerank 的区别主要是 `path` 所代表的协议与具体服务路径。

## 协议边界

| 协议 | Base URL path | 典型用途 | 本项目中的常见配置 |
|---|---|---|---|
| OpenAI-compatible | `/compatible-mode/v1` | 用 OpenAI SDK 风格调用 chat、文本 embedding、models list | `DASHSCOPE_BASE_URL` |
| DashScope native | `/api/v1` | 调用 DashScope 原生能力，例如多模态 embedding、`qwen3-vl-rerank` | `POC_DASHSCOPE_BASE_URL` 或由专用代码从同 host 推导 |

两套协议能调用的模型不是严格包含关系。

```text
OpenAI-compatible 可调用能力
  chat/completions
  text embeddings，例如 text-embedding-v4、text-embedding-v3、qwen3.7-text-embedding
  OpenAI SDK 兼容模型

DashScope native 可调用能力
  multimodal embedding
  multimodal rerank，例如 qwen3-vl-rerank
  DashScope 原生服务路径
  部分不走 OpenAI 兼容协议的模型能力

交集存在，但不能假设一个协议能 list 到模型，另一个协议就能用同样 payload 调用。
```

不要把“OpenAI-compatible 不支持多模态向量”误读成“OpenAI-compatible 不支持任何向量”。它支持文本向量 `/embeddings`，但 `qwen3-vl-embedding`、`tongyi-embedding-vision-*` 和 `multimodal-embedding-v1` 这类多模态向量应走 DashScope native Multimodal Embedding API。

`qwen3-vl-rerank` 是重排模型，不是向量模型。它不能放进多模态 embedding 模型列表里用同一个 endpoint 调用，而应走 native rerank endpoint。

## 以图搜图模型链路

以图搜图通常不是只靠一个模型完成，而是“向量召回 + 可选重排”的两段式链路：

```text
图片 query
  -> 多模态 embedding 模型生成 query 向量
  -> 向量库召回 TopK 候选
  -> 可选：qwen3-vl-rerank 对候选二次排序
  -> 返回 TopN 结果
```

| 阶段 | 是否必需 | 模型类型 | 典型模型 | 调用协议 |
|---|---:|---|---|---|
| 入库向量化 | 必需 | 多模态 embedding | `qwen3-vl-embedding`、`tongyi-embedding-vision-plus`、`tongyi-embedding-vision-flash`、`multimodal-embedding-v1` | native `/api/v1` |
| 查询向量化 | 必需 | 多模态 embedding | 必须和入库模型一致 | native `/api/v1` |
| 向量库召回 | 必需 | 向量检索 | pgvector、Milvus、Elasticsearch vector 等 | 项目内部能力 |
| 候选重排 | 可选 | 多模态 rerank | `qwen3-vl-rerank` | native `/api/v1` |

embedding 模型决定“能否生成向量并做粗召回”；rerank 模型决定“召回后的候选排序能否再提升”。rerank 不能替代 embedding，也不能用于建立向量索引。

## 国内和国际配置

### 共享域名

国内北京：

```env
DASHSCOPE_API_KEY=<北京地域 API Key>
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

国际新加坡：

```env
DASHSCOPE_API_KEY=<新加坡地域 API Key>
DASHSCOPE_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
```

美国弗吉尼亚：

```env
DASHSCOPE_API_KEY=<美国弗吉尼亚地域 API Key>
DASHSCOPE_BASE_URL=https://dashscope-us.aliyuncs.com/compatible-mode/v1
```

### Workspace 专属域名

生产更推荐 workspace 专属域名。host 换成对应 workspace，path 仍按协议选择。

国内北京：

```env
DASHSCOPE_API_KEY=<北京 workspace API Key>
DASHSCOPE_BASE_URL=https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
```

国际新加坡：

```env
DASHSCOPE_API_KEY=<新加坡 workspace API Key>
DASHSCOPE_BASE_URL=https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1
```

DashScope native 地址只改 path：

```env
# OpenAI-compatible
DASHSCOPE_BASE_URL=https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1

# DashScope native
POC_DASHSCOPE_BASE_URL=https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/api/v1
```

API Key、host 的地域和 workspace 必须匹配。北京 Key 调新加坡 endpoint，或默认 workspace Key 调某个子 workspace 专属域名，都会按鉴权错误处理。

## 本项目怎么用

### 主服务配置

主服务里的 `DASHSCOPE_BASE_URL` 表示 DashScope 的 OpenAI-compatible base URL：

```env
DASHSCOPE_API_KEY=<同地域 API Key>
DASHSCOPE_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
```

它用于 provider registry、`scripts/ai.sh models dashscope` 和 OpenAI-compatible adapter 路径。

验证 API Key 和模型列表：

```bash
./scripts/ai.sh --env-file .env models dashscope
```

成功时会看到：

```text
AI Models
- provider: dashscope
  status: ok
  model_count: ...
  endpoint: https://...
  - qwen-plus owned_by=...
```

这个验证只访问远端 models list，不提交 Job，不执行推理。

### Asset Vector POC 配置

`poc/asset-vector/asset_vector_poc.py` 调用的是 DashScope native Multimodal Embedding API。它不使用 `DASHSCOPE_BASE_URL` 作为调用地址，而是按以下优先级读取 native base URL：

```text
POC_DASHSCOPE_BASE_URL
  -> DASHSCOPE_NATIVE_BASE_URL
  -> DASHSCOPE_API_HOST
  -> https://dashscope.aliyuncs.com/api/v1
```

因此国际新加坡要这样配：

```env
DASHSCOPE_API_KEY=<新加坡地域 API Key>
DASHSCOPE_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
POC_DASHSCOPE_BASE_URL=https://dashscope-intl.aliyuncs.com/api/v1
```

workspace 专属域名：

```env
DASHSCOPE_API_KEY=<新加坡 workspace API Key>
DASHSCOPE_BASE_URL=https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1
POC_DASHSCOPE_BASE_URL=https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/api/v1
```

检查 POC 环境：

```bash
uv run python poc/asset-vector/asset_vector_poc.py check-env
```

发起一次文字搜图向量调用：

```bash
uv run python poc/asset-vector/asset_vector_poc.py search-text "white running shoes" --top-k 10 --confirm-remote
```

该 POC 会拒绝包含 `compatible-mode` 的 native base URL，因为它最终拼出的接口是：

```text
{POC_DASHSCOPE_BASE_URL}
  /services/embeddings/multimodal-embedding/multimodal-embedding
```

如果误填：

```env
POC_DASHSCOPE_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
```

会得到类似错误：

```text
DashScope multimodal embeddings require native api/v1, not compatible-mode/v1
```

## 调用示例

### OpenAI-compatible Chat

```bash
curl --request POST "$DASHSCOPE_BASE_URL/chat/completions" \
  --header "Authorization: Bearer $DASHSCOPE_API_KEY" \
  --header "Content-Type: application/json" \
  --data '{
    "model": "qwen-plus",
    "messages": [
      {"role": "user", "content": "用一句话介绍 DashScope。"}
    ]
  }'
```

这里的 `DASHSCOPE_BASE_URL` 应该是 `/compatible-mode/v1`。

### OpenAI-compatible Text Embedding

```bash
curl --request POST "$DASHSCOPE_BASE_URL/embeddings" \
  --header "Authorization: Bearer $DASHSCOPE_API_KEY" \
  --header "Content-Type: application/json" \
  --data '{
    "model": "text-embedding-v4",
    "input": ["白色运动鞋", "黑色皮鞋"]
  }'
```

这里仍然使用 `/compatible-mode/v1`。适用前提是目标 embedding 模型支持 OpenAI-compatible embeddings API。

### DashScope Native Multimodal Embedding

```bash
DASHSCOPE_NATIVE_BASE_URL="${DASHSCOPE_BASE_URL%/compatible-mode/v1}/api/v1"

curl --request POST "$DASHSCOPE_NATIVE_BASE_URL/services/embeddings/multimodal-embedding/multimodal-embedding" \
  --header "Authorization: Bearer $DASHSCOPE_API_KEY" \
  --header "Content-Type: application/json" \
  --data '{
    "model": "tongyi-embedding-vision-flash-2026-03-06",
    "input": {
      "contents": [
        {
          "text": "白色运动鞋，轻量透气，适合跑步",
          "image": "https://example.com/shoe.png"
        }
      ]
    }
  }'
```

这里使用 `/api/v1`，并继续拼 DashScope native 服务路径。连通性验证时可以不传 `parameters.dimension`，让模型使用默认维度；真正落库前再根据向量库字段选择固定维度。

### DashScope Native Multimodal Rerank

```bash
DASHSCOPE_NATIVE_BASE_URL="${DASHSCOPE_BASE_URL%/compatible-mode/v1}/api/v1"

curl --request POST "$DASHSCOPE_NATIVE_BASE_URL/services/rerank/text-rerank/text-rerank" \
  --header "Authorization: Bearer $DASHSCOPE_API_KEY" \
  --header "Content-Type: application/json" \
  --data '{
    "model": "qwen3-vl-rerank",
    "input": {
      "query": {
        "image": "https://example.com/query.png"
      },
      "documents": [
        {
          "image": "https://example.com/candidate-1.png"
        },
        {
          "text": "白色运动鞋，轻量透气，适合跑步"
        }
      ]
    },
    "parameters": {
      "return_documents": false,
      "top_n": 2
    }
  }'
```

这里同样使用 `/api/v1`。`qwen3-vl-rerank` 接收 query 和候选 documents，返回候选排序分数；它不返回向量，不能用于向量库建索引。

### 最小 Probe

仓库里有一个独立 probe，用来验证“账号 + base URL + 模型”是否能支撑以图搜图链路：

```bash
uv run python poc/tongyi_embedding_vision_flash_probe.py --confirm-cost
```

该脚本不从 `.env` 读取 Key，而是在脚本头部配置四组账号与 endpoint：

```python
PERSONAL_CN_DASHSCOPE_API_KEY = "..."
PERSONAL_CN_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"

PERSONAL_INTL_DASHSCOPE_API_KEY = "..."
PERSONAL_INTL_DASHSCOPE_BASE_URL = "https://dashscope-intl.aliyuncs.com/api/v1"

COMPANY_CN_DASHSCOPE_API_KEY = "..."
COMPANY_CN_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"

COMPANY_INTL_DASHSCOPE_API_KEY = "..."
COMPANY_INTL_DASHSCOPE_BASE_URL = "https://dashscope-intl.aliyuncs.com/api/v1"
```

它会分别验证两类模型：

```python
MODELS = (
    ("qwen3-vl-embedding", None),
    ("tongyi-embedding-vision-plus", None),
    ("tongyi-embedding-vision-flash", None),
    ("multimodal-embedding-v1", None),
    ("tongyi-embedding-vision-plus-2026-03-06", None),
    ("tongyi-embedding-vision-flash-2026-03-06", None),
)

RERANK_MODELS = (
    "qwen3-vl-rerank",
)
```

脚本最后会输出面向认读的汇总表：

```text
Tested Embedding Models
- E1: qwen3-vl-embedding
- E2: tongyi-embedding-vision-plus

Tested Rerank Models
- R1: qwen3-vl-rerank

Image Search Capability By Account
| account | embedding_supported | rerank_supported |
|---|---|---|
| personal_cn (个人国内) | qwen3-vl-embedding (2560维) | qwen3-vl-rerank |
| company_cn (公司国内) | multimodal-embedding-v1 (1024维) | none |
```

只跑一个账号：

```bash
uv run python poc/tongyi_embedding_vision_flash_probe.py --target company_intl --confirm-cost
```

切换输入形态：

```bash
uv run python poc/tongyi_embedding_vision_flash_probe.py --kind image --confirm-cost
uv run python poc/tongyi_embedding_vision_flash_probe.py --kind fused --text "白色运动鞋，轻量透气" --confirm-cost
```

这个 probe 适合验证当前 Key 和 endpoint 下有哪些多模态 embedding 模型、哪些 rerank 模型可连通。它发真实 DashScope 请求，因此必须显式传 `--confirm-cost`。

## 常见判断

| 现象 | 更可能的原因 | 处理方式 |
|---|---|---|
| `scripts/ai.sh models dashscope` 成功，但多模态向量调用失败 | models list 只证明 Key 和 compatible endpoint 可访问，不证明目标模型支持该协议 | 检查模型是否需要 native `/api/v1` 服务路径 |
| OpenAI-compatible chat 能用，但以图搜图 embedding 失败 | chat 走 `/compatible-mode/v1`，多模态向量走 native `/api/v1` 服务路径 | 为以图搜图单独配置或推导 native `/api/v1` |
| OpenAI-compatible `/embeddings` 能用，但 `qwen3-vl-embedding` 失败 | OpenAI-compatible 支持文本向量，不支持多模态向量 | 多模态向量改用 native Multimodal Embedding API |
| `qwen3-vl-rerank` 放进 embedding 模型列表后失败 | rerank 不是 embedding 模型，endpoint 和请求体不同 | 单独走 `/services/rerank/text-rerank/text-rerank` |
| HTTP 401 / `invalid_api_key` | API Key 和 endpoint 地域或 workspace 不匹配 | 用同 region、同 workspace 的 Key 和 host |
| HTTP 400 / `Model not exist` | 当前 region、workspace 或协议下没有该模型名 | 换同区域存在的模型名，或检查是否需要国内/国际不同模型名 |
| HTTP 403 / `Model.AccessDenied` | Key 有效，但账号没有该模型权限 | 到控制台开通模型权限或更换已授权账号 |
| HTTP 400 / `Arrearage` 或 `AllocationQuota.FreeTierOnly` | 账号欠费、免费额度耗尽或只允许免费额度 | 检查账号计费状态和付费模式 |
| HTTP 404 | path 拼错，或把 native 服务路径拼到了 `/compatible-mode/v1` 后面 | 确认 base URL path 是 `/api/v1` 还是 `/compatible-mode/v1` |
| POC 报 `not compatible-mode/v1` | `POC_DASHSCOPE_BASE_URL` 填成了 OpenAI-compatible 地址 | 改成同 host 的 `/api/v1` |
| 向量维度不符合预期 | 模型默认维度或 `parameters.dimension` 不匹配 | 连通性探测不传维度；落库前再统一模型和维度 |

## 维护规则

- 新增 DashScope 普通文本、图片或 OpenAI-compatible embedding 能力时，优先沿用 `DASHSCOPE_BASE_URL`。
- 新增 DashScope 原生 API 能力时，明确写出 native `/api/v1` 的来源，不要把完整接口路径塞进 base URL。
- 文档示例只放占位 Key，不写真实密钥。
- 修改 provider、adapter 或 POC 配置读取规则后，同步更新本文和相关 README。

## 外部事实源

- 阿里云 Model Studio Base URL 总览：`https://help.aliyun.com/en/model-studio/base-url`
- 阿里云 Model Studio 地域和接入域名：`https://www.alibabacloud.com/help/en/model-studio/regions`
- OpenAI-compatible Embedding 说明：`https://www.alibabacloud.com/help/en/model-studio/embedding-interfaces-compatible-with-openai`
- Multimodal Embedding API 说明：`https://www.alibabacloud.com/help/en/model-studio/multimodal-embedding-api-reference`
- Text Rerank API 说明：`https://www.alibabacloud.com/help/en/model-studio/text-rerank-api`

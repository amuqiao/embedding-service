# AI 能力层 API 稳定性承诺

## 文档目的

本文档明确定义了小说本地化 AI 能力层的 API 合同（Contract），以及哪些是**长期稳定的**，哪些是**内部实现细节可变的**。

---

## 对外保证（Long-term Stable）

### ✅ 稳定的五大 API 接口

| 接口 | 方法 | 路径 | 稳定性 |
|---|---|---|---|
| 健康检查 | GET | `/health` | ✅ 永不变更 |
| 模型列表 | GET | `/models` | ✅ 添加新模型OK，不改ID |
| Prompt模板 | GET | `/prompt-templates` | ✅ 添加新job_type OK，不改字段 |
| 创建任务 | POST | `/jobs` | ✅ 请求体结构见下 |
| 查询任务 | GET | `/jobs/{job_id}` | ✅ 响应体结构见下 |

### ✅ 稳定的数据结构

#### POST /jobs 请求体结构

```json
{
  "client_request_id": "string (optional, 24h 幂等key)",
  "job_type": "novel_localization.step1_localize | step2_review | step3_translate",
  "model_id": "string (来自 /models)",
  "input": {
    "type": "text | oss_object",
    "content": "string (仅 text 类型)",
    "content_hash": "sha256:... (可选)",
    "oss_bucket": "string (仅 oss_object)",
    "oss_key": "string (仅 oss_object)",
    "oss_region": "string (仅 oss_object)"
  },
  "output": {
    "type": "oss_prefix (首版固定)",
    "oss_bucket": "string",
    "oss_prefix": "string",
    "oss_region": "string"
  },
  "callback": {
    "url": "https://...",
    "events": ["job.succeeded", "job.failed"] (可选)
  },
  "prompt": {
    "blocks": [
      {
        "key": "system | user | work_note",
        "role": "system | user",
        "content": "string (不允许 null)"
      }
    ]
  },
  "metadata": {
    "external_project_ref": "string (可选)",
    "external_job_ref": "string (可选)",
    "custom_field": "any (用于审计，AI层不处理)"
  }
}
```

**承诺**：
- `client_request_id` 不变 → 仍用于幂等去重
- `job_type` 枚举可扩展（新增 step4, step5），不删改现有值
- `input` 的三个字段（type, content, oss_*）保持结构不变
- `prompt.blocks[]` 的三个字段（key, role, content）保持不变
- `metadata` 的任意字段不会被 AI 层处理，只用于审计和日志关联

#### GET /jobs/{job_id} 响应体结构

```json
{
  "job_id": "uuid",
  "job_type": "string",
  "status": "queued | running | succeeded | failed | canceled",
  "progress_percent": 0-100,
  "progress_text": "string (可选，当前步骤文案)",
  "result": {
    "artifacts": [
      {
        "key": "localized_text | translated_text | review_summary | notes | ...",
        "type": "text | json | prompt_suggestion | ...",
        "label": "string (人类可读标签)",
        "storage": "oss_object (可选，大文本)",
        "oss_bucket": "string (仅 storage=oss_object)",
        "oss_key": "string (仅 storage=oss_object)",
        "oss_region": "string (仅 storage=oss_object)",
        "content_hash": "sha256:... (仅 storage=oss_object)",
        "content_size_bytes": 123456,
        "content": "string | object (仅小文本)",
        "target": {
          "job_type": "novel_localization.step1_localize",
          "prompt_block_key": "work_note",
          "default_mode": "append | replace"
        }
      }
    ],
    "signals": {
      "passed": true | false,
      "merge_scan_applied": true | false,
      "project_memory_frozen": true | false,
      "custom_signal": "any (未来扩展)"
    }
  },
  "error": {
    "code": "MODEL_CALL_FAILED | JOB_TIMEOUT | ... (23 个错误码)",
    "message": "string",
    "details": "object"
  },
  "created_at": "iso8601",
  "started_at": "iso8601 (nullable)",
  "finished_at": "iso8601 (nullable)"
}
```

**承诺**：
- `job_id`, `job_type`, `status`, `progress_percent` 永不变更
- `result` 的结构 `{ artifacts[], signals{} }` 永不变更
- `artifacts[].key` 的标准值（localized_text、translated_text 等）永不变更
- `artifacts[].storage` 的取值（oss_object | content）永不变更
- `signals` 可按需添加新字段（如 quality_score），不删改现有字段
- `error.code` 可添加新错误码，不删改现有码

### ✅ 稳定的 Callback 通知格式

```json
{
  "event": "job.succeeded | job.failed",
  "job_id": "uuid",
  "job_type": "string",
  "status": "succeeded | failed",
  "result": { "artifacts": [...], "signals": {...} },
  "error": { "code": "...", "message": "...", "details": {...} },
  "metadata": { /* 原始请求中的 metadata */ },
  "finished_at": "iso8601"
}
```

**承诺**：
- Callback 请求头的三个签名字段（X-AI-Service-Job-Id、X-AI-Service-Event、X-AI-Service-Timestamp、X-AI-Service-Signature）永不变更
- HMAC-SHA256 签名算法永不变更
- 重试策略（3 次，间隔 10s/30s/60s）永不变更

---

## 可能变化的（内部实现细节）

后端**不应该依赖**以下内容，因为它们属于 AI 层内部实现选项，可能在 minor 版本内改变：

| 项目 | 原因 | 示例 |
|---|---|---|
| Job 执行时是否分块、分多少块 | 取决于输入大小和算法优化 | 10KB 输入可能分 2 块或 3 块 |
| 是否使用 Celery Canvas、Chord、Group | 取决于执行策略 | 从 Canvas 改为直接任务链 |
| 并行度配置 | 取决于资源可用性 | 从 5 个并行块改为 8 个 |
| Executor 如何解析 LLM 输出 | 取决于 LLM 稳定性改进 | 从正则表达式改为 JSON Schema 解析 |
| progress_text 的具体文案 | 取决于 UI 需求 | "正在执行 chunk" → "处理第 3 块" |
| work_items 表的中间状态 | 取决于审计需求 | 可能删除某些调试字段 |
| Callback 重试的实际时间 | 取决于网络状况 | 可能改为 5s/15s/45s |

**后端的应对**：
- 不检查 `progress_text` 的具体文案，仅用 `progress_percent` 判断进度
- 不查询 `work_items` 表，仅查询 `jobs` 表
- 不假设 Job 的执行顺序或中间状态
- 仅在 Job 进入终态（succeeded/failed）后作出决策

---

## 版本管理规则

### 主版本（v1 → v2）：API 破坏性变更
- 新增必填字段 → 主版本
- 删除或改名现有字段 → 主版本
- 改变 HTTP 状态码语义 → 主版本

### 次版本（v1.0 → v1.1）：非破坏性扩展
- 新增可选字段 → 次版本
- 新增 job_type → 次版本
- 新增错误码 → 次版本
- 新增 artifact.key → 次版本
- 新增 signal 字段 → 次版本

### 补丁版本（v1.0.0 → v1.0.1）：内部实现改进
- 改进分块算法 → 补丁版本
- 优化 Canvas 拓扑 → 补丁版本
- 修复 Executor 的输出解析 → 补丁版本

---

## 扩展示例

### 新增 job_type 而不破坏 API

假设未来需要新增"翻译校验"能力（step4_translate_review）。

**步骤**：
1. 在 `/prompt-templates` 中新增模板
2. 在 POST /jobs 接收并验证新 job_type
3. 定义新的 result.signals（如 translation_quality_ok: bool）
4. 定义新的 artifact（如 translation_quality_report）

**后端改动**：零。仍然调用 POST /jobs，唯一的改动是 job_type 从 step3 改为 step4。

**API 合同**：不变。

### 新增 Prompt block 而不破坏 API

假设 step2_review 需要添加一个"上下文窗口"block（context）。

**步骤**：
1. 在 step2_review 的模板中新增 block（key: context, role: user）
2. 业务后端在创建 step2 Job 时，在 prompt.blocks 中补充此 block

**后端改动**：选择性的。如果不填 context block，AI 层仍能工作。

**API 合同**：不变。仍然是 prompt.blocks[]。

---

## 总结

- ✅ **API 层**：永远稳定，按语义化版本管理
- ✅ **数据结构**：永远稳定，可按需扩展（新字段）
- ⚠️ **内部实现**：自由变化，对后端完全透明

后端只需依赖上述"对外保证"中的内容，其余都是 AI 层的内部事务。

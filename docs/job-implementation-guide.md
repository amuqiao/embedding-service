# cms-novel-localize Job 系统实施说明

本文是 `async-job-spec.md` 通用规范在本项目的落地说明。规范中有大量可选项和分支，本文只记录**本项目实际启用的内容**，以及每处选择的依据，方便开发和运维直接对照。

**适用场景**：本地开发排障、生产运维配置、新成员了解 Job 系统。
**不适用**：通用架构决策、跨项目迁移（查 `async-job-spec.md`）。

---

## 一、选择摘要（一览表）

| 维度 | 本项目选择 | 规范对应章节 |
|---|---|---|
| 执行模式 | Single（默认）；Chunked 可通过配置启用 | §2.1 / §2.2 |
| 恢复机制 | Worker 启动扫描 + Worker 内置定期扫描 | §7.1 / §7.3 |
| 进程内定期扫描 | **已启用**，由 `JOB_RECOVERY_INTERVAL_SECONDS` 控制 | §7.3 |
| 积压上限 | MAX_ACTIVE_JOBS=5000；0 可禁用 | §4.3 |
| 数据保留 | 24h 过期标记 + Worker recovery loop 周期清理 | §13 |
| Celery Pool | solo（本地），threads（生产） | — |
| 对象存储 | local（开发），aliyun_oss（生产） | — |
| Callback 签名 | HMAC-SHA256，密钥来自 `CALLBACK_SIGNING_SECRET` | §8.2 |

---

## 二、Job 类型

本项目固定支持三个 job_type，由 `app/infrastructure/novel_loc/prompts.yaml` 定义。

### 2.1 novel_localization.step1_localize（本地化）

| 项 | 说明 |
|---|---|
| 输入 | 原文中文小说（OSS 对象，UTF-8） |
| Prompt blocks | `user`（本地化方法论）、`work_note`（上一轮约束，可为空） |
| 输出 artifact | `localized_text`（中文本地化稿，OSS 写入）、`work_note`（本次模型输出的工作注释，inline） |
| work_note apply_mode | `replace`（直接替换，不与旧注释合并） |
| signals | `{}` |
| 重跑时 | source 换为上一次 step1 输出的 localized_text OSS key；work_note 换为 step2 返回的建议注释 |

模型输出格式（由 output_contract 约束）：
```
===工作注释开始===
...注释内容...
===工作注释结束===

===本地化正文开始===
...中文本地化正文...
===本地化正文结束===
```

### 2.2 novel_localization.step2_review（校验）

| 项 | 说明 |
|---|---|
| 输入 | step1 输出的 localized_text（OSS 对象） |
| Prompt blocks | `user`（校验标准）、`work_note`（当前工作注释） |
| 输出 artifact | `review_summary`（校验结果说明，inline）；**仅 passed=false 时**额外返回 `work_note`（建议注释，inline） |
| signals | `{"passed": true/false}` |
| 语义 | `status=succeeded` 表示 AI Job 成功，`signals.passed=false` 表示校验未通过（非错误） |

### 2.3 novel_localization.step3_translate（翻译）

| 项 | 说明 |
|---|---|
| 输入 | step1 输出的 localized_text（OSS 对象） |
| Prompt blocks | `user`（翻译标准）、`work_note`（可为空） |
| 输出 artifact | `translated_text`（英文终稿，OSS 写入） |
| signals | `{}` |

---

## 三、执行模式

### 3.1 Single 模式（当前默认）

```
process_job_task(job_id)
  → mark_running
  → load_input_text (OSS 读取)
  → asyncio.wait_for(run_ai_job(), timeout=MODEL_CALL_TIMEOUT_SECONDS)  ← L1
  → persist_large_artifacts (OSS 写入 localized.txt / translated.txt)
  → mark_succeeded
  → deliver_callback
```

控制开关：`NOVEL_LOCALIZATION_CHUNKING_ENABLED=false`（默认）。

### 3.2 Chunked 模式（可选，已实现但默认关闭）

当 `NOVEL_LOCALIZATION_CHUNKING_ENABLED=true` 且输入字符数 > `NOVEL_LOCALIZATION_SINGLE_MAX_CHARS` 时触发：

- 按 `NOVEL_LOCALIZATION_CHUNK_SIZE` 字符切分
- step1 额外生成 memory WorkItem（人物/地名/术语一致性）
- step3 额外生成 scan WorkItem（最终合并扫描）
- WorkItem 结果汇总后进入 finalize，对外仍返回单一 Job 状态

**本地开发不建议启用**，模型成本高且恢复逻辑更复杂。

---

## 四、超时链

超时链由一个锚点和三个 buffer 派生。用户不直接配置 L3/L4/L5 绝对值：

```
L1  MODEL_CALL_TIMEOUT_SECONDS
L3  celery_soft_time_limit      = L1 + CELERY_SOFT_TIMEOUT_BUFFER_SECONDS
L4  celery_time_limit           = L3 + CELERY_HARD_TIMEOUT_BUFFER_SECONDS
L5  job_stale_running_seconds   = L4 + JOB_STALE_RUNNING_BUFFER_SECONDS
```

本项目默认值：

| 层 | 变量 | 默认值 | 作用 |
|---|---|---|---|
| L1 | `MODEL_CALL_TIMEOUT_SECONDS` | 600s | `asyncio.wait_for` 截断 LLM 调用，超时 → `JOB_TIMEOUT` |
| L3 buffer | `CELERY_SOFT_TIMEOUT_BUFFER_SECONDS` | 300s | 派生 Celery soft time limit |
| L4 buffer | `CELERY_HARD_TIMEOUT_BUFFER_SECONDS` | 60s | 派生 Celery hard time limit |
| L5 buffer | `JOB_STALE_RUNNING_BUFFER_SECONDS` | 600s | 派生 recovery stale running 阈值 |

L1/L3 超时均触发 `JOB_TIMEOUT` 错误码，由 `CELERY_MAX_RETRIES` 统一控制重试次数（默认 0 不重试）。

---

## 五、恢复机制

### 5.1 Worker 启动扫描（必选，已实现）

Worker 进程就绪后通过 `worker_ready` 信号自动触发一次扫描（`app/tasks/recovery.py`）：

**孤儿 queued Job 重投递**（`celery_task_id IS NULL` 且 `created_at < now - JOB_ORPHAN_TIMEOUT_SECONDS`）：

```
pre-generate task_id
→ CAS: UPDATE ... SET celery_task_id=<id> WHERE celery_task_id IS NULL
→ 成功：apply_async 投递（仅胜者投递，防多 Worker 重复）
→ 失败：跳过（另一 Worker 已抢占）
```

**僵死 running Job 强制失败**（`started_at < now - settings.job_stale_running_seconds`）：

```
→ mark_failed(code=JOB_TIMEOUT)
→ deliver_callback
```

### 5.2 Worker 内置定期扫描（已实现）

Worker 启动后会运行内置 recovery loop，周期由 `JOB_RECOVERY_INTERVAL_SECONDS` 控制。每轮扫描会处理 orphan queued、未确认发布、stale running、callback 补偿和 `expires_at <= now()` 的 Job 清理。

多 Worker Pod 同时运行时，每轮扫描通过 PostgreSQL advisory lock 做全局单飞，不需要启动 Celery Beat。

### 5.3 Celery Beat（当前不使用）

本项目当前生产形态只要求 API 和 Worker 两类服务。`jobs.cleanup_expired` 仍保留为可手动调用的 Celery task，但默认不依赖 Celery Beat。

---

## 六、积压控制

`MAX_ACTIVE_JOBS` 控制 `POST /jobs` 入口的背压：

```
MAX_ACTIVE_JOBS = 0        → 禁用检查，队列可无限积压（内部系统使用）
MAX_ACTIVE_JOBS = 5000     → 默认，queued+running 总数 ≥ 5000 时返回 503
```

**注意**：这是 best-effort 软限制，多 API Pod 并发时实际积压可能短暂超出，属预期行为。精确限制需改用 DB 行锁或 Redis 原子计数器，但本项目暂无必要。

---

## 七、对象存储

| 环境 | `STORAGE_BACKEND` | 说明 |
|---|---|---|
| 本地开发 | `local` | 读写 `LOCAL_OBJECT_STORAGE_PATH`（默认 `storage/objects/`） |
| 生产 | `aliyun_oss` | 读写阿里云 OSS，bucket/region/AK 由 env 配置 |

**输入对象**：调用方只传 `oss_key`，AI 能力层用自身配置的 bucket+凭证读取。
**输出对象**：AI 能力层按 `OSS_OUTPUT_PREFIX/{job_id}/` 前缀写入，`localized_text` → `localized.txt`，`translated_text` → `translated.txt`。

---

## 八、数据保留

```
Job 创建时刻 T0
T0 ~ T0+24h      → expires_at 标记过期；仍可通过 GET /jobs/{id} 查询
T0+24h 后        → Worker recovery loop 删除 expires_at <= now() 的记录
```

业务后端应在收到 Callback 后立即保存结果，不依赖 AI 能力层做长期存储。

---

## 九、错误码

| code | 触发场景 |
|---|---|
| `INVALID_JOB_TYPE` | job_type 不在支持列表 |
| `MODEL_NOT_AVAILABLE` | model_id 不存在或 enabled=false |
| `INVALID_INPUT` | 参数格式错误、编码错误、prompt.blocks 校验失败 |
| `INPUT_TOO_LARGE` | OSS 对象读取后超过 `OSS_INPUT_MAX_BYTES`（默认 5MB） |
| `INPUT_HASH_MISMATCH` | content_hash 校验失败 |
| `OSS_OBJECT_NOT_FOUND` | OSS 对象不存在 |
| `OSS_FETCH_FAILED` | OSS 对象读取失败 |
| `OSS_WRITE_FAILED` | OSS 对象写入失败 |
| `JOB_NOT_FOUND` | job_id 不存在（已过期或从未创建） |
| `MODEL_CALL_FAILED` | 模型调用失败（非超时，包括通用内部异常） |
| `MODEL_OUTPUT_INVALID` | 模型输出不符合 output_contract（缺少标记、疑似拒绝等） |
| `JOB_TIMEOUT` | L1/L3 超时，或 L5 扫描强制终止僵死 running Job |
| `QUEUE_FULL` | queued+running ≥ MAX_ACTIVE_JOBS，HTTP 503 |

---

## 十、关键环境变量速查

### 必填

| 变量 | 说明 |
|---|---|
| `DATABASE_URL` | PostgreSQL 连接串 |
| `SERVICE_API_KEY` | Bearer Token，调用方鉴权 |
| `CALLBACK_SIGNING_SECRET` | Callback HMAC-SHA256 签名密钥 |
| `OPENAI_API_KEY` | 模型调用密钥 |

### 超时链（L3/L4/L5 由 buffer 派生）

| 变量 | 默认 | 说明 |
|---|---|---|
| `MODEL_CALL_TIMEOUT_SECONDS` | 600 | L1 asyncio.wait_for 截断 |
| `CELERY_SOFT_TIMEOUT_BUFFER_SECONDS` | 300 | L3 相对 L1 的 buffer |
| `CELERY_HARD_TIMEOUT_BUFFER_SECONDS` | 60 | L4 相对 L3 的 buffer |
| `JOB_STALE_RUNNING_BUFFER_SECONDS` | 600 | L5 相对 L4 的 buffer |

### 积压与恢复

| 变量 | 默认 | 说明 |
|---|---|---|
| `MAX_ACTIVE_JOBS` | 5000 | 入口积压上限，0=禁用 |
| `JOB_ORPHAN_TIMEOUT_SECONDS` | 300 | queued+task_id=NULL 超时视为孤儿 |
| `CELERY_MAX_RETRIES` | 0 | L1/L3 超时重试次数 |

### 分块（可选）

| 变量 | 默认 | 说明 |
|---|---|---|
| `NOVEL_LOCALIZATION_CHUNKING_ENABLED` | false | 是否启用分块模式 |
| `NOVEL_LOCALIZATION_SINGLE_MAX_CHARS` | 20000 | Single 模式字符上限 |
| `NOVEL_LOCALIZATION_CHUNK_SIZE` | 3000 | 分块目标字符数 |

---

## 十一、运维速查

| 现象 | 排查方向 |
|---|---|
| `POST /jobs` 返回 503 | 检查 `queued+running` 总数是否达到 `MAX_ACTIVE_JOBS`；扩 Worker Pod 或临时调高限制 |
| Job 长期停留在 queued | 检查 Worker 是否存活；Redis 队列是否有消息（`LLEN celery`）；查 `.run/worker.pid` 和 `logs/worker.log` |
| Job 长期停留在 running | 检查 Worker 日志；派生的 `job_stale_running_seconds` 到期后恢复扫描会强制 failed |
| 收不到 Callback | 检查 `CALLBACK_SIGNING_SECRET` 和 `ALLOW_INSECURE_CALLBACKS`；查 worker 日志中的 callback 重试记录 |
| 模型输出 `MODEL_OUTPUT_INVALID` | 检查 `prompts.yaml` 的 output_contract 标记是否与 `executor.py` 解析规则一致 |
| OSS 写入失败 | 检查 `OSS_BUCKET`、`OSS_ACCESS_KEY_ID/SECRET`、endpoint 配置；确认 bucket 权限 |
| Worker 重启后任务重跑 | 正常行为（路径 A，`acks_late` + `reject_on_worker_lost`）；检查 `terminationGracePeriodSeconds` ≥ 派生的 `celery_time_limit` + 60s |
| 测试验证 | `./scripts/dev.sh check`（语法+pytest）；完整链路：`./scripts/dev.sh mock-smoke` |

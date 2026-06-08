# 内部实现文档

本目录说明 AI 能力层**如何在内部实现 Job 执行**，包括不同的执行策略、并行处理、中间扫描等。**所有内容对后端完全透明**。

## 文件说明

| 文档 | 用途 | 受众 | 内容 |
|---|---|---|---|
| **内部实现选项指南.md** | 🔧 内部架构参考 | AI 能力层开发者 | P1/P5/扫描三种模式、work_items 生命周期、监控指标 |
| **扩展示例_新增Job类型.md** | 🔧 扩展指南 | AI 能力层架构师 | 如何添加新 job_type 而不破坏 API 的完整示例 |

**注意**：本目录文档仅供 AI 能力层团队内部使用，**不发给后端**。  
后端团队不需要了解内部如何分块、使用什么 Canvas 拓扑等实现细节。

## 核心设计原则

### 对后端完全隐形

后端的视角：
```
输入文本 200KB
    ↓
POST /jobs
    ↓
[后端轮询]
    ↓
GET /jobs/{job_id}
    ↓
status = succeeded, result = {artifacts, signals}
```

AI 能力层的视角（后端无需知道）：
```
输入文本 200KB → 分块 → 生成映射表 → 并行处理 N 个块
    → 合并 N 个块 → 扫描一致性 → 返回结果
```

**关键约定**：后端不查询 work_items、不假设分块数、不依赖 progress_text 的具体文案。

---

## 三种执行模式

### 模式 1：P1（单步执行）

**触发条件**：输入 < 5000 字符

**流程**：
```
输入（< 5000 字符）
    ↓
单个 Celery task
    ↓
单次 LLM 调用
    ↓
直接返回结果
```

**特点**：
- 无分块，无 merge
- 速度快（单个 task）
- 无一致性问题

**代码位置**：`app/services/job_planner.py:_build_plan_p1()`

---

### 模式 2：P5（分块执行）

**触发条件**：输入 >= 5000 字符

**流程**（step1 为例）：
```
输入（≥ 5000 字符）
    ↓
自动分块（按段落，≈4500 字符/块）
    ↓
生成映射表（memory task）
    ↓
并行处理各块（chord）
    ↓
合并结果（merge task）
    ↓
返回结果
```

**Canvas 拓扑**：
```python
chain(
    execute(memory:0),      # 串行：生成映射表
    chord(
        [execute(chunk:1), execute(chunk:2), ...],  # 并行：处理各块
        execute(merge)      # 汇合：合并结果
    )
)
```

**特点**：
- 分块算法：段落为单位，不切断段落
- 并行度：由 Celery worker 数决定（通常 2-8）
- 映射表：冻结在 merge 阶段，可选返回给后端

**代码位置**：
- 规划：`app/services/job_planner.py:_build_plan_p5()`
- Canvas：`app/services/job_workflow.py:build_canvas()`
- 合并：`app/services/job_workflow.py:merge_work_items()`

---

### 模式 3：扫描模式（仅 step3）

**触发条件**：step3 + P5 模式 + 块数 > 1

**额外流程**：
```
各块并行翻译完成
    ↓
merge 汇总各块英文
    ↓
scan task：检查一致性
  - 人名是否统一（"Shen Yan" vs "沈砚"）
  - 术语是否一致（"元婴" 的翻译）
  - 分块边界是否衔接
    ↓
返回修订后的完整英文
```

**特点**：
- 额外的 LLM 调用，但轻量级（扫描 prompt）
- 确保翻译的全局一致性
- 自动进行，后端无需关心

**代码位置**：`app/services/job_workflow.py:_first_scan_payload()`

---

## 参数配置

```python
# config.py

# 分块阈值
NOVEL_LOCALIZATION_P1_MAX_CHARS = 5000  # < 此值用 P1

# 分块大小
NOVEL_LOCALIZATION_CHUNK_SIZE = 4500  # 每块目标字符数

# Celery 并行度
CELERY_CONCURRENCY = 4  # app.conf.worker_concurrency

# 超时设置
JOB_QUEUE_TIMEOUT_SECONDS = 600  # 排队超时
JOB_EXECUTION_TIMEOUT_SECONDS = 1800  # 执行超时
```

### 这些配置是否会影响后端 API？

**❌ 否**。完全是内部实现细节。

后端永远调用同样的 5 个 API，无需修改。

---

## Project Memory（映射表）

### 何时生成

仅在 **step1_localize 的 P5 模式**下生成。

### 内容示例

```json
{
  "characters": [
    {"cn_name": "沈砚", "en_name": "Shen Yan", "title": "protagonist"},
    {"cn_name": "赵虎", "en_name": "Tiger Zhao", "title": "antagonist"}
  ],
  "places": [
    {"cn_name": "青牛镇", "en_name": "Green Ox Town"}
  ],
  "glossary": [
    {"cn_term": "元婴", "en_term": "Immortal Embryo"}
  ],
  "style_guide": "保留原名拼音，避免过度美国化",
  "cultural_rules": [...]
}
```

### 生命周期

1. **生成**：memory task 执行，调用 LLM 生成映射表
2. **冻结**：在 merge step1 时，将映射表返回为 artifact（如有）
3. **注入**：后续 step2、step3 的 chunk tasks 可读取并遵守映射表

### 后端如何使用

**可选**：
- 保存到业务库（用于审核）
- 在 step2/step3 的 work_note 中注入（加强约束）
- 用于人工审核（查看 AI 的判断）

---

## Work Items（任务项）

### 结构

```python
# ai_job_work_items 表

job_id          # 关联的 job_id
name            # "memory:0" / "chunk:1" / "merge:2" / "scan:3"
kind            # "memory" / "chunk" / "merge" / "scan" / "whole"
chunk_index     # 块编号（仅 chunk/merge/scan）
status          # "queued" / "running" / "succeeded" / "failed"
celery_task_id  # Celery 任务 ID（用于追踪）
input_payload   # 输入内容
result_payload  # 输出结果（JSON）
error_payload   # 错误信息（仅失败时）
```

### 后端是否应该查询 work_items？

**❌ 不应该**。

- work_items 是内部实现细节
- 后端仅应查询 `jobs` 表
- 如需排障，通过日志和内部工具查看（不通过 API）

---

## 监控和可观测性

### 后端应该观测的指标

```python
# 来自 GET /jobs/{job_id}
job = {
    'job_id': '...',
    'status': 'running',
    'progress_percent': 30,
    'progress_text': '正在执行 chunk',
    'created_at': '...',
    'started_at': '...',
    'finished_at': None
}
```

### 后端不应该依赖的指标

- ❌ progress_text 的具体文案（可能改变）
- ❌ job.started_at - job.created_at 的精确值（波动）
- ❌ Work Items 的中间状态
- ❌ Celery task 数量或 IDs

---

## 扩展点

### 未来可能的改进

| 改进 | 影响 | API 破坏性 |
|---|---|---|
| 分块算法改进（NLP 分句） | 分块数/大小改变 | ❌ 无 |
| Canvas 优化（Group + Loop） | 执行顺序改变 | ❌ 无 |
| 并行度动态调整 | 不同输入的并行度不同 | ❌ 无 |
| 增加中间扫描（step2） | 新增 progress 阶段 | ❌ 无 |
| 改进 LLM 调用（缓存、batch） | 执行时间改变 | ❌ 无 |
| 支持其他 LLM 厂商 | ai_gateway 改变 | ❌ 无 |

**结论**：所有内部优化都**不需要**后端改动 API 调用方式。

---

## 最佳实践

### 后端在设计时应该

✅ 依赖 5 个 API 接口的 request/response 格式  
✅ 依赖 `result.artifacts[]` 和 `result.signals{}`  
✅ 依赖 `error.code` 和错误码清单  
✅ 依赖 `job_id`、`status`、`progress_percent`  
✅ 实现轮询逻辑（2-5 秒间隔）  

### 后端在设计时不应该

❌ 硬编码 `progress_text` 值  
❌ 查询 `work_items` 表  
❌ 假设 Job 的执行顺序（并行无序）  
❌ 依赖 `progress_percent` 的具体阶段值（如必定 30%）  
❌ 依赖分块数或块大小（内部参数）

---

## 相关文档

- 📚 接口规范：`../接口层/小说本地化AI能力层接口文档v3.md`
- 📖 设计承诺：`../设计承诺/AI能力层API稳定性承诺.md`
- 📋 后端对接：`../后端对接/后端对接清单.md`

# 设计承诺文档

本目录说明 AI 能力层向业务后端做出的设计保证，包括 API 稳定性原则和核心概念的深度解释。

## 文件说明

| 文档 | 用途 | 受众 | 何时阅读 |
|---|---|---|---|
| **AI能力层API稳定性承诺.md** | 明确 API 永久稳定什么、内部实现可变什么，包含版本管理规则 | AI 团队、PO、架构师 | 设计新功能时参考 |
| **Work_note与优化建议的角色.md** | 🤝 **给后端用** — 详解 work_note 的两个角色和重跑流程，含三行代码快速参考 | 业务后端团队 | 实现 step2 失败重跑时 |

## 核心承诺

### ✅ 对外保证（Long-term Stable）

#### 稳定的五大接口
```
GET  /health
GET  /models
GET  /prompt-templates
POST /jobs
GET  /jobs/{job_id}
```

#### 稳定的数据结构
- 请求体：`{client_request_id, job_type, model_id, input, output, callback, prompt, metadata}`
- 响应体：`{job_id, status, progress_percent, result{artifacts[], signals{}}, error, timestamps}`
- Callback：`{event, job_id, job_type, status, result, error, metadata, finished_at}`

#### 版本管理规则
- **主版本（v1 → v2）**：破坏性变更（新增必填字段、删改现有字段）
- **次版本（v1.0 → v1.1）**：非破坏性扩展（新增 job_type、新增 artifact.key、新增 signal）
- **补丁版本（v1.0.0 → v1.0.1）**：内部优化（分块算法改进、Canvas 拓扑优化）

### ⚠️ 可能变化（内部实现细节）

后端**不应该依赖**：
- ❌ Job 是否分块、分多少块
- ❌ 是否使用 Celery Canvas、Chord、Group
- ❌ 并行度配置
- ❌ progress_text 的具体文案
- ❌ work_items 表的中间状态
- ❌ Executor 的输出解析方式

**应对方式**：仅依赖 5 个 API 的 request/response 格式，不检查内部实现细节。

---

## 核心概念

### work_note 的两个角色

#### 角色 1：业务后端的输入（Context 传递）
```python
# 首次调用 step1_localize
prompt.blocks = [
    {'key': 'work_note', 'role': 'user', 'content': ''}  # 空或业务说明
]

# 根据校验失败的建议重跑
prompt.blocks = [
    {'key': 'work_note', 'role': 'user', 'content': optimization_prompt.content}  # 注入建议
]
```

#### 角色 2：AI 层的建议输出（Suggestion 消费）
```python
# step2_review 失败时返回
result.artifacts = [
    {
        'key': 'optimization_prompt',
        'type': 'prompt_suggestion',
        'content': '请重新本地化时统一人物称呼...',
        'target': {
            'job_type': 'novel_localization.step1_localize',
            'prompt_block_key': 'work_note',
            'default_mode': 'append'  # 或 replace
        }
    }
]
```

### 迭代闭环

```
第一轮：
  POST /jobs (step1_localize, work_note='')
  → POST /jobs (step2_review)
  → signals.passed = false, optimization_prompt = {...}

第二轮（重跑）：
  POST /jobs (step1_localize, work_note=optimization_prompt.content)
  → POST /jobs (step2_review)
  → signals.passed = true
  → POST /jobs (step3_translate)
```

---

## 快速参考

### 重跑流程（三行代码）
```python
# 1. 从 step2 结果中提取建议
opt = next(a for a in step2['artifacts'] if a['key'] == 'optimization_prompt')

# 2. 重跑 step1，注入建议到 work_note
step1_retry = post_job('step1_localize', prompt_blocks=[..., 
    {'key': 'work_note', 'role': 'user', 'content': opt['content']}])
```

### 关键点
✅ `job_type` 保持不变（仍为 `step1_localize`）  
✅ 仅修改 `prompt.blocks` 中 `work_note` 的 `content`  
✅ 直接使用 `optimization_prompt.content`，无需再处理

---

## 设计原理

### 为什么这样设计？

**原则**：解耦 AI 生成 vs 业务消费，给业务层充分控制权

1. **AI 层只负责生成建议**
   - 返回 `optimization_prompt` artifact
   - 不强制执行重跑

2. **业务后端负责审核和决策**
   - 可以展示建议给用户
   - 可以修改建议再提交
   - 可以限制重试次数

3. **支持多轮迭代和人工干预**
   - 既可用 AI 建议重跑
   - 也可用人工反馈重跑
   - 可以累积多个建议或仅保留最新建议

4. **避免 AI 层过度"聪明"**
   - 不自动重跑
   - 不自动修改 prompt
   - 明确的边界和职责

---

## 常见问题

**Q: work_note 可以为空吗？**  
A: 可以。如果没有额外的上下文指导，传空字符串即可。AI 层会使用默认的 system prompt。

**Q: optimization_prompt 的内容是否会自动追加？**  
A: 否。AI 层只返回建议，业务后端负责决定追加还是替换。这样设计给你审核的机会。

**Q: step2_review 和 step3_translate 需要 work_note 吗？**  
A: 可传可不传。虽然 API 要求三个 block，但：
- step2_review：work_note 通常为空或包含上一步的说明
- step3_translate：work_note 通常为空，翻译主要依赖本地化结果

**Q: work_note 会无限增长吗？**  
A: 可能会。建议：
- 只保留最新的有效建议（replace mode）
- 或限制 work_note 长度（如 2KB）
- 或每次清空后只保留最新建议

---

## 扩展示例

### 如何添加新 job_type？

假设要新增 `step4_translate_review`（翻译质量校验）。

#### 第一步：定义模板
```python
# app/infrastructure/prompt_templates.py
JOB_TEMPLATES = [
    JobTypeTemplate(
        job_type="novel_localization.step4_translate_review",
        name="英文翻译校验",
        prompt_blocks=_blocks(
            system="你是英文翻译质量审核编辑...",
            user="请审核以下英文翻译质量，输出：【审核结论】通过/不通过...",
        ),
    ),
]
```

#### 第二步：实现执行逻辑
```python
# app/services/executor.py
if job_type == "novel_localization.step4_translate_review":
    passed, summary, optimization = _parse_step2_output(output)  # 复用解析逻辑
    return JobResult(
        artifacts=[
            {"key": "translation_quality_report", "content": summary},
            {"key": "translation_optimization_prompt", "content": optimization, ...}
        ],
        signals={"translation_quality_passed": passed}
    )
```

#### 第三步：后端如何使用？

**无需改动 API 调用**：
```python
# 后端代码几乎一样
result = post_job('novel_localization.step4_translate_review', ...)  # 仅这里改了 job_type
if result['signals']['translation_quality_passed']:
    # 翻译质量通过
else:
    # 根据建议重跑 step3
    opt = next(a for a in result['artifacts'] if a['key'] == 'translation_optimization_prompt')
    step3_retry = post_job('novel_localization.step3_translate', work_note=opt['content'])
```

**结论**：新增 job_type 是**非破坏性扩展**（次版本）。后端改动最小化。

---

## 相关文档

- 📚 详细接口规范：`../接口层/小说本地化AI能力层接口文档v3.md`
- 🔧 内部实现透明说明：`../内部实现/内部实现选项指南.md`
- 📋 后端对接清单：`../后端对接/后端对接清单.md`

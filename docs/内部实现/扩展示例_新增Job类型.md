# 扩展示例：新增 Job 类型而不破坏 API

## 文档目的

本文档通过一个**完整的端到端示例**展示如何在保持 API 契约不变的前提下，为 AI 能力层添加新的 job_type。

---

## 场景：新增"翻译校验"能力

假设业务需求演进：

**当前能力**：
1. step1_localize：本地化
2. step2_review：校验本地化稿
3. step3_translate：翻译

**新增需求**：
- step4_translate_review：校验英文翻译质量（新增）

业务流变为：
```
step1_localize → step2_review → step3_translate → step4_translate_review
```

---

## 实现步骤

### 步骤 1：定义新 Job 模板（AI 能力层）

编辑 `app/infrastructure/prompt_templates.py`，添加新 job_type：

```python
JOB_TEMPLATES = [
    # ... 现有的三个模板 ...
    
    JobTypeTemplate(
        job_type="novel_localization.step4_translate_review",  # ← 新增
        name="英文翻译校验",
        description="检查英文翻译的质量，包括术语一致性、语法、流畅度。",
        prompt_blocks=_blocks(
            system="你是一位英文翻译质量审核编辑，负责检查小说英文翻译的准确性、一致性和流畅度。"
                   "审核标准包括：1) 术语一致性；2) 语法正确性；3) 文化适切性；4) 人名/地名的拼写一致；5) 分段结构是否保留。",
            user="请审核以下英文翻译质量。输出格式如下："
                 "\n\n【审核结论】通过"
                 "\n\n（若不通过，追加：）"
                 "\n【问题说明】{问题列表}"
                 "\n【优化建议】{改进提示词片段}"
                 "\n\n请基于实际审核结果判断是否通过。"
        ),
    ),
]
```

### 步骤 2：实现执行逻辑（AI 能力层）

编辑 `app/services/executor.py`，在 `run_ai_job()` 中补充 step4 的处理：

```python
def run_ai_job(job_type: str, model_id: str, prompt_payload: dict, input_text: str) -> JobResult:
    # ... 现有逻辑 ...
    
    if job_type == "novel_localization.step4_translate_review":
        passed, review_summary, optimization_prompt = _parse_step2_output(input_text)
        # ↑ 复用 step2 的解析逻辑（【审核结论】的格式相同）
        
        return JobResult(
            artifacts=[
                {
                    "key": "translation_quality_report",
                    "type": "text",
                    "label": "翻译质量报告",
                    "content": review_summary,
                },
                {
                    "key": "translation_optimization_prompt",
                    "type": "prompt_suggestion",
                    "label": "改进建议 Prompt",
                    "content": optimization_prompt,
                    "target": {
                        "job_type": "novel_localization.step3_translate",
                        "prompt_block_key": "work_note",
                        "default_mode": "append",
                    },
                },
            ],
            signals={"translation_quality_passed": passed},
        )
    
    raise KeyError(job_type)
```

### 步骤 3：支持新 job_type 的工作流规划（如需）

编辑 `app/services/job_planner.py`，补充 step4 的规划逻辑：

```python
def build_job_plan(job_type: str, text: str) -> JobPlan:
    # 检查是否需要分块
    char_count = _count_chars(text)
    
    if job_type == "novel_localization.step4_translate_review":
        # step4 也可能很长，支持分块
        if char_count <= settings.NOVEL_LOCALIZATION_P1_MAX_CHARS:
            # P1：单块
            return JobPlan(
                execution_mode="p1",
                chunk_count=1,
                work_items=[PlannedWorkItem(name="whole", kind="whole", chunk_index=0)],
                chunk_registry=[],
            )
        else:
            # P5：分块并行校验
            chunks = split_text_with_registry(text, max_chars=settings.NOVEL_LOCALIZATION_CHUNK_SIZE)
            work_items = [
                PlannedWorkItem(name=f"chunk_{i}", kind="chunk", chunk_index=chunk['chunk_index'])
                for chunk in chunks
            ]
            work_items.append(
                PlannedWorkItem(name="merge_final", kind="merge", chunk_index=len(chunks) + 1)
            )
            return JobPlan(
                execution_mode="p5",
                chunk_count=len(chunks),
                work_items=work_items,
                chunk_registry=[{**chunk, 'text': None} for chunk in chunks],  # 不存储大文本
            )
    
    # ... 现有逻辑 ...
```

### 步骤 4：补充合并逻辑（如需）

编辑 `app/services/job_workflow.py`，在 `merge_work_items()` 中补充 step4：

```python
def merge_work_items(job: AIJob, items: list[AIJobWorkItem]) -> JobResult:
    # ... 现有逻辑 ...
    
    if job.job_type == "novel_localization.step4_translate_review":
        chunk_items = [item for item in items if item.kind == "chunk"]
        
        # 汇总各块的校验结果
        summaries: list[str] = []
        suggestions: list[str] = []
        passed = True
        
        for item in sorted(chunk_items, key=lambda x: x.chunk_index):
            payload = item.result_payload or {}
            signals = payload.get("signals") or {}
            if signals.get("translation_quality_passed") is False:
                passed = False
            
            summary = _artifact_content(payload, "translation_quality_report").strip()
            suggestion = _artifact_content(payload, "translation_optimization_prompt").strip()
            
            if summary:
                summaries.append(f"分块 {item.chunk_index}:\n{summary}")
            if suggestion:
                suggestions.append(f"分块 {item.chunk_index}:\n{suggestion}")
        
        summary_text = "已满足" if passed else "\n\n".join(summaries)
        suggestion_text = "" if passed else "\n\n".join(suggestions)
        
        return JobResult(
            artifacts=[
                {
                    "key": "translation_quality_report",
                    "type": "text",
                    "label": "翻译质量报告",
                    "content": summary_text,
                },
                {
                    "key": "translation_optimization_prompt",
                    "type": "prompt_suggestion",
                    "label": "改进建议 Prompt",
                    "content": suggestion_text,
                    "target": {
                        "job_type": "novel_localization.step3_translate",
                        "prompt_block_key": "work_note",
                        "default_mode": "append",
                    },
                },
            ],
            signals={"translation_quality_passed": passed},
        )
    
    # ... 现有逻辑 ...
```

---

## 后端如何使用新 Job 类型

### 步骤 1：查询新模板

```bash
curl -s -H "Authorization: Bearer <key>" \
  http://api/api/v1/novel-localization-ai/prompt-templates
```

响应中会包含新模板：
```json
{
  "job_types": [
    // ... 现有三个 ...
    {
      "job_type": "novel_localization.step4_translate_review",
      "name": "英文翻译校验",
      "description": "检查英文翻译的质量...",
      "prompt_blocks": [
        { "key": "system", "role": "system", "label": "系统 Prompt", "default_content": "..." },
        { "key": "user", "role": "user", "label": "用户 Prompt", "default_content": "..." },
        { "key": "work_note", "role": "user", "label": "工作注释 Prompt", "default_content": "" }
      ]
    }
  ]
}
```

### 步骤 2：创建 step4 Job

```json
POST /api/v1/novel-localization-ai/jobs

{
  "job_type": "novel_localization.step4_translate_review",  // ← 新 job_type
  "model_id": "gpt-4o-mini",
  "source": {
    "oss": {
      "oss_key": "step3/translated.txt",
      "oss_url": "https://example.com/step3/translated.txt",
      "content_hash": "sha256:...",
      "content_type": "text/plain; charset=utf-8"
    }
  },
  "callback": {
    "url": "https://backend.example.com/ai-callbacks/novel-localization"
  },
  "prompt": {
    "blocks": [
      { "key": "system", "role": "system", "content": "..." },
      { "key": "user", "role": "user", "content": "..." },
      { "key": "work_note", "role": "user", "content": "" }
    ]
  },
  "metadata": {
    "external_job_ref": "step4-review-001"
  }
}
```

### 步骤 3：处理响应

```json
GET /api/v1/novel-localization-ai/jobs/{job_id}

{
  "job_id": "...",
  "job_type": "novel_localization.step4_translate_review",
  "status": "succeeded",
  "result": {
    "artifacts": [
      {
        "key": "translation_quality_report",
        "type": "text",
        "label": "翻译质量报告",
        "content": "术语一致，语法无误，仅有一处分段需调整。"
      },
      {
        "key": "translation_optimization_prompt",
        "type": "prompt_suggestion",
        "label": "改进建议 Prompt",
        "content": "若翻译未通过，请重新翻译时注意...",
        "target": {
          "job_type": "novel_localization.step3_translate",
          "prompt_block_key": "work_note",
          "default_mode": "append"
        }
      }
    ],
    "signals": {
      "translation_quality_passed": true  // ← 新 signal
    }
  }
}
```

### 步骤 4：决策和重试

```python
if result['signals']['translation_quality_passed']:
    # 翻译质量通过，流程结束
    print("翻译质量符合要求")
else:
    # 翻译质量未通过，根据建议重新翻译
    opt_artifact = next(
        a for a in result['artifacts']
        if a['key'] == 'translation_optimization_prompt'
    )
    
    # 重新提交 step3，注入改进建议
    new_work_note = opt_artifact['content']
    
    step3_retry = post_job({
        'job_type': 'novel_localization.step3_translate',
        'input': step1_localized_text,
        'prompt': {
            'blocks': [
                { 'key': 'system', 'role': 'system', 'content': system_prompt },
                { 'key': 'user', 'role': 'user', 'content': user_prompt },
                { 'key': 'work_note', 'role': 'user', 'content': new_work_note }  // ← 注入建议
            ]
        }
    })
```

---

## 关键点总结

### ✅ API 层无需改动

| 接口 | 改动 | 说明 |
|---|---|---|
| GET /health | ❌ | 不变 |
| GET /models | ❌ | 不变 |
| GET /prompt-templates | ❌ | 自动返回新模板，无需改代码 |
| POST /jobs | ❌ | 请求体结构完全相同 |
| GET /jobs/{job_id} | ❌ | 响应体结构完全相同（仅 job_type/artifact 的 key 可能不同） |

### ✅ 后端改动最小化

```python
# 差异仅在这里
if step_code == 'step4_translate_review':
    result = post_job('novel_localization.step4_translate_review', ...)
    if not result['signals']['translation_quality_passed']:
        # 根据建议重跑 step3
        ...
```

### ✅ 未来继续扩展也不会破坏现有代码

如果再新增 step5、step6...，后端仍然调用同样的 5 个 API。

---

## 对比：破坏性 vs 非破坏性扩展

### ❌ 破坏性扩展（不推荐）

假如新增 job_type 需要**新的专有字段**：

```json
// ❌ 坏：改变了 POST /jobs 请求体结构
{
  "job_type": "step4_translate_review",
  "source": { ... },
  "translation_specific_config": {    // ← 新字段，破坏契约
    "quality_threshold": 0.8,
    "check_terminology": true
  }
}
```

→ 后端需要改代码来支持新字段
→ 旧代码无法使用新 job_type
→ API 版本必须升至 v2

### ✅ 非破坏性扩展（推荐）

新增 job_type 仍然使用**统一的请求体**：

```json
// ✅ 好：请求体结构完全相同
{
  "job_type": "novel_localization.step4_translate_review",  // ← 枚举扩展
  "source": { ... },
  "prompt": { ... },  // ← 仍然是通用 prompt
  "metadata": {
    "translation_quality_threshold": 0.8,  // ← 业务参数放在 metadata
    "check_terminology": true
  }
}
```

→ 后端无需改请求体代码
→ 旧代码和新代码都能工作
→ API 版本仍为 v1.x

---

## 最佳实践

### ✅ 设计新 job_type 时

1. **复用现有 Prompt 块结构**：system + user + work_note
2. **复用现有 artifact 格式**：key/type/label/content
3. **复用现有 signal 约定**：按需添加新 signal 字段
4. **业务配置放 metadata**：不新增请求体字段
5. **定义新的 artifact.key**：但保持 type 和格式一致

### ✅ 实现新 job_type 时

1. 在 `prompt_templates.py` 中添加模板
2. 在 `executor.py` 中添加执行逻辑
3. 在 `job_planner.py` 中（如需）添加规划逻辑
4. 在 `job_workflow.py` 中（如需）添加合并逻辑
5. **无需改 API 路由**

### ❌ 避免的做法

1. 不在请求体中新增字段（除非 v2 大版本变更）
2. 不改变 artifact 的结构（type/label/content）
3. 不改变 signal 的现有字段含义
4. 不在 API 层添加新接口（如 POST /jobs/step4）

---

## 总结

通过这个示例可以看到：

- **添加新 job_type** = 在内部实现（prompt/executor/workflow）中扩展，无需改 API
- **后端升级方式** = 仅在业务逻辑中处理新 job_type（if-else），无需改底层代码
- **向后兼容** = 旧后端代码仍能工作，新后端代码能使用新能力
- **API 长期稳定** = 5 个接口的结构不变，仅枚举值可扩展

这正是"API 契约稳定 + 内部实现灵活"的最佳体现。

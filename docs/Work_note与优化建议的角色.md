# Prompt Block：work_note 与优化建议的角色区分

## 文档目的

本文档澄清 `work_note` block 在小说本地化流程中的**两个不同角色**，以及它与 `optimization_prompt` 的关系。

---

## 快速参考

**step2 校验失败，如何重跑 step1？**

```python
# 1. 提取优化建议
optimization = next(
    a for a in step2_result['artifacts']
    if a['key'] == 'optimization_prompt'
)

# 2. 重跑 step1，注入建议（只改 work_note）
step1_retry = call_api('step1_localize', {
    'prompt': {
        'blocks': [
            { 'key': 'system', 'role': 'system', 'content': system_prompt },
            { 'key': 'user', 'role': 'user', 'content': user_prompt },
            { 'key': 'work_note', 'role': 'user', 
              'content': optimization['content'] }  # ← 直接使用建议内容
        ]
    }
})

# 3. 再次校验，循环迭代
```

**关键点**：
- ✅ `job_type` 保持不变（仍为 `step1_localize`）
- ✅ 仅修改 `prompt.blocks` 中 `work_note` 的 `content`
- ✅ 将 `optimization_prompt.content` **直接**赋值给 `work_note.content`

---

## 背景

在三步本地化流程中：

1. **step1_localize**（本地化）
2. **step2_review**（校验）→ 可能失败，生成改进建议
3. **step3_translate**（翻译）

当 step2 校验失败时，业务后端需要根据 AI 的建议重新执行 step1。这个过程涉及 `work_note` 的**上下文传递**和**迭代优化**。

---

## work_note 的角色 1：用户输入（Context 传递）

### 定义

`work_note` 是 prompt.blocks 中的一个标准字段，由**业务后端**在创建 Job 时填充。

### 结构

```json
{
  "key": "work_note",
  "role": "user",
  "content": "string (可以为空)"
}
```

### 内容示例

**首次调用 step1_localize（无改进建议）**：
```json
{
  "key": "work_note",
  "role": "user",
  "content": ""  // 或不填，视为空字符串
}
```

**根据 step2 的建议重新调用 step1_localize**：
```json
{
  "key": "work_note",
  "role": "user",
  "content": "参考上次校验意见，请重点修改人物称谓的一致性，避免同一人物出现多个不同的名字；检查是否有过度的美国化转换。"
}
```

### 作用

- 传递**业务上下文**或**之前的反馈**，帮助 LLM 更好地理解期望
- 实现"问题 → 反馈 → 改进"的**迭代闭环**
- 对应 LLM 的用户指令链中的"补充说明"角色

---

## work_note 的角色 2：AI 建议的注入点（Suggestion 消费）

### 流程图

```
step1_localize 成功
    ↓
step2_review 执行
    ├─ 校验通过 → 进入 step3
    └─ 校验失败 → 生成 optimization_prompt
           ↓
      优化建议（prompt 片段）
           ↓
      业务后端提取并**注入到 work_note**
           ↓
      重新调用 step1_localize（带新 work_note）
           ↓
      step1 输出更优质的本地化稿
           ↓
      step2 再次校验（以此类推）
```

### optimization_prompt 的结构

当 step2_review 校验失败时，AI 能力层返回：

```json
{
  "artifacts": [
    {
      "key": "review_summary",
      "type": "text",
      "label": "校验结果",
      "content": "存在人物称谓不一致、过度美国化等问题"
    },
    {
      "key": "optimization_prompt",
      "type": "prompt_suggestion",
      "label": "优化建议 Prompt",
      "content": "请重新本地化时统一使用以下人物名字映射：\\n- 沈砚 → Shen Yan（保留原名拼音）\\n- 赵虎 → Tiger Zhao\\n避免混用中英文名字。检查是否有过度删减的段落。",
      "target": {
        "job_type": "novel_localization.step1_localize",
        "prompt_block_key": "work_note",
        "default_mode": "append"
      }
    }
  ]
}
```

### 关键字段说明

| 字段 | 说明 |
|---|---|
| `key` | 固定为 "optimization_prompt" |
| `type` | 固定为 "prompt_suggestion" |
| `content` | AI 生成的改进建议，直接是 Prompt 文本片段（不是结构化数据） |
| `target.job_type` | 建议注入到哪个 job_type（通常是 step1_localize） |
| `target.prompt_block_key` | 建议注入到该 job_type 的哪个 block（通常是 work_note） |
| `target.default_mode` | 注入模式：append（追加）或 replace（替换） |

### 业务后端的使用方式

**伪代码**：

```python
# step2 校验失败
if not step2_result['signals']['passed']:
    # 提取优化建议
    optimization = next(
        a for a in step2_result['artifacts']
        if a['key'] == 'optimization_prompt'
    )
    
    # 获取当前 work_note
    current_work_note = current_step1_prompts['work_note']['content'] or ""
    
    # 根据 default_mode 合并
    if optimization['target']['default_mode'] == 'append':
        new_work_note = f"{current_work_note}\n\n{optimization['content']}"
    else:  # replace
        new_work_note = optimization['content']
    
    # 构造新的 step1 请求
    step1_request = {
        'job_type': 'novel_localization.step1_localize',
        'model_id': model_id,
        'input': { ... },
        'prompt': {
            'blocks': [
                { 'key': 'system', 'role': 'system', 'content': system_prompt },
                { 'key': 'user', 'role': 'user', 'content': user_prompt },
                { 'key': 'work_note', 'role': 'user', 'content': new_work_note }  # ← 注入这里
            ]
        },
        ...
    }
    
    # 重新提交 step1
    step1_retry = submit_job(step1_request)
```

---

## 两个角色的关系矩阵

| 场景 | work_note 内容 | 来源 | 作用 | 示例 |
|---|---|---|---|---|
| 首次本地化 | 空或业务说明 | 业务后端 | 基础指导 | "" 或 "保留人物原名拼音" |
| 根据校验建议重做 | 追加 optimization_prompt | AI 层生成 | 迭代改进 | "保持风格...\\n请统一人物名字..." |
| 根据人工反馈重做 | 自定义说明 | 业务后端/用户 | 人工干预 | "用户觉得过度美国化，请保留更多中文特色" |
| 多轮迭代 | 逐次追加 | AI + 业务后端 | 累积约束 | "...\\n尝试 1：...\\n尝试 2：..." |

---

## 常见问题

### Q1：work_note 可以为空吗？

**A**：可以。如果业务后端没有额外的上下文指导，可以传空字符串或不填此 block。AI 能力层会使用默认的 system prompt 进行处理。

### Q2：optimization_prompt 的内容是否会自动追加？

**A**：否。AI 能力层只返回建议（optimization_prompt artifact），**业务后端负责**决定是否追加、如何追加。这样设计是为了：
- 给业务后端审核的机会（人工干预点）
- 支持多轮迭代（追加多个建议）
- 避免 AI 层过度填充 prompt

### Q3：work_note 中可以包含 JSON 吗？

**A**：可以，但不推荐。目前 AI 能力层不解析 work_note 的结构，仅当做自由文本传递给 LLM。如果需要结构化数据，应该：
- 用自然语言表述（推荐）
- 或在 metadata 中传递，而不是 work_note

### Q4：step2_review 和 step3_translate 需要 work_note 吗？

**A**：不需要特别关注。虽然 API 要求传 work_note block，但：
- **step2_review**：work_note 通常为空或包含上一步的说明
- **step3_translate**：work_note 通常为空，翻译主要依赖本地化的结果

### Q5：如果多次 step2 都失败，work_note 会无限增长吗？

**A**：可能会。如果业务逻辑是"追加所有建议"，work_note 可能变很长。建议：
- 只保留最新的、有效的建议
- 或限制 work_note 的长度（如 2KB）
- 或每次清空后只保留最新的建议

---

## 最佳实践

### 业务后端的推荐流程

```python
def handle_localization_with_feedback(user_input):
    # 第一次：无建议
    step1_result = call_api('step1_localize', {
        'input': user_input,
        'prompt': {
            'blocks': [
                { 'key': 'system', 'role': 'system', 'content': system_prompt },
                { 'key': 'user', 'role': 'user', 'content': user_prompt },
                { 'key': 'work_note', 'role': 'user', 'content': '' }
            ]
        }
    })
    
    # 执行校验
    step2_result = call_api('step2_review', {
        'input': step1_result['localized_text'],
        'prompt': { ... }
    })
    
    # 如果校验失败
    retry_count = 0
    while not step2_result['signals']['passed'] and retry_count < 3:
        retry_count += 1
        
        # 提取建议
        opt_artifact = next(
            a for a in step2_result['artifacts']
            if a['key'] == 'optimization_prompt'
        )
        
        # 仅保留最新建议（不累积）
        new_work_note = opt_artifact['content']
        
        # 重试 step1
        step1_result = call_api('step1_localize', {
            'input': user_input,
            'prompt': {
                'blocks': [
                    { 'key': 'system', 'role': 'system', 'content': system_prompt },
                    { 'key': 'user', 'role': 'user', 'content': user_prompt },
                    { 'key': 'work_note', 'role': 'user', 'content': new_work_note }  # ← 注入建议
                ]
            }
        })
        
        # 再次校验
        step2_result = call_api('step2_review', {
            'input': step1_result['localized_text'],
            'prompt': { ... }
        })
    
    # 进入翻译
    if step2_result['signals']['passed']:
        step3_result = call_api('step3_translate', {
            'input': step1_result['localized_text'],
            'prompt': { ... }
        })
        return step3_result
    else:
        raise Exception("达到重试上限，校验仍未通过")
```

---

## 总结

- **work_note 角色 1**：业务后端填充的**上下文指导**，支持人工干预和多轮迭代
- **work_note 角色 2**：AI 层建议的**注入点**，通过 optimization_prompt artifact 传递
- **设计原理**：解耦"AI 生成"和"业务消费"，给业务层充分的控制权和审核机会

这个设计使得：
1. AI 层只负责**生成建议**，不强制执行
2. 业务后端可以**审核或修改建议**后再使用
3. 支持**人工干预**和**多轮迭代**
4. 避免 AI 层过度"聪明"导致不可控

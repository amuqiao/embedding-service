# step1 重跑设计决策

本文记录 step2 校验不通过后重跑 step1 的正确方式，以及背后的设计依据。

## 核心结论

step2 不通过后重跑 step1，正确调用方式：

| 字段 | 传入内容 |
|---|---|
| `source.oss` | step1 输出的 `localized_text` 的 OSS key（**本地化稿**，不是原文） |
| `work_note` | **只传 step2 返回的建议工作注释**，不与 step1 旧注释合并 |

## 为什么传本地化稿，不传原文

step2 审的是本地化稿，发现的问题也在本地化稿上。建议工作注释是针对那份稿子写的，修正目标是修复具体问题，不是重新做一遍。

传原文重跑没有意义——step1 第一次已经做了完整本地化，大部分内容是对的。丢掉重来等于放弃已有的正确工作，且 step2 的建议针对的是本地化稿的具体措辞，对着原文执行这些建议没有语义支撑。

step1 提示词已有约束：

> 对于无明显问题的文字保持不变

模型看到的是一份已经本地化的稿子，只有 `work_note` 指出的问题需要修正，其余内容保持不动。这句约束就覆盖了"接收本地化稿进行定向修正"的场景，不需要额外改提示词。

## 为什么 work_note 只传 step2 建议，不追加旧注释

step1 旧注释描述的是第一次执行时的决策，这些决策的结果已经体现在本地化稿里了。把旧注释再传一遍是重复信息，还可能与 step2 建议产生语义冲突。

step2 建议工作注释直接作为下一次 step1 的 `work_note` 传入即可，所以 step2 返回的 `work_note` artifact 使用 `apply_mode=replace`，语义是"直接替换使用，不与旧注释合并"。

## apply_mode 语义确认

| 阶段 | apply_mode | 含义 |
|---|---|---|
| step1_localize | `replace` | 完整工作注释候选，和 `localized_text` 同属本次 step1 输出 |
| step2_review（不通过时） | `replace` | 建议工作注释，供调用方直接作为下一次 step1 的 `work_note` 传入 |

step2 不再使用 `append`。`append` 的原始含义是"追加到当前工作注释"，但追加旧注释没有实际价值（见上），因此统一改为 `replace`。

## 重跑请求示例

```json
{
  "job_type": "novel_localization.step1_localize",
  "source": {
    "oss": {
      "oss_key": "上一次 step1 输出的 localized_text oss_key",
      "oss_url": "...",
      "content_type": "text/plain; charset=utf-8"
    }
  },
  "prompt": {
    "blocks": [
      {"key": "user", "role": "user", "content": "..."},
      {"key": "work_note", "role": "user", "content": "【step2 返回的 work_note.content】"}
    ]
  }
}
```

调用方只需把 `artifacts[key=work_note].content` 直接写入下一次请求的 `prompt.blocks[key=work_note].content`，配合 `localized_text` 的 OSS key 作为 `source`，即可完成重跑。

## 边界说明

- step2 校验通过（`signals.passed=true`）时不返回 `work_note`，不触发重跑路径。
- 用户手动编辑工作注释后重跑：`source` 仍传本地化稿 OSS key，`work_note` 传用户编辑后的注释内容，逻辑相同。
- AI 能力层不决定是否重跑、不控制重试次数，这些由业务后端决定。

# 小说本地化三阶段与工作注释模型 v2

本文重新梳理“用户、业务后端、AI 能力层”三个心智模型下，三阶段本地化流程、工作注释、建议工作注释与重跑闭环的关系。

## 一句话结论

工作注释和建议工作注释在 AI 能力层接口中统一成同一种返回结构：`work_note` artifact。

```text
请求输入: prompt.blocks.work_note.content
响应输出: result.artifacts[].key = work_note
```

AI 能力层用 `apply_mode` 表示这段工作注释候选内容的使用语义：

```text
apply_mode=replace  供调用方直接使用的工作注释候选内容。
                    step1 返回：本次本地化的完整工作注释。
                    step2 返回（不通过时）：建议工作注释，供调用方直接作为下一次 step1 的 work_note 传入，不与旧注释合并。
```

## 三个心智模型

### 用户视角

用户不需要理解 Job、artifact、Prompt block。

用户看到的是一个业务流程：

```text
原文
  ↓
生成本地化稿
  ↓
查看本地化稿和工作注释
  ↓
修改工作注释，决定是否重新本地化
  ↓
提交校验
  ↓
若校验不通过，查看修改建议，决定是否按建议继续重跑
  ↓
校验通过后进入翻译
```

在用户视角里，“工作注释”就是一个可编辑对象。它可以来自模型，也可以被用户修改，还可以吸收校验阶段给出的修改建议。

### 业务后端视角

业务后端负责把用户看到的业务对象落库，并把它们转换成 AI 能力层每次需要的 Job 请求。

业务后端需要管理：

```text
原文 OSS 引用
当前本地化稿 OSS 引用
当前英文译文 OSS 引用
工作注释对象
校验结果
用户确认状态
阶段状态机
每次 Job 历史
```

业务后端决定：

- 是否重新执行 step1 本地化。
- 是否把用户修改后的工作注释作为下一次 `work_note`。
- 是否把 step2 返回的 `work_note(apply_mode=replace)` 作为下一次 step1 的 `work_note` 使用。
- 是否继续校验、跳过校验或进入翻译。

### AI 能力层视角

本服务只做能力执行，不做业务编排。

AI 能力层每次只理解一次 Job：

```text
接收 job_type + source.oss + prompt.blocks + model_id + callback
  ↓
读取本次 source.oss
  ↓
用本次 prompt.blocks 执行模型
  ↓
返回 artifacts + signals
```

AI 能力层不保存“当前工作注释”，不自动读取历史 Job，也不自动把建议写回下一次请求。

## 三阶段职责

| 阶段 | job_type | 用户理解 | 后端输入 | AI 返回 |
|---|---|---|---|---|
| 本地化 | `novel_localization.step1_localize` | 生成本地化稿和工作注释 | 原文 + 当前工作注释 | `localized_text`、`work_note(apply_mode=replace)` |
| 本地化校验 | `novel_localization.step2_review` | 判断本地化稿是否合格 | 当前本地化稿 + 当前工作注释 | `review_summary`、`passed`；失败时额外返回 `work_note(apply_mode=replace)` |
| 翻译 | `novel_localization.step3_translate` | 生成英文终稿 | 当前确认的本地化稿 + 当前工作注释 | `translated_text` |

三个阶段是三个独立 Job。是否进入下一阶段、是否重跑上一阶段，由业务后端和用户决定。

## AI 能力层的 work_note 契约

AI 能力层只定义 `work_note` artifact 的接口结构，不定义调用方如何建模或持久化。

```text
step1_localize 返回:
  work_note(apply_mode=replace)
  表示完整工作注释候选内容。

step2_review 校验不通过时返回:
  work_note(apply_mode=replace)
  表示建议工作注释，供调用方直接作为下一次 step1 的 work_note 传入，不与旧注释合并。
```

调用方如果希望下一次 Job 使用某段工作注释，必须在下一次请求中通过 `prompt.blocks[].key=work_note.content` 显式传入。

## work_note 输入与输出的关系

### 接口层关系

```text
POST /jobs prompt.blocks.work_note.content
  是输入，表示本次模型应该遵守的工作注释。

GET /jobs/{job_id} result.artifacts[].key = work_note
  是输出，表示模型生成的工作注释候选内容。
```

关键边界：

```text
Job 返回的 work_note 不是 prompt block。
Job 返回的 work_note 没有 role。
业务后端确认或用户确认后，才把 work_note.content 写入下一次请求的 prompt.blocks[].key=work_note.content。
下一次请求中的 role 仍来自 prompt template，当前固定为 user。
```

### 业务层关系

业务层可以把二者视为同一个对象的不同版本：

```text
首次本地化:
  work_note.content = 空或已有约束
  step1 返回 work_note(apply_mode=replace)
  后端把 work_note.content 作为工作注释候选内容展示给用户

用户修改:
  用户编辑工作注释
  调用方自行保存状态

重新本地化:
  work_note.content = 调用方下一次请求的 work_note.content
  创建新的 step1 Job
```

因此，回答“是否应该是同一个对象”：

```text
对用户：是，同一个工作注释对象。
对调用方：是否同一个存储对象由调用方自行决定，不属于 AI 能力层接口契约。
对 AI 能力层：是统一语义，但区分请求输入和响应候选内容。
```

## 建议工作注释的关系

step2 校验失败后返回的修改建议也使用 `work_note` artifact，不再单独使用 `optimization_prompt` 结构。

它和工作注释的关系是：

```text
work_note(apply_mode=replace) 不是新的 Prompt block
work_note(apply_mode=replace) 不是 AI 能力层自动执行的指令
work_note(apply_mode=replace) 是给用户/后端确认后直接作为下一次 step1 的 work_note 使用的建议内容
```

调用方处理边界：

```text
step2 校验失败
  ↓
展示 review_summary 和 work_note(apply_mode=replace)
  ↓
用户确认、编辑或拒绝建议工作注释
  ↓
若确认重跑:
  调用方下一次请求的 work_note.content = 用户确认后的建议工作注释（直接使用，不追加旧注释）
  source.oss = 本地化稿的 OSS key
  ↓
创建新的 step1_localize Job
```

## 重跑闭环

完整闭环由调用方编排：

```text
1. 原文 → step1 本地化
   返回 localized_text + work_note(apply_mode=replace)

2. 用户修改工作注释
   调用方自行保存状态

3. 如果用户选择重跑本地化
   调用方用原文 + 新的 work_note.content 创建新的 step1

4. 用户确认某版 localized_text 后
   调用方用该 localized_text 创建 step2 校验

5. 如果 step2 passed=false
   AI 返回 review_summary + work_note(apply_mode=replace)

6. 用户确认建议后
   调用方用本地化稿 + work_note(建议内容，直接使用) 创建新的 step1

7. 重复 4-6，直到:
   - step2 passed=true
   - 或用户手动接受当前版本
   - 或达到业务后端设置的重试上限

8. 进入 step3 翻译
```

## 三类 Prompt 的位置

每次 Job 都有三类 Prompt block：

| Prompt | 来源 | 用途 |
|---|---|---|
| `system` | 调用方传入的 PromptConfig，默认来自 AI 能力层模板 | 定义模型角色和基本约束 |
| `user` | 调用方传入的 PromptConfig，默认来自 AI 能力层模板 | 定义本阶段任务和输出格式 |
| `work_note` | 调用方在本次请求中传入的工作注释 | 注入用户确认后的术语、理由、修正和建议 |

AI 能力层执行时只使用本次请求传入的最终 `prompt.blocks`。

## 阶段输入建议

| 阶段 | source.oss 应传什么 | work_note 应传什么 |
|---|---|---|
| step1 初次本地化 | 原文 | 空，或项目已有约束 |
| step1 重跑本地化 | **本地化稿**（上一次 step1 输出的 `localized_text`） | step2 返回的建议工作注释，或用户编辑后的工作注释 |
| step2 本地化校验 | 当前确认的本地化稿 | 当前确认的工作注释 |
| step3 翻译 | 当前确认且可进入翻译的本地化稿 | 当前确认的术语、风格、项目记忆 |

注意：step2 校验失败后重跑 step1，`source` 应传本地化稿（`localized_text` 的 OSS key），而不是原文。校验建议作为 `work_note` 传入，step1 提示词约束"对于无明显问题的文字保持不变"确保模型只修正问题处，不重写整篇。设计依据见 [`接口层/step1-rerun-design.md`](接口层/step1-rerun-design.md)。

## 与后端对接接口文档的关系

后端对接文档定义的是 AI 能力层接口合同：

```text
POST /jobs
  job_type
  model_id
  source.oss
  callback
  prompt.blocks
```

本文仅说明调用方需要如何把用户态输入显式映射到 AI 能力层接口合同：

```text
用户工作注释 → prompt.blocks.work_note.content
本地化稿 OSS → step2 的 source.oss
原文 OSS → step1 的 source.oss
校验建议 → 调用方确认后显式放入下一次 work_note.content
```

## 明确边界

AI 能力层不做：

- 不保存工作注释状态。
- 不保存用户 PromptConfig。
- 不决定是否重跑。
- 不自动合并响应 `work_note` 和请求 `work_note`。
- 不自动选择历史 Job 结果作为下一次输入。
- 不控制循环次数。

调用方负责：

- 保存每次 step1/step2 返回的 `work_note` artifact。
- 让用户确认是否应用建议。
- 构建下一次 Job 请求。
- 控制重试上限和阶段状态。

## 推荐结论

请求工作注释和响应工作注释候选内容都使用 `work_note` 这个统一语义，靠方向和 `apply_mode` 区分用途。响应 `work_note` 不是 prompt block，不带 `role`；调用方采纳后再放入下一次请求的 `prompt.blocks[].key=work_note.content`，`role` 由 prompt template 决定。

推荐命名：

```text
接口输入: prompt.blocks.work_note.content
接口输出: artifacts[].key = work_note
合并策略: artifacts[].apply_mode = replace（step1 和 step2 均使用 replace）
调用方状态: 由调用方自定义
```

这样可以减少 `notes`、`optimization_prompt`、`prompt_suggestion` 多套结构造成的维护成本，也符合用户对“工作注释可以查看、修改、再提交”的直觉。

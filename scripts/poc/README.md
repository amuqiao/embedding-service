# 短剧打标 POC

本目录用于验证“短剧素材 + 标签体系 + 互斥规则 -> AI 打标结果”的本地 POC 流程。

POC 被拆成两个脚本：

- `short_drama_build_structured_inputs.py`：本地结构化输入构建脚本。它只负责把当前仓库 `.data/` 下的第三批素材、标签体系和互斥规则转换成结构化 JSON。
- `short_drama_tagging_poc.py`：AI 打标流程脚本。它只消费结构化 `input.json` 和 POC 配置，不再读取 `.srt`、`.xlsx`、`.md` 等本地原始资源。

这个拆分是为了贴近未来服务形态：正式服务化后，CPP 服务负责提供短剧素材结构化数据，RS 服务负责提供标签体系和互斥规则，AI 服务只保留第二个脚本对应的流程能力。

## 运行目录

默认 POC 运行目录是：

```text
.data/poc/short_drama_tagging/
  inputs/
    cpp/
      material_snapshot.json
    rs/
      tag_schema_snapshot.json
      mutual_exclusion_rules.json
    jobs/
      per_book/<t_book_id>/input.json
  config/
    ai_tagging_poc_config.json
    workflow_definition.json
    prompt_templates.json
  runs/
    latest/
      per_book/<t_book_id>/
        input/input.json
        intermediate/prompts.json
        intermediate/story_overview_result.json
        intermediate/candidate_tags.json
        outputs/final_tags.json
        outputs/tagging_detail.json
        outputs/job_result.json
```

目录职责：

- `inputs/cpp/`：模拟 CPP 服务提供的短剧素材快照。
- `inputs/rs/`：模拟 RS 服务提供的标签体系和互斥规则。
- `inputs/jobs/per_book/`：按短剧拆分后的 AI 服务请求输入，每个 `input.json` 是第二个脚本的主输入。
- `config/`：AI 打标 POC 的服务配置，包括模型参数、workflow 阶段定义和 prompt 模板。
- `runs/`：第二个脚本的运行产物，包括输入留档、中间 prompt、模型中间结果和最终输出。

## 使用方式

### 1. 先区分输入目录

本 POC 常见有两套结构化输入目录：

```text
.data/poc/short_drama_tagging/inputs/
.data/poc/short_drama_tagging/inputs_full/
```

- `inputs/`：默认样本输入，适合小范围 dry-run、调 prompt、验证流程。
- `inputs_full/`：完整输入，适合对全部短剧正式打标。

两者内部结构相同，真正给打标脚本消费的是：

```text
<inputs_root>/jobs/per_book/<t_book_id>/input.json
```

选择单部还是批量，取决于传给 `short_drama_tagging_poc.py` 的参数：

- `--input-json <某个 input.json>`：单部打标。
- `--input-dir <jobs/per_book 目录>`：批量打标，脚本会按排序扫描每个 `*/input.json`。
- `--limit N`：批量目录里只取前 N 部，适合小批量验证。

### 2. 构建结构化输入

从默认第三批素材构建完整 POC 输入：

```bash
uv run python scripts/poc/short_drama_build_structured_inputs.py \
  --poc-root .data/poc/short_drama_tagging
```

快速构建少量样本，适合检查目录结构和输入格式：

```bash
uv run python scripts/poc/short_drama_build_structured_inputs.py \
  --poc-root .data/poc/short_drama_tagging \
  --limit 1 \
  --limit-episodes 1
```

只构建指定短剧：

```bash
uv run python scripts/poc/short_drama_build_structured_inputs.py \
  --poc-root .data/poc/short_drama_tagging \
  --book-id 200000000000000417
```

需要更换原始资源时，显式传入路径：

```bash
uv run python scripts/poc/short_drama_build_structured_inputs.py \
  --poc-root .data/poc/short_drama_tagging \
  --material-dir .data/第三批字幕srt \
  --tag-xlsx .data/标签体系v1.2.xlsx \
  --works-md .data/第三批打标尝试.md
```

### 3. 只生成 prompt，不调用模型

默认使用 dry-run，只生成输入留档和 prompt，不调用模型，适合检查输入和 prompt 是否正确。

使用默认 `inputs/`：

```bash
uv run python scripts/poc/short_drama_tagging_poc.py \
  --poc-root .data/poc/short_drama_tagging \
  --dry-run
```

对 `inputs_full/` 里的全部短剧只生成 prompt：

```bash
uv run python scripts/poc/short_drama_tagging_poc.py \
  --input-dir .data/poc/short_drama_tagging/inputs_full/jobs/per_book \
  --output-dir .data/poc/short_drama_tagging/runs/full_prompts \
  --dry-run
```

对 `inputs_full/` 里的单部短剧只生成 prompt：

```bash
uv run python scripts/poc/short_drama_tagging_poc.py \
  --input-json .data/poc/short_drama_tagging/inputs_full/jobs/per_book/200000000000000417/input.json \
  --output-dir .data/poc/short_drama_tagging/runs/single_200000000000000417_prompt \
  --dry-run
```

### 4. 调用模型打标

运行前需要先确认本地模型调用环境已配置完成，例如当前 shell 中存在可用的 `OPENAI_API_KEY`，并且当前虚拟环境已安装项目依赖。

检查当前 shell 是否能读到 key：

```bash
echo ${OPENAI_API_KEY:+set}
```

输出 `set` 代表当前 shell 已配置；没有输出代表当前 shell 没读到。

#### 单部打标

```bash
uv run python scripts/poc/short_drama_tagging_poc.py \
  --input-json .data/poc/short_drama_tagging/inputs_full/jobs/per_book/200000000000000417/input.json \
  --output-dir .data/poc/short_drama_tagging/runs/single_200000000000000417 \
  --run-model
```

#### 单部打标并指定模型

`gpt-5.5` 只接受默认温度，命令里需要显式传 `--temperature 1`，否则会因为配置文件默认 `temperature: 0.2` 被 API 拒绝。

```bash
uv run python scripts/poc/short_drama_tagging_poc.py \
  --input-json .data/poc/short_drama_tagging/inputs_full/jobs/per_book/200000000000000417/input.json \
  --output-dir .data/poc/short_drama_tagging/runs/single_200000000000000417_gpt55 \
  --model gpt-5.5 \
  --temperature 1 \
  --run-model
```

#### 小批量验证

```bash
uv run python scripts/poc/short_drama_tagging_poc.py \
  --input-dir .data/poc/short_drama_tagging/inputs_full/jobs/per_book \
  --output-dir .data/poc/short_drama_tagging/runs/full_limit_3_gpt55 \
  --limit 3 \
  --model gpt-5.5 \
  --temperature 1 \
  --concurrency 2 \
  --run-model
```

#### 全部短剧打标

```bash
uv run python scripts/poc/short_drama_tagging_poc.py \
  --input-dir .data/poc/short_drama_tagging/inputs_full/jobs/per_book \
  --output-dir .data/poc/short_drama_tagging/runs/full_all \
  --run-model
```

#### 全部短剧打标并指定模型

```bash
uv run python scripts/poc/short_drama_tagging_poc.py \
  --input-dir .data/poc/short_drama_tagging/inputs_full/jobs/per_book \
  --output-dir .data/poc/short_drama_tagging/runs/full_all_gpt55 \
  --model gpt-5.5 \
  --temperature 1 \
  --concurrency 3 \
  --run-model
```

#### 并发执行

`--concurrency` 控制同时处理多少部短剧。每部短剧内部仍按 `story_overview -> candidate_tagging -> finalize` 顺序执行，不会并发同一部剧的不同阶段。

建议先从 `2` 或 `3` 开始：

```bash
uv run python scripts/poc/short_drama_tagging_poc.py \
  --input-dir .data/poc/short_drama_tagging/inputs_full/jobs/per_book \
  --output-dir .data/poc/short_drama_tagging/runs/full_all_gpt55_c3 \
  --model gpt-5.5 \
  --temperature 1 \
  --concurrency 3 \
  --run-model
```

也可以写入 `config/ai_tagging_poc_config.json` 作为默认值：

```json
{
  "concurrency": 3
}
```

命令行 `--concurrency` 优先级高于配置文件。并发数越大，越容易遇到模型服务的 rate limit 或超时；如果全量跑失败，先降低并发数再重试。

#### 使用环境变量设置默认模型

```bash
export SHORT_DRAMA_POC_MODEL=gpt-5.5
```

之后可以省略 `--model`：

```bash
uv run python scripts/poc/short_drama_tagging_poc.py \
  --input-dir .data/poc/short_drama_tagging/inputs_full/jobs/per_book \
  --output-dir .data/poc/short_drama_tagging/runs/full_all_gpt55 \
  --temperature 1 \
  --run-model
```

#### 覆盖温度和超时

下面示例适用于支持自定义温度的模型。`gpt-5.5` 不要使用 `--temperature 0.2`，应使用 `--temperature 1`。

```bash
uv run python scripts/poc/short_drama_tagging_poc.py \
  --input-dir .data/poc/short_drama_tagging/inputs_full/jobs/per_book \
  --output-dir .data/poc/short_drama_tagging/runs/full_all_gpt4o_mini_tuned \
  --model gpt-4o-mini \
  --temperature 0.2 \
  --timeout-seconds 900 \
  --run-model
```

## 输出检查和下游使用

dry-run 成功后，至少应看到：

```text
.data/poc/short_drama_tagging/runs/latest/per_book/<t_book_id>/
  input/input.json
  intermediate/prompts.json
```

`--run-model` 成功后，额外应看到：

```text
.data/poc/short_drama_tagging/runs/latest/per_book/<t_book_id>/
  intermediate/story_overview_result.json
  intermediate/candidate_tags.json
  outputs/final_tags.json
  outputs/tagging_detail.json
  outputs/job_result.json
```

如果使用了自定义 `--output-dir`，把上面的 `runs/latest` 替换成实际输出目录，例如：

```text
.data/poc/short_drama_tagging/runs/full_all_gpt55/per_book/<t_book_id>/
```

核心产物说明：

- `outputs/final_tags.json`：提供给其他服务消费的最终打标结果。
- `outputs/tagging_detail.json`：打标明细、规则应用、`partial_success` 问题说明。
- `outputs/job_result.json`：聚合结果，包含 `final_tags`、剧情概览、打标明细和 `signals`。
- `intermediate/prompts.json`：实际发给模型的 prompt，用于排查。
- `intermediate/finalize_raw_output.txt`：模型最终阶段原始输出，只用于排查，不建议给下游服务直接消费。

生成报告时，不需要额外维护一张 `id -> name` 映射表。直接使用：

```text
outputs/final_tags.json
inputs_full/rs/tag_schema_snapshot.json
```

或使用对应输入根目录下的：

```text
<inputs_root>/rs/tag_schema_snapshot.json
```

其中：

- `tag_schema_snapshot.json` 是本次打标使用的标签完整表。
- `final_tags.json` 是某部剧的实际打标结果。
- 报告展示分类名时，用 `category_id` 回查 `tag_schema_snapshot.json`。
- 机器处理标签时，优先使用 `label_id`。

如果模型证据不足导致必填分类缺失或少于 `min_items`，脚本会保留可用标签并输出 `partial_success`：

```json
{
  "result_status": "partial_success",
  "validation_issues": [
    {
      "category_id": "000006",
      "category_name": "情绪",
      "issue": "missing_required_category",
      "actual_items": 0
    }
  ]
}
```

缺失必填分类时，`final_tags.json` 中该分类会保留为空数组，便于下游保持稳定结构：

```json
{
  "tags": {
    "000006": []
  }
}
```

## 边界说明

- 短剧素材资源属于 CPP 输入域，对应 `<inputs_root>/cpp/` 和 `<inputs_root>/jobs/per_book/*/input.json` 中的 `job_params`。
- 标签体系和互斥规则属于 RS 输入域，对应 `<inputs_root>/rs/` 和 `input.json` 中的 `rs_default_tag_bundle`。
- 模型、温度、超时和产物约束属于 AI 服务配置，对应 `config/ai_tagging_poc_config.json`。
- 打标阶段顺序和中间产物声明属于 AI workflow 配置，对应 `config/workflow_definition.json`。
- prompt 模板属于 AI prompt 配置，对应 `config/prompt_templates.json`，运行时会被渲染为 `runs/.../intermediate/prompts.json`。
- prompt 模板支持可选 block，例如 `.data/第三批打标尝试.md` 中“情绪变化组合标签”规则会保留为 `emotion_sequence_prompt_v1`，默认 `enabled: false`，需要时再显式开启。
- 打标中间过程和最终结果属于 AI 服务输出，对应 `runs/`。
- 当前 POC 默认不启用情绪组合规则，`000006` 情绪类别先按普通单标签结构输出。

后续拆成独立服务时，正式服务入口不再依赖 `short_drama_build_structured_inputs.py`，而是改由外部 CPP/RS 服务直接提供结构化输入；`short_drama_tagging_poc.py` 对应的流程再收敛为正式服务入口。

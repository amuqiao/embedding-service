**一图总览**

```
CPP
  |
  | POST /api/v1/ai-jobs/jobs
  v
AI API 创建 Job
  | 校验 job_type / job_params / callback / model / fixture 语种文件
  | 写 DB: status=queued, 保存 job_params_ref/runtime_ref
  | 投递 Celery dispatch task
  v
Celery dispatch
  | status queued -> running
  | 生成 short drama single execution plan
  | 创建 whole work item
  v
Worker 执行 whole item
  | 拉 RS 标签库: fixture 或 HTTP GET /api/v1/tag-schemas/default?lang=xx
  | 读取/补齐字幕文本
  | AI 三阶段: story_overview -> candidate_tagging -> finalize
  | adapter: 模型结果 -> RS 写入 payload
  | 写 RS: fixture 或 HTTP POST /api/v1/ai-tag-results
  v
Finalize Job
  | 保存 canonical_result
  | public JobView.result = null
  | mark job succeeded
  v
Callback CPP
  | event=job.succeeded / job.failed
  | body.job 与 GET /jobs/{job_id} 同形
```

**1. CPP 创建 Job**

CPP 调 `POST /api/v1/ai-jobs/jobs`，入口在 app/api/routes/jobs.py。请求里关键字段是：

```json
{
  "job_type": "short_drama.tagging.initial",
  "job_params": {
    "t_book_id": "...",
    "work_context": {
      "subtitle_language": "zh"
    },
    "assets": [
      { "asset_type": "subtitle_srt", "format": "srt", "text": "..." }
    ]
  },
  "callback": { "url": "<https://cpp.example.com/>..." }
}
```

AI API 先根据 `job_type` 找 handler，然后校验 `job_params`。短剧打标要求 `subtitle_language` 是业务语种代码，素材里至少有一个 `subtitle_srt`；本地 fixture 模式下，还会提前检查对应语种的标签库 JSON 是否存在，避免 Job 入队后才失败。实现位置在 schemas.py 和 handler.py。

创建阶段还会做幂等校验：如果 `client_request_id` 已存在且请求指纹一致，直接返回已有 Job；如果同一个 `client_request_id` 对应不同请求，返回冲突。随后写 DB，状态是 `queued`，并保存 `job_params_ref` 和 `runtime_ref`。这段在 app/services/jobs.py。

**2. API 返回 202 并投递任务**

创建成功后，API 给 Job 生成 `celery_task_id`，提交 `jobs.dispatch` Celery task，然后返回 `202 Accepted`。这个响应只表示“AI 已接单”，不是打标完成。代码在 app/api/routes/jobs.py。

返回给 CPP 的核心结构是：

```json
{
  "job_id": "...",
  "status": "queued",
  "status_url": "/api/v1/ai-jobs/jobs/{job_id}"
}
```

CPP 后续可以轮询 `GET /api/v1/ai-jobs/jobs/{job_id}`，这个接口直接从 DB 读当前 JobView。实现见 app/services/jobs.py。

**3. Job 开始执行**

Celery `jobs.dispatch` 收到任务后，会把 `queued` Job 标记为 `running`，然后调用 workflow planner。短剧 handler 的执行计划是 `single`，只创建一个 `whole` work item，不分片。对应实现是 tasks/jobs.py、job_workflow.py 和 handler.py。

```
Job running
  -> plan: execution_mode=single
  -> work item: kind=whole, chunk_index=0
  -> Celery chain:
       execute_work_item_task
       finalize_job_task
```

**4. Worker 拉 RS 标签库**

执行 `whole` item 时，短剧 handler 不走通用 LLM runtime，而是自己实现 `execute_standard_item()`。第一步读取 `subtitle_language`，然后拉对应语种的 RS 标签库。入口在 handler.py。

本地开发：

```
SHORT_DRAMA_RS_SCHEMA_SOURCE=fixture
SHORT_DRAMA_RS_SCHEMA_FIXTURE_PATH=docs/接口层/mock-data/short_drama_tagging/tag_schema_snapshot.{lang}.json
```

正式环境：

```
SHORT_DRAMA_RS_SCHEMA_SOURCE=http
GET {SHORT_DRAMA_RS_BASE_URL}/api/v1/tag-schemas/default?lang={subtitle_language}
Authorization: Bearer {SHORT_DRAMA_RS_API_KEY}
```

provider 会校验 `categories`、`label_id`、`mutual_exclusion_rules` 等结构，不符合就失败。实现见 rs_client.py。

**5. Worker 准备素材**

如果 CPP 直接传了 `assets[].text`，就直接用文本；如果传的是 `oss://bucket/key`，服务会从对象存储读取字幕，并在有 `content_hash` 时校验 hash。实现见 handler.py。

**6. AI 三阶段打标**

当前短剧打标不是一次 prompt 完成，而是三阶段：

```
stage 1: story_overview
  生成剧情概览、人物、世界观、冲突、时间线

stage 2: candidate_tagging
  基于剧情概览和标签库生成候选标签

stage 3: finalize
  根据候选标签、标签库、互斥规则输出 selected_tags + tagging_detail
```

三阶段 prompt 由 prompts.py 生成。每一阶段都调用 `generate_text()`，并要求模型返回 JSON。模型输出不是合法 JSON 会抛 `MODEL_OUTPUT_INVALID`，Job 进入失败终态。

**7. Adapter 转 RS 写入格式**

模型最终输出可能是中文字段：

```json
{
  "标签名": "女频",
  "权重": 0.9,
  "打标原因": "剧情以女主视角展开"
}
```

adapter 会把它转成 RS 需要的英文字段：

```json
{
  "label_id": "...",
  "name": "女频",
  "weight": 0.9,
  "reason": "剧情以女主视角展开",
  "definition": "..."
}
```

这里的 `label_id/name/definition` 不信任模型，而是从 RS 标签库快照里按标签名解析出来。adapter 还会校验未知分类、未知标签、权重范围、必选分类、数量上下限和互斥规则。实现见 adapter.py。

**8. 写 RS 结果**

adapter 生成的 RS 写入 payload 形态是：

```json
{
  "status": "success",
  "msg": null,
  "t_book_id": "...",
  "job_id": "...",
  "tag_schema_version": "v1.1",
  "tags": {
    "000001": [
      {
        "label_id": "...",
        "name": "...",
        "weight": 0.9,
        "reason": "...",
        "definition": "..."
      }
    ]
  }
}
```

handler 先把这个兼容 payload 保存进 canonical result；Job 成功终态 callback CPP 后，再由短剧 handler 的后置 hook 写 RS。也就是说：**RS 写入发生在 callback CPP 之后**。实现见 handler.py 和 job_workflow.py。

本地开发：

```
SHORT_DRAMA_RS_RESULT_SINK=fixture
```

正式环境：

```
SHORT_DRAMA_RS_RESULT_SINK=http
POST {SHORT_DRAMA_RS_BASE_URL}/api/v1/ai-tag-results
```

RS 写入响应必须是明确成功结构：`code=0`，并且有 `msg` 和 `data` 对象。否则抛 `RS_RESULT_WRITE_FAILED`，由后置任务错误暴露。实现见 rs_client.py。

**9. partial_success 的当前语义**

如果模型输出结构合法，也没有互斥冲突，但缺少某些必打分类、低于 `min_items` 或高于 `max_items`，当前实现不会失败，而是：

```
result_status = partial_success
signals.success = false
仍然写 RS
Job 仍然 succeeded
CPP callback 仍然 job.succeeded
```

partial 的原因写在内部 canonical result 的 `signals.validation_issues` 和 `tagging_detail.validation_issues` 里。当前写给 RS 的 payload 使用兼容格式 `status/msg/t_book_id/job_id/tag_schema_version/tags`，不把 signals 额外塞进 RS payload。

**10. Finalize 和 CPP 可见结果**

work item 执行完成后，`finalize_job_task` 合并 single result，保存完整 `canonical_result`，然后调用 handler 的 `public_result()`。短剧 handler 设置了：

```python
expose_result_in_job_view = False
```

所以 CPP 轮询或 callback 看到的 `JobView.result` 固定是 `null`。实现见 workflow_registry.py、handler.py 和 job_workflow.py。

成功终态 CPP 看到：

```json
{
  "status": "succeeded",
  "result": null,
  "error": null
}
```

失败终态 CPP 看到：

```json
{
  "status": "failed",
  "result": null,
  "error": {
    "code": "MODEL_OUTPUT_INVALID",
    "message": "...",
    "details": {}
  }
}
```

**11. Callback CPP**

Job 进入 `succeeded` 或 `failed` 后，服务会投递 callback。callback body 里的 `job` 字段和 CPP 轮询接口返回的 JobView 同形，所以短剧打标成功时 callback 里也不会带标签结果。实现见 callbacks.py。

```
event = job.succeeded | job.failed
body.job = GET /jobs/{job_id} 的同形 JobView
```

**一句话总结**

当前代码已经实现的是：CPP 提交素材和语种创建异步 Job；AI 根据语种拉 RS 标签库，本地用 fixture、正式用 HTTP；AI 三阶段生成打标结果；adapter 转成 RS 兼容 payload；Job 成功终态先 callback CPP，再用同一份 canonical result 写 RS；CPP 始终只看 Job 生命周期，标签结果不通过 `JobView.result` 暴露。

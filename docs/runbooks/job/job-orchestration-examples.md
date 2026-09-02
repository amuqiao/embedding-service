# Job 编排与新增 Job 开发讲解

本文是一份独立讲解文档，用 `audio_stem_separation_triton` 和 `poster_title_image` 两个现有 Job 说明：本项目如何组织 Job、什么时候用单 executor、什么时候拆 root/child workflow、如何接入 tool，以及新增一个业务 Job 时应该怎么落代码。

## 一句话心智模型

本服务对外只暴露一个异步 Job 入口，但内部可以有两种实现方式：

```text
方式 A：一个 Job 自己完成全部步骤

Root Job
  -> executor
  -> 调工具 / 调模型 / 写结果
  -> succeeded / failed
```

```text
方式 B：一个 Root Job 编排多个 Child Job

Root Job
  -> workflow orchestration
  -> Child Job A
  -> Child Job B
  -> Child Job C
  -> Join Child 汇总
  -> Root Job succeeded / failed
```

对调用方来说，两种方式都还是：

```text
POST /jobs
  -> 返回 job_id
  -> GET /jobs/{job_id} 查询状态
  -> 成功后拿 job_result
```

区别只在服务内部如何拆步骤。

## 基础分层

新增 Job 时先记住这四层：

```text
Job executor
  负责一个业务步骤，例如音频分离、生成图片、汇总结果

Workflow
  负责把多个 executor 串起来、并行起来、最后汇总

Capability
  负责可复用的业务能力，例如准备音频输入、准备图片输入

Tool
  负责底层动作，例如读 OSS、解码音频、调用某个本地处理函数
```

推荐依赖方向：

```text
Job executor
  -> Capability
  -> Tool
  -> Integration / SDK / 外部服务
```

不要反过来让 tool 依赖 job。tool 应该小、稳定、可复用。

## 示例一：audio_stem_separation_triton

`audio_stem_separation_triton` 是单 executor 形态。它没有拆 child Job，而是在一个 root Job 的 executor 内部完成完整链路。

### 文字流程图

```text
Client
  |
  | POST /jobs
  | job_type = audio_stem_separation_triton
  v
Root Job
  |
  v
AudioStemSeparationTritonJob executor
  |
  +--> 读取输入音频
  |      |
  |      +--> 音频业务 storage adapter
  |            使用 app/object_storage 从 OSS 读取 bytes
  |
  +--> 解码和规范化
  |      |
  |      +--> audio_decode_normalize:1
  |            转成模型需要的 44.1kHz stereo float32
  |
  +--> 切 segment
  |
  +--> 调 Triton 模型
  |      |
  |      +--> htdemucs_ft_drums
  |      +--> htdemucs_ft_bass
  |      +--> htdemucs_ft_other
  |      +--> htdemucs_ft_vocals
  |
  +--> overlap-add 合并整首音频
  |
  +--> 写 4 条 WAV stem 到 OSS
  |
  v
Job succeeded
```

### 伪代码

```python
class AudioStemSeparationTritonJob(JobExecutor):
    name = "audio_stem_separation_triton"
    role = "root"
    visibility = "demo"
    required_tool_refs = {"audio_decode_normalize:1"}

    def normalize_job_params(self, job_params):
        params = validate_params(job_params)
        build_audio_input_plan(params.input_audio)
        return params

    def runtime_job_fields(self, job_params):
        params = validate_params(job_params)
        return {
            "media_input_plan": build_audio_input_plan(params.input_audio),
            "triton_model_version": settings.AUDIO_STEM_TRITON_MODEL_VERSION,
        }

    async def _execute(self, job, db):
        return await run_in_thread(self._execute_sync, job)

    def _execute_sync(self, job):
        runtime = load_runtime_fields(job)

        # 业务包 helper 读取 frozen plan，并调用 object_storage_read + audio_decode_normalize
        audio = prepare_audio_input(runtime["media_input_plan"])

        # provider adapter: 调 Triton HTTP endpoint
        runner = get_triton_runner()
        separated = runner.separate(audio.data)

        outputs = {}
        for stem in ["drums", "bass", "other", "vocals"]:
            wav = encode_wav(separated.stems[stem])
            outputs[stem] = storage.write_bytes(wav)

        return {
            "stems": outputs,
            "segment_count": separated.segment_count,
            "sample_rate": audio.sample_rate,
            "duration_ms": {
                "inference": separated.inference_ms,
                "total": elapsed_ms(),
            },
        }
```

### 这个例子说明什么

适合单 executor 的情况：

```text
步骤固定
步骤之间强绑定
没有必要把每个步骤暴露成独立 child Job
最终只需要一次性返回一个 result
失败时整个 Job 失败即可
```

`audio_stem_separation_triton` 虽然内部调用了多个 Triton 模型，但它仍然是一个 Job executor 内部的业务流程，不是 workflow 编排。

## 示例二：poster_title_image

`poster_title_image` 是 root/child workflow 形态。调用方提交一个 root Job，root Job 不直接生成图片，而是编排内部 child Job。

### 文字流程图

```text
Client
  |
  | POST /jobs
  | job_type = poster_title_image
  v
Root Job: poster_title_image
  |
  | workflow orchestration
  v
Child Jobs
  |
  +--> probe.0
  |      job_type = poster_title_image_style_probe
  |      读取参考图，生成 style_desc
  |
  +--> item.a
  |      job_type = poster_title_image_generate_item
  |      depends_on = probe.0
  |      根据 style_desc 生成透明标题图
  |
  +--> item.b
  |      job_type = poster_title_image_generate_item
  |      depends_on = probe.0
  |
  v
join
  job_type = poster_title_image_join
  汇总所有 item child 的结果
  |
  v
Root Job result
```

### 编排形态

它可以理解成：

```text
chord(
  group(
    probe.0,
    item.a depends_on probe.0,
    item.b depends_on probe.0
  ),
  join
)
```

也就是：

```text
先跑一组 child
  -> item child 可以依赖 probe child
  -> 所有需要的 child 完成后
  -> 跑 join child 汇总
```

### root executor 伪代码

root executor 不直接调模型。

```python
class PosterTitleImageJob(JobExecutor):
    name = "poster_title_image"
    role = "root"
    visibility = "public"

    def normalize_job_params(self, job_params):
        return validate_root_params(job_params)

    def runtime_job_fields(self, job_params):
        return {
            "image_adapter": resolve_image_adapter(),
            "generation_model_id": resolve_generation_model(job_params),
        }

    async def _execute(self, job, db):
        raise Error("root job must be executed by workflow orchestration")
```

root 的重点是定义参数、校验边界、冻结运行时字段。真正干活的是 child。

### workflow definition 伪代码

```python
def build_poster_title_image_workflow(root_params):
    probe_nodes = []
    item_nodes = []

    for item in root_params["items"]:
        probe_key = choose_or_reuse_probe_key(item)

        if probe_key not in probe_nodes:
            probe_nodes.append(
                task(
                    key=probe_key,
                    job_type="poster_title_image_style_probe",
                    job_params={
                        "reference_image": item["reference_image"],
                        "style_prompt": item["style_prompt"],
                    },
                )
            )

        item_nodes.append(
            task(
                key=f"item.{item['item_id']}",
                job_type="poster_title_image_generate_item",
                job_params={
                    "item": item,
                    "probe_node_key": probe_key,
                },
                depends_on=(probe_key,),
            )
        )

    return chord(
        group(*probe_nodes, *item_nodes),
        task(
            key="join",
            job_type="poster_title_image_join",
            job_params={"items": root_params["items"]},
        ),
    )
```

### child executor 伪代码

```python
class PosterTitleImageStyleProbeJob(JobExecutor):
    name = "poster_title_image_style_probe"
    role = "leaf"
    visibility = "internal"

    async def _execute(self, job, db):
        params = load_job_params(job)
        reference_image = read_reference_image(params["reference_image"])

        style_desc = generate_text_with_images_with_ledger(
            image=reference_image,
            prompt=params["style_prompt"],
        )

        return {
            "style_desc": style_desc,
        }
```

```python
class PosterTitleImageGenerateItemJob(JobExecutor):
    name = "poster_title_image_generate_item"
    role = "leaf"
    visibility = "internal"

    async def _execute(self, job, db):
        params = load_job_params(job)

        probe_child = find_child_by_node_key(params["probe_node_key"])
        style_desc = probe_child.result["style_desc"]

        image = generate_image_with_ledger(
            prompt=build_prompt(params["item"], style_desc),
        )

        transparent_png = remove_green_background(image)
        output_ref = storage.write_bytes(transparent_png)

        return {
            "item_id": params["item"]["item_id"],
            "image": output_ref,
        }
```

```python
class PosterTitleImageJoinJob(JobExecutor):
    name = "poster_title_image_join"
    role = "leaf"
    visibility = "internal"

    async def _execute(self, job, db):
        children = list_workflow_children(root_job_id=job.root_job_id)

        items = []
        for child in children:
            if child.job_type == "poster_title_image_generate_item":
                items.append(child.result)

        return {
            "batch_summary": {
                "total": len(items),
                "succeeded": len(items),
            },
            "items": items,
        }
```

### 这个例子说明什么

适合 workflow 的情况：

```text
一个 root 请求会展开成多个内部任务
有并行任务
有任务依赖
需要 join 汇总
需要按 child 定位问题
需要 running / failed 时也能看到部分成功结果
```

`poster_title_image` 不是“一个函数里从头跑到尾”，而是 root 负责编排，child 负责执行，join 负责汇总。

## 两种 Job 形态对比

| 维度 | 单 executor Job | root/child workflow Job |
|---|---|---|
| 代表例子 | `audio_stem_separation_triton` | `poster_title_image` |
| 对外入口 | 一个 root Job | 一个 root Job |
| 内部结构 | root executor 自己干活 | root 编排 child |
| 并行能力 | executor 自己写并发逻辑 | workflow group / chord |
| 汇总方式 | executor 内部汇总 | join child 汇总 |
| 状态粒度 | root 级 | root + child 级 |
| 代码数量 | 少 | 多 |
| 适合场景 | 固定流水线 | 多步骤、多 item、并行、汇总 |

判断规则：

```text
固定流程、没有独立步骤状态诉求
  -> 单 executor

多个 item 或多个模型步骤需要独立排查
  -> workflow

需要并行后汇总
  -> workflow chord + join

需要按列表展开任务
  -> workflow map / starmap / chunks

只是 executor 内部调用多个模型
  -> 不一定要 workflow
```

## 新增 Job：开发顺序

新增 Job 不要先写代码。先画出业务流程。

```text
输入是什么？
  |
  v
要经过哪些步骤？
  |
  v
步骤之间是串行、并行，还是并行后汇总？
  |
  v
每个步骤失败时，整个 Job 应该失败还是允许部分成功？
  |
  v
最终 result 返回什么？
```

然后选择形态：

```text
一个 executor 能清楚表达
  -> 新增普通 JobExecutor

需要 root/child
  -> 新增 root JobExecutor + child JobExecutors + workflow definition
```

## 新增普通 Job 示例

假设新增 `audio_report_triton`：

```text
audio_report_triton
  |
  +--> 读取音频
  +--> 解码音频
  +--> 调 Triton 做语音检测
  +--> 调 Triton 做音乐标签
  +--> 调 LLM 生成报告
  +--> 写报告到 OSS
  |
  v
result
```

目录可以这样放：

```text
app/business_packages/audio_report_triton/
  __init__.py
  executor.py
```

schema 放在统一 schema 区域：

```python
class AudioReportTritonParams(BaseModel):
    input_audio: OssUrlRef
    max_duration_seconds: float | None

class AudioReportTritonRuntimeFields(BaseModel):
    media_input_plan: AudioInputPlan
    triton_model_version: str

class AudioReportTritonResult(BaseModel):
    report: OssUrlRef
    tags: list[str]
    duration_ms: dict
```

executor 伪代码：

```python
class AudioReportTritonJob(JobExecutor):
    name = "audio_report_triton"
    role = "root"
    visibility = "public"
    params_schema = AudioReportTritonParams
    runtime_fields_schema_name = "AudioReportTritonRuntimeFields"
    canonical_result_schema = AudioReportTritonResult
    public_result_schema = AudioReportTritonResult
    required_tool_refs = {"audio_decode_normalize:1"}

    def normalize_job_params(self, job_params):
        params = AudioReportTritonParams.model_validate(job_params)
        build_audio_input_plan(params.input_audio)
        return params.model_dump()

    def runtime_job_fields(self, job_params):
        params = AudioReportTritonParams.model_validate(job_params)
        return {
            "media_input_plan": build_audio_input_plan(params.input_audio),
            "triton_model_version": settings.TRITON_MODEL_VERSION,
        }

    async def _execute(self, job, db):
        runtime = AudioReportTritonRuntimeFields.model_validate(runtime_fields_from_job(job))

        audio = prepare_audio_input(runtime.media_input_plan)

        speech = triton_client.infer("speech_detect", audio.data)
        tags = triton_client.infer("music_tag", audio.data)
        report_text = generate_text_with_ledger(build_report_prompt(speech, tags))

        report_ref = storage.write_bytes(report_text.encode("utf-8"))

        return {
            "report": report_ref,
            "tags": tags,
            "duration_ms": collect_duration(),
        }
```

注册伪代码：

```python
def register_all_job_types():
    register(AudioReportTritonJob())
```

## 新增 workflow Job 示例

假设新增 `video_analysis_report`：

```text
video_analysis_report root
  |
  v
prepare_media
  |
  v
group
  |
  +--> asr_triton
  +--> speaker_triton
  +--> scene_classifier
  |
  v
join_report
  |
  v
root result
```

目录可以这样放：

```text
app/business_packages/video_analysis_report/
  __init__.py
  executor.py
```

root executor：

```python
class VideoAnalysisReportJob(JobExecutor):
    name = "video_analysis_report"
    role = "root"
    visibility = "public"

    def normalize_job_params(self, job_params):
        return VideoAnalysisReportParams.model_validate(job_params).model_dump()

    def runtime_job_fields(self, job_params):
        return {
            "workflow_input": freeze_safe_input(job_params),
        }

    async def _execute(self, job, db):
        raise Error("root is orchestrated by workflow")
```

child executors：

```python
class PrepareMediaJob(JobExecutor):
    name = "video_analysis_prepare_media"
    role = "leaf"
    visibility = "internal"

    async def _execute(self, job, db):
        params = load_job_params(job)
        media = prepare_media(params["input_video"])
        return {"media_ref": media.ref}
```

```python
class AsrTritonJob(JobExecutor):
    name = "video_analysis_asr"
    role = "leaf"
    visibility = "internal"

    async def _execute(self, job, db):
        params = load_job_params(job)
        media = read_prepared_media(params["media_ref"])
        transcript = triton_client.infer("asr", media.audio)
        return {"transcript": transcript}
```

```python
class JoinReportJob(JobExecutor):
    name = "video_analysis_join"
    role = "leaf"
    visibility = "internal"

    async def _execute(self, job, db):
        children = list_workflow_children(job.root_job_id)

        transcript = find_result(children, "video_analysis_asr")
        speakers = find_result(children, "video_analysis_speaker")
        scenes = find_result(children, "video_analysis_scene")

        return {
            "transcript": transcript,
            "speakers": speakers,
            "scenes": scenes,
        }
```

workflow definition：

```python
def build_video_analysis_workflow(root_params):
    prepare = task(
        key="prepare",
        job_type="video_analysis_prepare_media",
        job_params={"input_video": root_params["input_video"]},
    )

    analysis = group(
        task(
            key="asr",
            job_type="video_analysis_asr",
            job_params={"media_ref": "{{prepare.media_ref}}"},
            depends_on=("prepare",),
        ),
        task(
            key="speaker",
            job_type="video_analysis_speaker",
            job_params={"media_ref": "{{prepare.media_ref}}"},
            depends_on=("prepare",),
        ),
        task(
            key="scene",
            job_type="video_analysis_scene",
            job_params={"media_ref": "{{prepare.media_ref}}"},
            depends_on=("prepare",),
        ),
    )

    join = task(
        key="join",
        job_type="video_analysis_join",
        job_params={},
    )

    return chain(
        prepare,
        chord(analysis, join),
    )
```

注意：上面的 `"{{prepare.media_ref}}"` 只是讲解占位。真实实现里要用本项目已有的 runtime snapshot、child result 查询或稳定参数传递方式表达，不要引入字符串模板魔法。

注册 workflow：

```python
def register_video_analysis_workflow():
    register_workflow(
        WorkflowDefinition(
            workflow_type="video_analysis_report",
            build=build_video_analysis_workflow,
            failure_policy="fail_fast",
            max_nodes=100,
        )
    )
```

注册所有 executor 和 workflow：

```python
def register_all_job_types():
    register(VideoAnalysisReportJob())
    register(PrepareMediaJob())
    register(AsrTritonJob())
    register(SpeakerTritonJob())
    register(SceneClassifierJob())
    register(JoinReportJob())

    register_video_analysis_workflow()
```

## 如何接入 tool

tool 用来表达底层动作。比如：

```text
读对象存储
解码音频
抽取视频帧
调用本地二进制
解析媒体元信息
```

新增 tool 的形态：

```text
app/tools/video_frame.py
  extract_video_frames()

app/tools/register.py
  ToolDefinition(
    tool_ref="video_frame_extract:1",
    kind="media_transform",
    entrypoint_path="app.tools.video_frame:extract_video_frames",
    request_schema="VideoFrameExtractRequest",
    result_schema="VideoFrameExtractResult",
    error_codes={...},
  )
```

tool 实现伪代码：

```python
def extract_video_frames(request):
    req = VideoFrameExtractRequest.model_validate(request)

    if req.max_frames <= 0:
        raise AppError("VIDEO_FRAME_INVALID", "max_frames must be positive")

    frames = ffmpeg_extract_frames(
        data=req.data,
        max_frames=req.max_frames,
        every_seconds=req.every_seconds,
    )

    return VideoFrameExtractResult(
        frames=frames,
        frame_count=len(frames),
    )
```

tool 注册伪代码：

```python
register(
    ToolDefinition(
        tool_ref="video_frame_extract:1",
        kind="media_transform",
        entrypoint_path="app.tools.video_frame:extract_video_frames",
        request_schema="VideoFrameExtractRequest",
        result_schema="VideoFrameExtractResult",
        error_codes={
            "VIDEO_FRAME_INVALID",
            "VIDEO_FRAME_RUNTIME_UNAVAILABLE",
        },
    )
)
```

不要在业务 executor 里直接创建 `ToolDefinition`。统一放注册入口，便于 registry 检查和工具清单展示。

## 如何接入 tool

tool 用来封装可复用的底层执行边界。多个 tool 的组合逻辑应放在业务包自己的 helper / adapter 中，不再抽成跨业务复合 capability。

例如当前音频输入处理的心智模型是：

```text
prepare_audio_input(plan)
  |
  +--> app/object_storage
  |     由业务 adapter 读取 OSS bytes
  |
  +--> audio_decode_normalize:1
        解码、校验、规范化
  |
  v
PreparedAudioInput
```

新增视频输入处理可以类似这样：

```text
business_packages/video_analysis/input_adapter.py
  -> app/object_storage
  -> video_probe:1
  -> video_frame_extract:1
  -> PreparedVideoInput
```

业务包 helper 伪代码：

```python
def prepare_video_input(plan):
    snapshot = VideoInputPlan.model_validate(plan)

    data = read_object_bytes(
        bucket=snapshot.source.bucket,
        key=snapshot.source.key,
        max_bytes=snapshot.fetch.max_bytes,
    )

    metadata = probe_video({
        "data": data,
        "content_type": snapshot.source.content_type,
    })

    frames = extract_video_frames({
        "data": data,
        "max_frames": snapshot.decode.max_frames,
    })

    return PreparedVideoInput(
        metadata=metadata,
        frames=frames,
    )
```

job executor 声明实际依赖的 tools：

```python
class VideoAnalysisReportJob(JobExecutor):
    required_tool_refs = {
        "video_probe:1",
        "video_frame_extract:1",
    }

    def runtime_job_fields(self, job_params):
        return {
            "video_input_plan": build_video_input_plan(job_params["input_video"]),
        }

    async def _execute(self, job, db):
        runtime = runtime_fields_from_job(job)
        video = prepare_video_input(runtime["video_input_plan"])
        ...
```

如果多个业务包确实长期复用同一段组合逻辑，优先抽成 `app/tools/private/` 下的小工具函数；只有底层动作需要进入注册图时，才新增 `ToolDefinition`。

## 新增 Job 的开发清单

普通 Job：

```text
1. 定义 Params / RuntimeFields / Result schema
2. 实现 JobExecutor
3. normalize_job_params() 做入参规范化
4. runtime_job_fields() 冻结执行期需要的 plan / model / adapter
5. _execute() 执行业务逻辑
6. 如需复用底层动作，接 tool 或业务包内 helper
7. 在注册入口注册 JobExecutor
8. 补测试和最小验证
```

workflow Job：

```text
1. 定义 root Params / RuntimeFields / Result schema
2. 定义每个 child 的 Params / RuntimeFields / Result schema
3. 实现 root JobExecutor
4. 实现 child JobExecutors
5. 编写 workflow definition
6. 用 task / chain / group / chord / map / starmap / chunks 表达结构
7. 实现 join child 汇总结果
8. 注册 root、children 和 workflow definition
9. 补 workflow 编排、child 执行和 root result 测试
```

tool：

```text
1. 底层动作放 tool
2. 多个 tool 的组合逻辑放业务包 helper / adapter
3. Job executor 声明 required_tool_refs
4. helper 读取 frozen plan，不直接改 Job 状态
5. tool 不依赖 Job，也不写业务状态
```

## 最后判断

新增 Job 时可以用这张图做最终判断：

```text
我要做一个新业务 Job
  |
  +-- 只是固定流水线？
  |     |
  |     v
  |   单 executor
  |
  +-- 有多个 item / 多模型并行 / 需要 join？
  |     |
  |     v
  |   workflow root + child jobs
  |
  +-- 有底层能力可复用？
        |
        v
      tool + 业务包 helper
```

最常见的落地顺序是：

```text
先做单 executor 跑通业务
  -> 如果后续需要并行、step 状态、部分结果或更强排障
  -> 再升级为 root/child workflow
```

不要为了“复杂模型调度”一开始就拆很多 child。拆分应该服务于可观察性、并行、复用和汇总，而不是服务于形式。

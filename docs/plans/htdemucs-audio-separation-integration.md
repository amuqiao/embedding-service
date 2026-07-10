# HTDemucs-FT 音乐源分离接入计划

本文只记录接入 `audio_stem_separation` job_type（htdemucs-ft ONNX 音乐源分离模型）尚未实现、值得做的工作。当前没有对应的 `docs/current/` 事实文档——这个能力现在还不存在。模型背景资料见 `docs/notes/htdemucs-ft-onnx-指南.html`；新增 job_type 的标准流程见 [`../api/extension-guide.md`](../api/extension-guide.md)。

本阶段范围只覆盖**本地 macOS CPU 闭环**：数据准备 → 发起 job 请求 → 输入到输出。GPU 迁移、生产部署、镜像拆分是后续阶段，本文只在 Remaining Gaps 点出，不展开设计。

## Current Baseline

- `app/jobs/types/poster_title_image/` 是当前唯一的正式业务包目录范式（`executor.py` + `errors.py` + `models.yaml`/`prompts.yaml`），可作为目录结构参照，但它是 workflow root（`role="root"`，`_execute()` 直接 `raise JOB_RUNTIME_NOT_SUPPORTED`，实际执行由 chord/group workflow 驱动）。`audio_stem_separation` 不需要这套编排——它是单一原子推理步骤，直接在 `_execute()` 内完成分段、4 个专家 ONNX 推理、官方 bag 聚合与 overlap-add，不注册 workflow definition。
- `app/jobs/adapters/oss_url_ref.py` 的 `canonical_ref_from_oss_url_ref()` / `oss_url_ref_from_output_object()` 已经是本仓库校验和构造 OSS 引用对象的统一入口，`poster_title_image` 的 reference/输出图片已经在用。
- `app/jobs/base.py` 的 `JobExecutor.timeout_seconds`（默认 300）会被 `app/services/jobs.py:414`（`int(getattr(handler, "timeout_seconds", ...))`）读出，并经 `JobRepo.create_initial_attempt`（`app/repositories/job_repo.py:353-391`）写入 `JobAttempt.timeout_seconds` 列（`app/models/job.py:218`，`NOT NULL`）。这一列**已经落库**，但当前没有任何读路径使用它。
- `app/tasks/jobs.py` 的 `run_job_attempt()` 调用 `JobRepo.claim_attempt_for_execution(..., lease_seconds=settings.job_stale_running_seconds)`，`heartbeat()` 闭包调用 `JobRepo.heartbeat_attempt(..., lease_seconds=settings.job_stale_running_seconds)`——两处都只用全局 `settings.job_stale_running_seconds`，不读刚才存的 per-attempt `timeout_seconds`。
- `settings.job_stale_running_seconds` 完全从 LLM 语义的 `MODEL_CALL_TIMEOUT_SECONDS` 派生（`app/core/config.py`：`worker_soft_time_limit = model_call_timeout_seconds + 300`，`worker_hard_time_limit = soft + 60`，`job_stale_running_seconds = hard + 600`），全服务所有 job_type 共享同一个值。
- `JobRepo.claim_attempt_for_execution()` 和 `JobRepo.heartbeat_attempt()`（`app/repositories/job_repo.py:739-833`）在各自的 SQL 查询里都已经把 `JobAttempt` 整行（含 `timeout_seconds`）连同 `Job` 一起加锁读出，只是读出后没有使用该字段——这意味着两处的修复不需要改变调用方签名或新增查询。
- 这个"声明了 per-job_type timeout_seconds、但运行时 lease 窗口不读它"的缺口，已经是 `docs/plans/job-kernel-reliability-review.md` 风险分级表中标注的 P1 条目（原文：「长模型调用期间没有周期性 lease 续约，`update_progress` 不延长 `lease_expires_at`」），且其"分面审查 § 3"进一步指出 `run_job_attempt` 只在执行前 heartbeat 一次，long-running `_execute()` 期间没有周期续约。
- `scripts/models.sh` 已提供本地模型资产入口，当前已知 `htdemucs-ft` 会下载到 `.data/models/htdemucs-ft` 并检查 4 个专家 ONNX 文件、`bag_infer.py` 和 `requirements.txt`。
- `pyproject.toml` 当前没有 `[project.optional-dependencies]` extra；`onnxruntime`、`soundfile` 均不在依赖列表中。

## Remaining Gaps

- ONNX 模型形态已明确不是单个 `htdemucs_ft.onnx`：Hugging Face `StemSplitio/htdemucs-ft-onnx` 落地为 4 个专家文件 `htdemucs_ft_drums.onnx`、`htdemucs_ft_bass.onnx`、`htdemucs_ft_other.onnx`、`htdemucs_ft_vocals.onnx`，并提供 `bag_infer.py` 作为官方聚合推理参考。已知每个专家模型输入 `mix` 形状为 `(1, 2, 343980)`，输出 `stems` 形状为 `(1, 4, 2, 343980)`；仍需在本地探测并记录每个文件的 I/O 签名、sha256、导出版本和官方 bag 逻辑对应关系。
- 模型权重尚未落地：需要从 Hugging Face `StemSplitio/htdemucs-ft-onnx` 下载到本地目录，实施侧下载入口按 `./scripts/models.sh download htdemucs-ft` 使用。
- lease 窗口与 per-job_type `timeout_seconds` 脱钩：这是本次接入发现的、独立于 htdemucs 本身的 job kernel 缺陷，任何声明了较长 `timeout_seconds` 的 job_type 都会受影响，不只是这一个 job_type。
- `visibility="demo"` 语义借用：`docs/api/extension-guide.md` 定义 `demo` 是"模板示例、smoke 或压测入口，不是正式业务合同"，而 htdemucs 分离是用户想要的真实产品能力。阶段性借用 `demo` 只是为了复用它"仅 `APP_ENV=local/dev` 允许外部提交"的准入限制，不代表这是模板示例功能。
- 运行时依赖（`onnxruntime`、`soundfile`）和系统库（`libsndfile1`）尚未接入 `pyproject.toml` / Dockerfile。
- GPU 执行、镜像拆分（api/worker 分离）、生产部署、分段级周期续约心跳机制均不在本阶段范围（见 Non-goals）。

## Planned Work

以下步骤按依赖关系分组；同组内可并行，组间必须按顺序推进。

### 阶段 0：探测先行（阻塞 schema 定稿，必须最先做）

1. **ONNX I/O 签名探测**：已知模型是 4 文件专家袋，不再探测"是否单文件/是否多文件"。本阶段要用 `onnxruntime.InferenceSession(...).get_inputs()/get_outputs()` 分别实测 `htdemucs_ft_drums.onnx`、`htdemucs_ft_bass.onnx`、`htdemucs_ft_other.onnx`、`htdemucs_ft_vocals.onnx`，确认并记录每个专家的输入 `mix`、输出 `stems`、dtype、固定窗口长度 `343980`，同时计算 sha256；再对照官方 `bag_infer.py` 记录 executor 需要复刻的 bag 聚合逻辑。这一步必须排在 params/result schema、`model_asset.yaml` 和 timeout 预算定稿之前。
2. 与探测并行、无依赖：把 htdemucs-ft ONNX 权重从 Hugging Face `StemSplitio/htdemucs-ft-onnx` 下载到本地目录；实施侧下载入口按 `./scripts/models.sh download htdemucs-ft` 使用，本计划不修改脚本。

### 阶段 1：两条互不依赖的主线（阶段 0 完成后并行推进）

**主线 A：job_type 实现**（依赖阶段 0 的 I/O 签名探测结果）

3. 在 `app/schemas/jobs.py` 定义 Params（`input_audio: OssUrlRef`，仅允许 `audio/wav`，可选 `audio/flac`；拒绝非 44.1kHz 立体声输入，不做静默重采样/转码；`max_duration_seconds` 上限拒绝超限输入，不做静默截断）、Runtime fields（`onnx_model_version`、`execution_provider`、`segment_seconds`/`overlap_ratio`）和 Result schema（`stems: {drums, bass, other, vocals}`，每个是 `PosterTitleImageObject` 风格的 OssUrlRef 对象；`duration_ms: {inference, io, total}`；`source_duration_seconds`）。
4. 新建 `app/jobs/types/audio_stem_separation/` 包目录：
   - `__init__.py`：导出执行器供 `register.py` 导入。
   - `executor.py`：单一 `AudioStemSeparationJob(JobExecutor)`，`name="audio_stem_separation"`、`role="root"`、`visibility="demo"`（见下方决策说明）；`_execute()` 内部加载 4 个 ONNX session，按分段（7.8 秒窗口 / 25% 重叠）对每段执行 4 次专家推理，再按官方 `bag_infer.py` 逻辑聚合并做三角窗 overlap-add 拼接，不注册 workflow definition。
   - `errors.py`：`AUDIO_STEM_INPUT_INVALID`、`AUDIO_STEM_DURATION_EXCEEDS_LIMIT`、`AUDIO_STEM_MODEL_ASSET_MISSING`、`AUDIO_STEM_INFERENCE_FAILED`、`AUDIO_STEM_OUTPUT_INVALID`。
   - `model_asset.yaml`：记录 4 个本地 ONNX 专家文件的版本标识、相对路径、sha256、导出时的 I/O 签名说明，以及官方 `bag_infer.py` 聚合参考（阶段 0 探测结果落地于此）。语义上不复用 `models.yaml`——`models.yaml` 表达"可外部选择的 LLM provider 模型目录"，本场景是固定本地推理权重，两者语义不同。
   - 不新增 `prompts.yaml`（没有 LLM 调用）。
5. Params 校验复用 `app/jobs/adapters/oss_url_ref.py` 的 `canonical_ref_from_oss_url_ref()`；输出对象用 `oss_url_ref_from_output_object()` 构造，参照 `PosterTitleImageObject` 的用法。
6. `retry_policy` 声明 `business_execution.max_attempts=1`（本地确定性推理失败重试不会自愈，等同 CPU 一次多分钟的算力代价）。
7. `timeout_seconds` 基于"目标测试音频时长 × 预估 CPU 推理倍率 × 每段 4 次专家推理"估算并声明（`docs/notes/htdemucs-ft-onnx-指南.html` 给出的经验参考是 CPU 上可能到数分钟量级/4 分钟歌曲；htdemucs_ft 相比基础版慢约 4 倍），并留出官方 bag 聚合和 overlap-add 的余量。
8. 在 `app/jobs/types/register.py` 显式导入并注册。
9. 补充 schema、registry、params 校验和最小 job 执行测试。

**主线 B：lease 窗口修复**（不依赖主线 A 的实现细节，可与主线 A 同时进行；只依赖阶段 0 探测结果里的 timeout 估算方法定型，用于验证效果）

10. 修改 `app/repositories/job_repo.py` 的 `claim_attempt_for_execution()` 和 `heartbeat_attempt()`：两处已经在同一次查询里把 `JobAttempt`（含 `timeout_seconds` 列）连同 `Job` 一并锁定读出，只需在设置 `attempt.lease_expires_at` 前，把调用方传入的 `lease_seconds`（全局 floor）与 `attempt.timeout_seconds` 取较大值：`effective_lease_seconds = max(lease_seconds, attempt.timeout_seconds)`。不新增配置项，不改 `_execute()` 契约，不下传 `lease_token` 以外的新参数，调用方 `app/tasks/jobs.py` 的 `run_job_attempt()` 无需修改。
11. 补充测试：验证声明了较大 `timeout_seconds` 的 job_type（可用 `audio_stem_separation` 或测试专用 job_type）claim/heartbeat 后的实际 `lease_expires_at` 使用了 attempt 自身的 `timeout_seconds`，而非全局 floor；同时验证未声明或声明值更小的 job_type 仍落回全局 floor，不受影响。

### 阶段 2：依赖阶段 1 两条主线都完成

12. 运行 `./scripts/dev.sh start` + `./scripts/verify.sh workflow-smoke`，确认 lease 修复不影响现有 job_type（尤其 `example_*`、`job_real_llm_*`）的行为。
13. 用真实下载好的模型权重和一段本地转换好的测试音频，手动跑一次完整闭环（提交 job → 轮询/等待 callback → 下载四条 stem 产物），验证端到端可用。这一步依赖用户自行完成的数据准备（见下方前置条件）和模型下载（步骤 2）。
14. 把 `docs/plans/job-kernel-reliability-review.md` 对应 P1 条目（长模型调用期间没有周期性 lease 续约）的状态更新为「已部分缓解（per-attempt lease 窗口生效），周期性续约仍未实现，留给未来长任务/GPU 阶段」，不标记为完全解决。

### 数据准备前置条件（阻塞阶段 2 的手动验证，用户自行完成）

用户自行准备的测试音频大概率是 mp3 格式或非 44.1kHz 采样率，而本 job_type 拒绝这类输入且不做转码。数据准备阶段应先用本地媒体素材入口转换并校验：

```
./scripts/media.sh audio prepare htdemucs-input input.mp3 --output .data/audio/input.wav
./scripts/media.sh audio verify htdemucs-input .data/audio/input.wav
```

`prepare htdemucs-input` 底层会调用 `ffmpeg` 生成 44.1kHz 双声道 WAV，并调用 `ffprobe` 校验产物；`verify htdemucs-input` 底层也会调用 `ffprobe` 检查容器、采样率和声道。将输入转换为 44.1kHz 立体声 WAV 后再上传，否则请求会在 params 校验阶段被 `AUDIO_STEM_INPUT_INVALID` 拒绝。

### 运行时依赖与模型文件落地（可与阶段 1 并行，无强依赖）

- 在 `pyproject.toml` 新增一个 `[project.optional-dependencies]` extra（例如 `audio-separation`），只包含 `onnxruntime`（CPU 版）和 `soundfile`，不进 base 依赖——api/worker 现在共用同一个环境，但只有 worker 会跑推理；`onnxruntime` 和 `onnxruntime-gpu` 是互斥的两个 PyPI 包，extra 组为未来 GPU 阶段换包留出干净接口。
- Dockerfile 本阶段不改（不装 `libsndfile1`）：本地验证走 `local` 模式，宿主机裸跑 worker 不经过 Dockerfile，用户自行用 Homebrew 装系统依赖；等 `compose-full` 或 GPU 阶段需要时再补。
- 新增一个目录级配置变量（例如 `HTDEMUCS_MODEL_DIR`），默认落在 `.data/models/htdemucs-ft` 下（`.data/` 是仓库既有的"本地验证输入，不提交"约定目录）。配置只表达"模型文件在哪个目录"这一稳定意图，不暴露具体文件名/版本号；executor 内部按 `model_asset.yaml` 记录的 4 个专家文件规则在该目录下查找并校验 sha256，缺失时 fail-fast 报 `AUDIO_STEM_MODEL_ASSET_MISSING`，不做静默降级。未来迁移到 GPU 服务器时只需改这一个路径值。
- execution provider 用 `onnxruntime.get_available_providers()` 在启动/首次调用时自动探测，构造优先级列表（CUDA 优先、CPU 兜底），并用于初始化 4 个专家 ONNX session；不新增配置开关——当前机器只有 `CPUExecutionProvider` 一种可行值，配置开关形同虚设。
- Worker/api 镜像本阶段不拆分，维持共用现状。

### 需要用户拍板的开放决策

`visibility`/`role` 不在本计划中替用户定死：

- 推荐阶段性使用 `visibility="demo"` + `role="root"`——先验证不承诺外部合同。
- 但 `docs/api/extension-guide.md` 里 `demo` 的语义是"模板示例、smoke 或压测入口，不是正式业务合同"；htdemucs 分离是用户想要的真实产品能力，不是模板占位示例。借用 `demo` 只是为了限制"只有 local/dev 环境能外部提交"这个效果，不代表这是模板示例功能。验证稳定后需要重新评估升级为 `public`。

### 一致性说明（避免实现阶段误解）

- 4 个 onnx session 初始化耗时（无论是首次加载还是模块级单例复用后的调用）天然计入 `_execute()` 所在 attempt 的 `timeout_seconds` 预算内，不需要单独预留一段"模型加载"超时。这一点必须在 executor 实现前明确，避免后来实现者误加多余的超时预算。
- lease 窗口修复只是让"声明超时"和"生效 lease"对齐，**不是**恢复了分段级周期续约——如果 worker 进程在推理中途真崩溃，stale 检测延迟会变成 `timeout_seconds` 量级（本 job_type 约 2400 秒级）而不是原来更紧的全局窗口。OSS 产物写入、callback 投递仍需依赖仓库既有幂等键机制兜底，不能假设 lease 是唯一防线。真正的分段级周期续约（扩展 `_execute()` 契约下传心跳回调）评估后决定本次不做，原因是这是影响所有 job_type 共用调用链的 kernel 级改动，风险收益在"只做本地验证"阶段不成比例，留作 GPU 阶段的独立后续项。
- 落地后应把 `docs/plans/job-kernel-reliability-review.md` 对应那条 P1（长模型调用期间没有周期性 lease 续约）的状态更新为「已部分缓解（per-attempt lease 窗口生效），周期性续约仍未实现，留给未来长任务/GPU 阶段」，不应标记为完全解决。本文不重复维护该文件的完整风险清单，只在这里引用并说明本计划对它的影响。

## Acceptance

- 阶段 0 探测结果（4 个专家 ONNX 文件的 I/O 签名、sha256、固定窗口长度、官方 `bag_infer.py` 聚合逻辑对应关系）已经写入 `model_asset.yaml`，且 params/result schema、runtime fields 和 timeout 估算都基于这份实测结果，而不是基于单文件 `htdemucs_ft.onnx` 假设。
- `audio_stem_separation` 声明的 `timeout_seconds` 在实际运行中生效为 attempt 的 lease 窗口（可通过日志或直接查询 `job_execution_attempts.lease_expires_at` 验证），其他未声明或声明值更小的 job_type 的 lease 行为不受影响。
- 用一段本地转换好的 44.1kHz 立体声 WAV 测试音频，能完整跑通提交 job → 执行 → 四条 stem 产物写入对象存储 → 可下载/可播放，无接缝噪声。
- 非法输入（错误采样率、错误声道数、超限时长、非白名单 content_type）在 params 校验阶段即被拒绝，返回对应的 `AUDIO_STEM_*` 错误码，不做静默转码或截断。
- 模型权重缺失或 sha256 不匹配时，job 执行 fail-fast 报 `AUDIO_STEM_MODEL_ASSET_MISSING`，不产生部分成功或伪造结果。
- `./scripts/verify.sh check` 通过；`./scripts/dev.sh start` + `./scripts/verify.sh workflow-smoke` 通过，证明 lease 修复未破坏现有 job_type 行为。
- `docs/plans/job-kernel-reliability-review.md` 对应 P1 条目的状态已经按上文口径更新为"部分缓解"，而不是本计划自行重写该文件的风险清单。

## Non-goals

- GPU 部署形态（`onnxruntime-gpu`、CUDA/TensorRT execution provider 实测）不在本阶段范围，只在 Remaining Gaps 中点出为后续阶段。
- 生产部署、阿里云 PAI-EAS/ACR/OSS 拓扑、K8s 资源不在本仓库边界内（参见 `AGENTS.md` 项目边界）。
- 镜像拆分（api/worker 分离）不在本阶段范围，留到 GPU 阶段或镜像体积真正成为问题时再评估。
- 分段级周期续约心跳机制（扩展 `_execute()` 契约、下传心跳回调）本阶段评估后决定不做，原因见上文"一致性说明"。
- `visibility="demo"` 到 `visibility="public"` 的正式升级不在本阶段范围，留到验证稳定后另行评估。
- 不新增 `shifts`（多次时移推理取平均）之类的质量增强参数；本阶段只验证单次推理闭环。
- 不在本计划中实现 `audio_stem_separation` 的 schema、executor、lease 修复或真实推理代码；模型下载入口已由 `scripts/models.sh` 提供。

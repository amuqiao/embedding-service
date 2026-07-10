# HTDemucs-FT 音乐源分离接入计划

本文只记录 `audio_stem_separation` job_type（htdemucs-ft ONNX 音乐源分离模型）接入后的剩余工作。当前 job_type、schema、executor、错误码、模型资产描述和单元测试已落地；模型背景资料见 `docs/notes/htdemucs-ft-onnx-指南.html`；新增 job_type 的标准流程见 [`../api/extension-guide.md`](../api/extension-guide.md)。

本阶段范围只覆盖**本地 macOS CPU 闭环**：数据准备 → 发起 job 请求 → 输入到输出。GPU 迁移、生产部署、镜像拆分是后续阶段，本文只在 Remaining Gaps 点出，不展开设计。

## Current Baseline

- `app/jobs/types/poster_title_image/` 是当前唯一的正式业务包目录范式（`executor.py` + `errors.py` + `models.yaml`/`prompts.yaml`），可作为目录结构参照，但它是 workflow root（`role="root"`，`_execute()` 直接 `raise JOB_RUNTIME_NOT_SUPPORTED`，实际执行由 chord/group workflow 驱动）。`audio_stem_separation` 不需要这套编排——它是单一原子推理步骤，直接在 `_execute()` 内完成分段、4 个专家 ONNX 推理、官方 one-hot bag 聚合与 overlap-add，不注册 workflow definition。
- `app/jobs/adapters/oss_url_ref.py` 的 `canonical_ref_from_oss_url_ref()` / `oss_url_ref_from_output_object()` 已经是本仓库校验和构造 OSS 引用对象的统一入口，`poster_title_image` 的 reference/输出图片已经在用。
- `app/jobs/base.py` 的 `JobExecutor.timeout_seconds`（默认 300）会被 `app/services/jobs.py` 读出，并经 `JobRepo.create_initial_attempt` 写入 `JobAttempt.timeout_seconds` 列（`app/models/job.py`，`NOT NULL`）。
- `JobRepo.claim_attempt_for_execution()` 和 `JobRepo.heartbeat_attempt()` 设置 `lease_expires_at` 时，使用 `max(settings.job_stale_running_seconds, attempt.timeout_seconds)`：job_type 声明的较长 `timeout_seconds` 可以拉长 attempt lease，但不能缩短全局保护窗口。
- `settings.job_stale_running_seconds` 仍从 LLM 语义的 `MODEL_CALL_TIMEOUT_SECONDS` 派生（`app/core/config.py`：`worker_soft_time_limit = model_call_timeout_seconds + 300`，`worker_hard_time_limit = soft + 60`，`job_stale_running_seconds = hard + 600`），作为全服务共享的 attempt lease floor。
- 这个修复只让 per-attempt timeout 参与 claim / heartbeat 的 lease 计算；`run_job_attempt` 仍只在执行前 heartbeat 一次，long-running `_execute()` 期间没有周期续约。`docs/plans/job-kernel-reliability-review.md` 对应 P1 风险只能标记为部分缓解，不能标记为完全解决。
- `scripts/models.sh` 已提供本地模型资产入口，当前已知 `htdemucs-ft` 会下载到 `.data/models/htdemucs-ft` 并检查 4 个专家 ONNX 文件、`bag_infer.py` 和 `requirements.txt`；`inspect htdemucs-ft` 可探测 4 个 ONNX 专家文件的 I/O 签名和 sha256。
- 本地 `htdemucs-ft` required 模型文件已下载完成；`./scripts/models.sh inspect htdemucs-ft --providers CPUExecutionProvider` 已实测 4 个专家 ONNX 都是输入 `mix tensor(float) [1, 2, 343980]`、输出 `stems tensor(float) [1, 4, 2, 343980]`，并已记录 sha256。
- `pyproject.toml` 已有 `[project.optional-dependencies] audio-separation`，当前固定 `onnxruntime==1.27.0` 和 `soundfile==0.14.0`；本机 macOS 已验证两个包可 import。Dockerfile 尚未安装 `libsndfile1`，compose-full / GPU 阶段需要时再补。
- 本地测试音频 `.data/misc/2485_0003_S6_梁萧.wav` 已通过 `./scripts/media.sh audio verify htdemucs-input`，格式为 WAV、44.1kHz、双声道。
- 参考项目 `/Users/admin/Downloads/Code/cms-video-analyzer-triton-master` 只作为 ONNX 模型接入查漏补缺来源；本阶段不引入 Triton server、Triton model repository 或 GPU server 拓扑。
- `app/jobs/types/audio_stem_separation/` 已新增单一 custom executor：读取 OSS WAV 输入，校验 44.1kHz 双声道，加载 4 个 htdemucs-ft ONNX 专家，按 7.8 秒窗口和 25% overlap 做分段推理，输出 drums/bass/other/vocals 四条 WAV OSS 引用。
- `app/jobs/types/audio_stem_separation/model_asset.yaml` 已固化 4 个 ONNX 文件名、sha256、I/O 签名、stem 行映射、官方 `bag_infer.py` 的 one-hot target-row 聚合口径和固定 runtime 参数。
- `.env.example` / `Settings` 已暴露 `AUDIO_STEM_SEPARATION_ALLOWED_OSS_BUCKETS`、`AUDIO_STEM_SEPARATION_ALLOWED_OSS_REGIONS` 和 `HTDEMUCS_MODEL_DIR`。

## Remaining Gaps

- 真实模型端到端闭环尚未跑：还需要用已下载权重和本地测试 WAV 提交一次实际 Job，确认四条 WAV 可下载/可播放。
- 长执行期间没有周期性 heartbeat：per-attempt `timeout_seconds` 已参与 claim / heartbeat 的 lease 计算，但 `_execute()` 内部不会周期性延长 `lease_expires_at`。本地 CPU 闭环阶段接受这个边界；GPU / 生产阶段再独立评估分段级续约。
- `visibility="demo"` 语义借用：`docs/api/extension-guide.md` 定义 `demo` 是"模板示例、smoke 或压测入口，不是正式业务合同"，而 htdemucs 分离是用户想要的真实产品能力。阶段性借用 `demo` 只是为了复用它"仅 `APP_ENV=local/dev` 允许外部提交"的准入限制，不代表这是模板示例功能。
- 容器系统依赖尚未接入：Python 侧 `audio-separation` extra 已包含 `onnxruntime` 和 `soundfile`；Dockerfile 尚未安装 `libsndfile1`，compose-full / GPU 阶段需要时再补。
- GPU 执行、镜像拆分（api/worker 分离）、生产部署、分段级周期续约心跳机制均不在本阶段范围（见 Non-goals）。

## Planned Work

以下步骤按依赖关系分组；同组内可并行，组间必须按顺序推进。

### 阶段 1：真实闭环验证

1. 运行 `./scripts/dev.sh start` + `./scripts/verify.sh workflow-smoke`，确认新增 job_type 不影响现有 job_type（尤其 `example_*`、`job_real_llm_*`）的行为。
2. 用真实下载好的模型权重和一段本地转换好的测试音频，手动跑一次完整闭环（提交 job → 轮询/等待 callback → 下载四条 stem 产物），验证端到端可用。

### 数据准备前置条件（阻塞阶段 1 的手动验证，用户自行完成）

用户自行准备的测试音频大概率是 mp3 格式或非 44.1kHz 采样率，而本 job_type 拒绝这类输入且不做转码。数据准备阶段应先用本地媒体素材入口转换并校验：

```
./scripts/media.sh audio prepare htdemucs-input input.mp3 --output .data/audio/input.wav
./scripts/media.sh audio verify htdemucs-input .data/audio/input.wav
```

`prepare htdemucs-input` 底层会调用 `ffmpeg` 生成 44.1kHz 双声道 WAV，并调用 `ffprobe` 校验产物；`verify htdemucs-input` 底层也会调用 `ffprobe` 检查容器、采样率和声道。将输入转换为 44.1kHz 立体声 WAV 后再上传；创建 Job 阶段只能校验 ref 结构、白名单和 `audio/wav` content type，采样率、声道和可解码性会在执行期读取 WAV 后校验，失败时报 `AUDIO_STEM_INPUT_INVALID`。

### 运行时依赖与模型文件落地（可与阶段 1 并行，无强依赖）

- `pyproject.toml` 已新增 `[project.optional-dependencies] audio-separation`，当前固定 `onnxruntime==1.27.0` 和 `soundfile==0.14.0`，不进 base 依赖。`onnxruntime` 和 `onnxruntime-gpu` 是互斥的两个 PyPI 包，extra 组为未来 GPU 阶段换包留出干净接口。
- Dockerfile 本阶段不改（不装 `libsndfile1`）：本地验证走 `local` 模式，宿主机裸跑 worker 不经过 Dockerfile，用户自行用 Homebrew 装系统依赖；等 `compose-full` 或 GPU 阶段需要时再补。
- 已新增目录级配置变量 `HTDEMUCS_MODEL_DIR`，默认落在 `.data/models/htdemucs-ft` 下（`.data/` 是仓库既有的"本地验证输入，不提交"约定目录）。配置只表达"模型文件在哪个目录"这一稳定意图，不暴露具体文件名/版本号；executor 内部按 `model_asset.yaml` 记录的 4 个专家文件规则在该目录下查找并校验 sha256，缺失时 fail-fast 报 `AUDIO_STEM_MODEL_ASSET_MISSING`，不做静默降级。未来迁移到 GPU 服务器时只需改这一个路径值。
- execution provider 用 `onnxruntime.get_available_providers()` 在启动/首次调用时自动探测，构造优先级列表（CUDA 优先、CPU 兜底），并用于初始化 4 个专家 ONNX session；不新增配置开关——当前机器只有 `CPUExecutionProvider` 一种可行值，配置开关形同虚设。
- Worker/api 镜像本阶段不拆分，维持共用现状。

### 已采用的阶段性决策

- 当前阶段使用 `visibility="demo"` + `role="root"`，先验证本地和开发环境闭环，不承诺测试/生产环境外部可提交合同。
- `docs/api/extension-guide.md` 里 `demo` 的语义是"模板示例、smoke 或压测入口，不是正式业务合同"；htdemucs 分离是用户想要的真实产品能力，不是模板占位示例。借用 `demo` 只是为了限制"只有 local/dev 环境能外部提交"这个效果，不代表这是模板示例功能。验证稳定后需要重新评估升级为 `public`。

### 一致性说明（避免实现阶段误解）

- 4 个 onnx session 初始化耗时（无论是首次加载还是模块级单例复用后的调用）天然计入 `_execute()` 所在 attempt 的 `timeout_seconds` 声明窗口内，不需要单独预留一段"模型加载"超时。这一点必须在 executor 实现前明确，避免后来实现者误加多余的超时声明。
- 当前 lease 行为只是让"声明超时"和"生效 lease"对齐，**不是**恢复了分段级周期续约——如果 worker 进程在推理中途真崩溃，stale 检测延迟会变成 `timeout_seconds` 量级（本 job_type 约 2400 秒级）而不是原来更紧的全局窗口。OSS 产物写入、callback 投递仍需依赖仓库既有幂等键机制兜底，不能假设 lease 是唯一防线。真正的分段级周期续约（扩展 `_execute()` 契约下传心跳回调）评估后决定本次不做，原因是这是影响所有 job_type 共用调用链的 kernel 级改动，风险收益在"只做本地验证"阶段不成比例，留作 GPU 阶段的独立后续项。

## Acceptance

- 阶段 0 探测结果（4 个专家 ONNX 文件的 I/O 签名、sha256、固定窗口长度、官方 `bag_infer.py` 聚合逻辑对应关系）已经写入 `model_asset.yaml`，且 params/result schema、runtime fields 和 timeout 估算都基于这份实测结果，而不是基于单文件 `htdemucs_ft.onnx` 假设。
- `audio_stem_separation` 声明的较长 `timeout_seconds` 在实际运行中会拉长 attempt 的 lease 窗口（可通过日志或直接查询 `job_execution_attempts.lease_expires_at` 验证），其他未声明或声明值更小的 job_type 仍落回全局 lease floor，不受影响。
- 用一段本地转换好的 44.1kHz 立体声 WAV 测试音频，能完整跑通提交 job → 执行 → 四条 stem 产物写入对象存储 → 可下载/可播放，无接缝噪声。
- 非法输入会 fail-fast，不做静默转码或截断：非白名单 ref / content type 在创建参数规范化阶段拒绝；错误采样率、错误声道数、不可解码 WAV 或超限时长在执行期读取输入后拒绝，并返回对应的 `AUDIO_STEM_*` 错误码。
- 模型权重缺失或 sha256 不匹配时，job 执行 fail-fast 报 `AUDIO_STEM_MODEL_ASSET_MISSING`，不产生部分成功或伪造结果。
- `./scripts/verify.sh check` 通过；`./scripts/dev.sh start` + `./scripts/verify.sh workflow-smoke` 通过，证明新增 job_type 未破坏现有 job_type 行为。
- `docs/plans/job-kernel-reliability-review.md` 对应 P1 条目保持"部分缓解"口径，不把 per-attempt lease 窗口生效误写成周期性续约已完成。

## Non-goals

- GPU 部署形态（`onnxruntime-gpu`、CUDA/TensorRT execution provider 实测）不在本阶段范围，只在 Remaining Gaps 中点出为后续阶段。
- 生产部署、阿里云 PAI-EAS/ACR/OSS 拓扑、K8s 资源不在本仓库边界内（参见 `AGENTS.md` 项目边界）。
- 镜像拆分（api/worker 分离）不在本阶段范围，留到 GPU 阶段或镜像体积真正成为问题时再评估。
- 分段级周期续约心跳机制（扩展 `_execute()` 契约、下传心跳回调）本阶段评估后决定不做，原因见上文"一致性说明"。
- `visibility="demo"` 到 `visibility="public"` 的正式升级不在本阶段范围，留到验证稳定后另行评估。
- 不新增 `shifts`（多次时移推理取平均）之类的质量增强参数；本阶段只验证单次推理闭环。
- 不在本计划中实现生产部署、GPU 服务化或 Triton server 接入；模型下载入口已由 `scripts/models.sh` 提供。

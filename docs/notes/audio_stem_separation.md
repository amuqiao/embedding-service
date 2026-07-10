`audio_stem_separation` 可以理解成一个“把 1 条混音 WAV 拆成 4 条 WAV”的异步 Job。

**整体流程**
```text
调用方
  |
  | 1. POST 创建 Job
  v
API /jobs
  |
  | 2. 校验 job_type + job_params
  |    job_type = audio_stem_separation
  |    input_audio = OSS URL Ref
  v
数据库记录 Job = queued
  |
  | 3. worker 领取 Job
  v
AudioStemSeparationJob executor
  |
  | 4. 通过 input_audio.public_url 下载 WAV
  | 5. 校验 sha256 / WAV / 44100Hz / 双声道 / 时长
  | 6. 加载 htdemucs-ft 4 个 ONNX 专家模型
  | 7. 分段推理 + 聚合
  | 8. 生成 drums / bass / other / vocals 四条 WAV
  | 9. 上传四条 WAV 到对象存储
  v
数据库更新 Job = succeeded + job_result
  |
  | 10. 调用方查询 Job 结果
  v
拿到 4 条 stem 的 URL Ref
```

**输入长这样**
```json
{
  "job_type": "audio_stem_separation",
  "job_params": {
    "input_audio": {
      "public_url": "https://cms-aicg-sz.epubgame.com/path/input.wav",
      "internal_url": "https://cms-aicg-sz.oss-cn-shenzhen-internal.aliyuncs.com/path/input.wav",
      "content_type": "audio/wav",
      "sha256": "..."
    },
    "max_duration_seconds": 60
  }
}
```

关键点：Job 不接收本地文件路径。即使你用 `real-flow.sh --input-file`，脚本也只是先把本地 WAV 上传到 OSS，然后构造这个 `input_audio`。

**模型执行可视化**
```text
input.wav
  shape: stereo, 44100Hz
  |
  v
切成 7.8 秒左右的片段
  |
  +--> htdemucs_ft_drums.onnx  -> 取 drums 行
  +--> htdemucs_ft_bass.onnx   -> 取 bass 行
  +--> htdemucs_ft_other.onnx  -> 取 other 行
  +--> htdemucs_ft_vocals.onnx -> 取 vocals 行
  |
  v
重叠片段加窗拼回完整长度
  |
  v
drums.wav + bass.wav + other.wav + vocals.wav
```

注意：它不是跑一个 ONNX，而是跑 4 个专家 ONNX，所以计算量大约是单模型假设的 4 倍。

**输出长这样**
```json
{
  "job_status": "succeeded",
  "job_result": {
    "stems": {
      "drums": {
        "public_url": ".../drums.wav",
        "internal_url": ".../drums.wav",
        "content_type": "audio/wav",
        "sha256": "..."
      },
      "bass": {
        "public_url": ".../bass.wav",
        "internal_url": ".../bass.wav",
        "content_type": "audio/wav",
        "sha256": "..."
      },
      "other": {
        "public_url": ".../other.wav",
        "internal_url": ".../other.wav",
        "content_type": "audio/wav",
        "sha256": "..."
      },
      "vocals": {
        "public_url": ".../vocals.wav",
        "internal_url": ".../vocals.wav",
        "content_type": "audio/wav",
        "sha256": "..."
      }
    },
    "duration_ms": {
      "download": 123,
      "inference": 8200,
      "upload": 450,
      "total": 8773
    },
    "runtime": {
      "model": "htdemucs-ft-onnx",
      "sample_rate": 44100,
      "channels": 2,
      "segment_count": 2,
      "execution_provider": "CPUExecutionProvider"
    }
  }
}
```

代码位置：
- 入参/出参 schema：[jobs.py](/Users/admin/Code/fastapi-best-ai-architecture/app/schemas/jobs.py:558)
- executor 主流程：[executor.py](/Users/admin/Code/fastapi-best-ai-architecture/app/jobs/types/audio_stem_separation/executor.py:381)
- 读取输入音频：[executor.py](/Users/admin/Code/fastapi-best-ai-architecture/app/jobs/types/audio_stem_separation/executor.py:313)
- 加载模型 runner：[executor.py](/Users/admin/Code/fastapi-best-ai-architecture/app/jobs/types/audio_stem_separation/executor.py:283)
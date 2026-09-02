# Object Storage

`app/object_storage` 是对象存储仓储层，业务 `job_type` 必须通过自己的 storage adapter 接入，不能直接绑定具体 provider。

## Boundary Rules

本目录是基础设施模块，不是业务适配层。修改本目录前必须先确认改动满足以下条件：

- 只新增或调整对象存储通用原语，例如读、写、删、元数据、provider 配置校验、底层错误、provider 专属签名 URL 和 provider URL 身份解析。
- 不为某个业务包、POC、smoke flow、接口字段或资源命名规则增加专用逻辑。
- 不引入业务错误码、业务 payload、业务 content type 白名单、业务 key 拼装规则或业务 URL Ref 解释。
- 业务差异必须放在对应业务包的 storage adapter；smoke 专用差异必须放在对应 smoke flow。
- 多个业务当前碰巧复用同一逻辑，不代表该逻辑可以进入本目录；只有稳定、无业务词汇、可被对象存储语义独立解释的能力才允许下沉。

## Architecture

本目录采用简化版 **Ports and Adapters / Hexagonal Architecture**：

```text
business job type
  -> business storage adapter
    -> BaseObjectStorageAdapter
      -> ObjectStorageRepository
        -> providers/aliyun_oss.py
        -> providers/local.py

public CDN / HTTPS input
  -> business storage adapter
    -> PublicUrlReader
```

对应关系：

```text
Port:
  repository.py              # ObjectStorageRepository abstract base class

Business adapter contract:
  adapter.py                 # ObjectStorageAdapterContext, BaseObjectStorageAdapter

Infrastructure adapters:
  providers/aliyun_oss.py    # Aliyun OSS read/write/sign URL/URL identity
  providers/local.py         # Local read/write for tests and development

Construction:
  factory.py                 # build_repository, register_provider_builder

Public input reader:
  public_url.py              # PublicUrlInputReader, PublicUrlReader
```

## Mental Model

先区分三类事实：

```text
Where:
  ObjectRef                  # 对象在哪里
  PublicUrlReadSpec.url      # 公网输入 URL 是什么

Integrity:
  ExpectedObjectIntegrity    # 期望的 size_bytes / sha256

Policy:
  ObjectReadPolicy           # 本次读取要不要校验 size / sha256 / max_bytes
```

`object_storage` 提供通用合同；业务 adapter 决定每个 job type 用哪种读取模式。
`PublicUrlConfig` 是公网 reader 的来源准入和硬上限，`ObjectReadPolicy` 是每次读取的显式校验策略。

```text
业务 payload
  -> business storage adapter
    -> ObjectReadSpec 或 PublicUrlReadSpec
      -> BaseObjectStorageAdapter
        -> ObjectStorageRepository 或 PublicUrlReader
          -> bytes
```

## Rules

业务接入规则：

- `job_type` 主流程必须依赖自己的业务 storage adapter。
- 业务 storage adapter 应继承或组合 `BaseObjectStorageAdapter`。
- 业务字段、业务 key 规则、content type 规则、输入输出 payload 转换，只能放在业务 storage adapter。
- `job_type` 主流程不要直接 import `app.object_storage.providers.*`。
- `providers/` 只实现底层对象存储读写，不放任何业务规则。
- 公网 URL 输入只通过 `PublicUrlReader` 读取，并由业务 storage adapter 组合使用。
- 自定义公网输入 reader 必须继承 `PublicUrlInputReader`。
- `PublicUrlConfig.allowed_hosts` 和 `max_bytes_ceiling` 是 job type 级输入安全护栏，不放进全局仓储配置。
- 业务 `job_type` 只能依赖 `app.object_storage`；对象存储读写事实源只保留本模块。
- provider 配置只接受声明过的字段；未知字段必须 fail-fast。
- 读取校验策略必须显式使用 `ObjectReadPolicy`，不要在业务主流程里散落手写 size 或 sha256 校验。
- Job runtime artifact 读写不属于本模块；业务输入输出适配放在业务 adapter 中。
- Aliyun OSS URL 解析只返回对象身份；业务 URL Ref、CDN 映射和允许哪些 bucket/region/content_type 由业务 adapter 判断。

允许直接使用 provider 的位置：

- `factory.py` 内置 provider 构建。
- provider 自身测试。
- provider 模块导入期或应用装配的单次执行路径，用于注册新的 provider builder。

## Adapter Shape

业务 adapter 放在各自 `job_type` 目录，不放在 `app/object_storage`：

```text
app/jobs/audio_stem/
  storage_adapter.py
  processor.py

app/object_storage/
  adapter.py
  repository.py
  factory.py
  providers/
```

业务 adapter 示例：

```python
from app.object_storage import (
    BaseObjectStorageAdapter,
    ExpectedObjectIntegrity,
    ObjectReadPolicy,
    ObjectReadSpec,
    ObjectRef,
    PutObjectResult,
)


class AudioStemStorageAdapter(BaseObjectStorageAdapter):
    def read_source(self, payload: dict[str, str]) -> bytes:
        return self.read_object(
            ObjectReadSpec(
                ref=ObjectRef(
                    provider=payload["provider"],
                    bucket=payload["bucket"],
                    region=payload["region"],
                    key=payload["key"],
                ),
                integrity=ExpectedObjectIntegrity(
                    size_bytes=int(payload["size_bytes"]),
                    sha256=payload["sha256"],
                ),
                policy=ObjectReadPolicy(
                    verify_size_bytes=True,
                    verify_sha256=True,
                ),
            )
        )

    def read_trusted_source(self, payload: dict[str, str]) -> bytes:
        return self.read_object(
            ObjectReadSpec(
                ref=ObjectRef(
                    provider=payload["provider"],
                    bucket=payload["bucket"],
                    region=payload["region"],
                    key=payload["key"],
                )
            )
        )

    def write_manifest(self, job_id: str, content: bytes) -> PutObjectResult:
        return self.write_object_bytes(
            f"audio-stem/{job_id}/manifest.json",
            content,
            content_type="application/json",
        )
```

只有公网 URL、没有 AK/SK 的输入示例：

```python
from app.object_storage import (
    BaseObjectStorageAdapter,
    ExpectedObjectIntegrity,
    ObjectReadPolicy,
    PublicUrlReadSpec,
)


class PublicInputStorageAdapter(BaseObjectStorageAdapter):
    def read_source_url(self, url: str, sha256: str | None = None) -> bytes:
        return self.read_public_url(
            PublicUrlReadSpec(
                url=url,
                integrity=ExpectedObjectIntegrity(sha256=sha256),
                policy=ObjectReadPolicy(
                    verify_sha256=sha256 is not None,
                    max_bytes=100 * 1024 * 1024,
                ),
            )
        )
```

## Read Modes

不同业务只改变 spec，不改变仓储骨架：

```text
可信内部 OSS，不校验
  ObjectReadSpec(
    ref=ObjectRef(...),
    policy=ObjectReadPolicy()
  )

内部 OSS，校验大小
  ObjectReadSpec(
    ref=ObjectRef(...),
    integrity=ExpectedObjectIntegrity(size_bytes=123),
    policy=ObjectReadPolicy(verify_size_bytes=True)
  )

内部 OSS，强校验
  ObjectReadSpec(
    ref=ObjectRef(...),
    integrity=ExpectedObjectIntegrity(size_bytes=123, sha256="..."),
    policy=ObjectReadPolicy(verify_size_bytes=True, verify_sha256=True)
  )

公网 URL，限制大小，可选 sha256
  PublicUrlReadSpec(
    url="https://cdn.example.com/input.wav",
    integrity=ExpectedObjectIntegrity(sha256="..."),
    policy=ObjectReadPolicy(verify_sha256=True, max_bytes=104857600)
  )
```

## Construction

从配置构建业务 adapter：

```python
from app.object_storage import ObjectStorageAdapterContext, ObjectStorageConfig, PublicUrlConfig


storage_context = ObjectStorageAdapterContext.from_config(
    repository_config=ObjectStorageConfig(
        provider="aliyun_oss",
        options={
            "bucket": "...",
            "region": "cn-hangzhou",
            "access_key_id": "...",
            "access_key_secret": "...",
            "key_prefix": "jobs/audio-stem",
            "endpoint": "oss-cn-hangzhou.aliyuncs.com",
            "endpoint_style": "virtual_host",
            "public_base_url": "https://cdn.example.com",
        },
    ),
)

storage = AudioStemStorageAdapter(storage_context)
```

需要公网 URL 输入的业务 adapter 单独注入自己的 `PublicUrlReader`：

```python
from app.object_storage import PublicUrlReader


storage = PublicInputStorageAdapter(
    storage_context,
    public_url_reader=PublicUrlReader(
        PublicUrlConfig(
            allowed_hosts=("cdn.example.com",),
            max_bytes_ceiling=200 * 1024 * 1024,
        )
    ),
)
```

如果业务 adapter 没有自定义构造逻辑，也可以使用 `BaseObjectStorageAdapter.from_config()` 统一装配：

```python
storage = PublicInputStorageAdapter.from_config(
    repository_config=ObjectStorageConfig(
        provider="aliyun_oss",
        options={
            "bucket": "...",
            "region": "cn-hangzhou",
            "access_key_id": "...",
            "access_key_secret": "...",
        },
    ),
    public_url_config=PublicUrlConfig(
        allowed_hosts=("cdn.example.com",),
        max_bytes_ceiling=200 * 1024 * 1024,
    ),
)
```

## Provider Extension

新增 provider 时遵循开闭原则：

```text
1. 新增 providers/<provider_name>.py
2. 继承 `ObjectStorageRepository`
3. 提供自己的 Config dataclass
4. 提供 builder: Mapping[str, Any] -> ObjectStorageRepository
5. 在模块导入期调用 `register_provider_builder()`，或在单次装配路径中先用 `registered_provider_names()` 防重复注册
```

业务 adapter 和 `job_type` 主流程不需要因为新增 provider 改代码。

## Boundary

本目录负责：

- 通用对象引用：`ObjectRef`
- 读取完整性期望：`ExpectedObjectIntegrity`
- 读取策略：`ObjectReadPolicy`
- 读取请求：`ObjectReadSpec`、`PublicUrlReadSpec`
- 通用对象元信息：`ObjectMeta`
- 写入结果：`PutObjectResult`
- 统一仓储抽象：`ObjectStorageRepository`
- 业务 adapter 基类：`BaseObjectStorageAdapter`
- provider 构建与注册：`build_repository`、`register_provider_builder`
- 内置 provider：`aliyun_oss`、`local`
- 公网 URL 只读输入：`PublicUrlReader`
- Aliyun OSS provider 专属能力：`signed_get_url()`、`signed_put_url()`、`public_url()`、`parse_aliyun_oss_url()`、`validate_aliyun_oss_access_url()`、`redact_aliyun_oss_url()`

本目录不负责：

- `job_type` 的业务输入输出 schema
- 业务 payload 字段解释
- 业务错误码或 FastAPI `AppError` 映射
- Job 编排和业务对象生命周期
- provider 凭据的生产密钥管理

# Object Storage 通用解决方案

本文说明 `app/object_storage` 当前已经落地的通用对象存储仓储层方案：它不绑定具体业务，不绑定 `job_type`，业务代码必须通过自己的 storage adapter 使用这套能力。

## 心智模型

先把对象存储拆成三层：

```text
业务代码
  关心：业务字段、输入输出 payload、对象 key 规则、是否校验、写什么产物

业务 storage adapter
  关心：把业务语义翻译成 object_storage 的通用读写合同

app/object_storage
  关心：对象在哪里、如何读写、如何校验、使用哪个 provider
```

当前实现对应到代码是：

```text
业务 job type / 其他项目业务模块
  -> 业务自己的 storage adapter
    -> BaseObjectStorageAdapter
      -> ObjectReadSpec / PublicUrlReadSpec
      -> ObjectStorageRepository / PublicUrlReader
        -> providers/aliyun_oss.py
        -> providers/local.py
```

这套目录的角色不是“OSS 工厂工具函数集合”，而是一个轻量的仓储层：

```text
Port:
  ObjectStorageRepository        # 稳定读写合同

Application adapter base:
  BaseObjectStorageAdapter       # 给业务 adapter 复用的基础能力

Domain-neutral schema:
  ObjectRef                      # 对象位置
  ExpectedObjectIntegrity        # 期望完整性
  ObjectReadPolicy               # 本次读取策略
  ObjectReadSpec                 # 内部对象读取请求
  PublicUrlReadSpec              # 公网 URL 读取请求
  PutObjectResult / ObjectMeta   # 写入结果和对象元数据

Infrastructure:
  AliyunOSSRepository            # Aliyun OSS provider
  LocalObjectStorageRepository   # 本地 provider，用于开发和测试

Construction:
  ObjectStorageConfig
  build_repository()
  register_provider_builder()
```

## 设计边界

`app/object_storage` 只解决通用对象存储问题：

- 从已知对象位置读取 bytes。
- 把 bytes 写入对象存储并返回 `PutObjectResult`。
- 查询对象元数据。
- 删除对象。
- 通过显式 policy 校验 `size_bytes`、`sha256` 和 `max_bytes`。
- 在没有 AK/SK 的情况下，从受控公网 HTTPS URL 读取输入 bytes。
- 通过 provider registry 接入不同底层存储实现。

它不解决这些业务问题：

- 不定义任何业务 `job_type`。
- 不理解业务 payload 字段。
- 不决定业务对象 key 命名。
- 不决定某个业务是否必须校验 `sha256`。
- 不负责旧 OSS 业务链路迁移。
- 不负责应用级配置从 `.env` 映射到 `ObjectStorageConfig`。
- 不负责把对象存储异常翻译成具体 HTTP 响应或 Job 错误码。

## 当前能力

| 能力 | 当前实现 | 说明 |
|---|---|---|
| 内部对象读 | `BaseObjectStorageAdapter.read_object()` | 输入必须是 `ObjectReadSpec` |
| 内部对象写 | `BaseObjectStorageAdapter.write_object_bytes()` | 返回 `PutObjectResult`，包含 `sha256`、`size_bytes` 和可选 `public_url` |
| 对象元数据 | `head_object()` / `ObjectStorageRepository.head()` | 用于读取前预检和业务需要的元数据查询 |
| 对象删除 | `delete_object()` / `ObjectStorageRepository.delete()` | provider 负责具体删除行为 |
| 完整性校验 | `ExpectedObjectIntegrity` + `ObjectReadPolicy` | 是否校验由 policy 显式决定 |
| 公网 URL 输入 | `PublicUrlReader` + `PublicUrlReadSpec` | 用于只有 CDN URL 或公网可下载 URL、没有 AK/SK 的场景 |
| provider 构建 | `ObjectStorageConfig` + `build_repository()` | 当前内置 `aliyun_oss` 和 `local` |
| provider 扩展 | `register_provider_builder()` | 新 provider 通过注册 builder 接入 |

## 读取合同

对象读取不是直接传一串参数，而是拆成三类事实。

```text
Where:
  ObjectRef(provider, bucket, region, key)

Integrity:
  ExpectedObjectIntegrity(size_bytes, sha256)

Policy:
  ObjectReadPolicy(verify_size_bytes, verify_sha256, max_bytes)
```

`ExpectedObjectIntegrity` 只表达“业务方给了什么期望值”，`ObjectReadPolicy` 才表达“本次是否使用这些期望值做校验”。

这能覆盖不同业务模式：

| 场景 | spec 形态 |
|---|---|
| 可信内部对象，不校验 | `ObjectReadSpec(ref=ObjectRef(...))` |
| 只限制最大输入大小 | `ObjectReadSpec(..., policy=ObjectReadPolicy(max_bytes=...))` |
| 校验 byte 大小 | `ExpectedObjectIntegrity(size_bytes=...)` + `ObjectReadPolicy(verify_size_bytes=True)` |
| 校验 sha256 | `ExpectedObjectIntegrity(sha256=...)` + `ObjectReadPolicy(verify_sha256=True)` |
| 强校验 | 同时启用 `verify_size_bytes=True` 和 `verify_sha256=True` |

如果 policy 要求校验，但 integrity 没有提供对应字段，`ObjectReadSpec` 会在创建时直接报错。

## 公网 URL 输入

公网 URL 输入不是 `ObjectStorageRepository`，因为它没有 bucket、region、AK/SK，也不一定允许写回。当前用独立的 `PublicUrlReader` 表达：

```text
公网 HTTPS URL
  -> PublicUrlReadSpec
    -> PublicUrlReader
      -> bytes
```

适用场景：

- 业务方只给 CDN URL。
- 业务方只给公网可直接下载的 HTTPS URL。
- 当前服务不知道业务方对象存储 AK/SK。
- 当前服务只需要读取输入，不需要管理对方 bucket。

当前安全边界：

- 只允许 HTTPS URL。
- 必须配置 `allowed_hosts`。
- URL 不能带账号密码、端口、query 或 fragment。
- path 必须是规范对象路径。
- 解析到私网、loopback、link-local、multicast 或 reserved 地址会被拒绝。
- 不允许跳转。
- `max_bytes_ceiling` 是 reader 级硬上限。
- `ObjectReadPolicy.max_bytes` 是单次读取上限。
- 如果两者都存在，实际使用更小的上限。

公网 URL 的 size 和 sha256 校验同样由 `PublicUrlReadSpec` 中的 policy 决定，不是强制全局校验。

## 业务如何接入

业务不要在主流程里直接 import `providers/aliyun_oss.py` 或 `providers/local.py`。推荐每个业务模块维护自己的 storage adapter：

```text
app/jobs/types/<job_type>/
  processor.py
  storage_adapter.py

app/object_storage/
  adapter.py
  repository.py
  factory.py
  public_url.py
  providers/
```

业务 adapter 负责把业务 payload 翻译成通用 spec：

```python
from app.object_storage import (
    BaseObjectStorageAdapter,
    ExpectedObjectIntegrity,
    ObjectReadPolicy,
    ObjectReadSpec,
    ObjectRef,
    PutObjectResult,
)


class ExampleStorageAdapter(BaseObjectStorageAdapter):
    def read_source(self, payload: dict[str, object]) -> bytes:
        return self.read_object(
            ObjectReadSpec(
                ref=ObjectRef(
                    provider=str(payload["provider"]),
                    bucket=str(payload["bucket"]),
                    region=str(payload["region"]),
                    key=str(payload["key"]),
                ),
                integrity=ExpectedObjectIntegrity(
                    size_bytes=int(payload["size_bytes"]),
                    sha256=str(payload["sha256"]),
                ),
                policy=ObjectReadPolicy(
                    verify_size_bytes=True,
                    verify_sha256=True,
                    max_bytes=500 * 1024 * 1024,
                ),
            )
        )

    def write_result(self, job_id: str, data: bytes) -> PutObjectResult:
        return self.write_object_bytes(
            f"example/{job_id}/result.json",
            data,
            content_type="application/json",
        )
```

只有公网 URL 的业务 adapter 可以这样组合：

```python
from app.object_storage import (
    BaseObjectStorageAdapter,
    ExpectedObjectIntegrity,
    ObjectReadPolicy,
    PublicUrlReadSpec,
)


class ExamplePublicInputStorageAdapter(BaseObjectStorageAdapter):
    def read_source_url(self, url: str, sha256: str | None = None) -> bytes:
        return self.read_public_url(
            PublicUrlReadSpec(
                url=url,
                integrity=ExpectedObjectIntegrity(sha256=sha256),
                policy=ObjectReadPolicy(
                    verify_sha256=sha256 is not None,
                    max_bytes=500 * 1024 * 1024,
                ),
            )
        )
```

装配时由应用层选择 provider：

```python
from app.object_storage import (
    ObjectStorageAdapterContext,
    ObjectStorageConfig,
    PublicUrlConfig,
    PublicUrlReader,
)


storage_context = ObjectStorageAdapterContext.from_config(
    repository_config=ObjectStorageConfig(
        provider="aliyun_oss",
        options={
            "bucket": "...",
            "region": "cn-hangzhou",
            "access_key_id": "...",
            "access_key_secret": "...",
            "key_prefix": "jobs/example",
            "endpoint": "oss-cn-hangzhou.aliyuncs.com",
            "endpoint_style": "virtual_host",
            "public_base_url": "https://cdn.example.com",
        },
    )
)

storage = ExamplePublicInputStorageAdapter(
    storage_context,
    public_url_reader=PublicUrlReader(
        PublicUrlConfig(
            allowed_hosts=("cdn.example.com",),
            max_bytes_ceiling=1024 * 1024 * 1024,
        )
    ),
)
```

## Provider 规范

新增 provider 时，不改业务 adapter 的主结构，只扩展基础设施层。

```text
新增 provider
  1. 在 app/object_storage/providers/ 下新增实现
  2. 定义 provider config dataclass
  3. 继承 ObjectStorageRepository
  4. 实现 get_bytes / put_bytes / head / delete
  5. 在装配路径注册 provider builder
```

provider builder 必须遵守：

- 输入是 `Mapping[str, Any]`。
- 输出必须是 `ObjectStorageRepository`。
- 未知配置字段必须 fail-fast。
- 必填字段缺失必须 fail-fast。
- provider 名称不能重复注册。
- provider 不写业务 key 规则、业务 payload 规则或业务错误码。

当前内置 provider：

| provider | 用途 | 关键配置 |
|---|---|---|
| `aliyun_oss` | Aliyun OSS 读写 | `bucket`、`region`、`access_key_id`、`access_key_secret`，可选 `key_prefix`、`endpoint`、`endpoint_style`、`public_base_url`、`scheme`、`timeout_seconds` |
| `local` | 本地开发和测试 | `root`，可选 `bucket`、`region`、`public_base_url` |

未来接入 S3、COS、MinIO 或其他对象存储时，优先新增 provider，不改变业务 adapter 和读取 spec。

## 演进规则

这套骨架的扩展顺序应保持稳定：

```text
新增业务类型
  -> 新增业务 storage adapter
  -> 复用现有 ObjectReadSpec / PublicUrlReadSpec

新增底层存储
  -> 新增 provider repository
  -> 注册 provider builder
  -> 不改业务主流程

新增通用校验能力
  -> 优先扩展 ExpectedObjectIntegrity / ObjectReadPolicy
  -> 只在所有 provider 都能理解或由 adapter 层统一完成时才加入

新增大文件/流式能力
  -> 先评估是否需要扩展 ObjectStorageRepository 合同
  -> 不在单个 provider 里做业务专属绕路
```

不要把一次性业务需求下沉到 `app/object_storage`。只有满足下面条件，才考虑扩展通用层：

- 多个业务都会使用。
- 与具体业务字段无关。
- 能用稳定合同表达。
- 不会让现有 provider 行为含糊。
- 能被测试覆盖。

## 使用规则

业务接入必须遵守：

- 业务主流程依赖自己的 storage adapter。
- storage adapter 可以继承或组合 `BaseObjectStorageAdapter`。
- 业务对象 key、content type、payload 字段转换放在业务 adapter。
- 业务主流程不直接 import `app.object_storage.providers.*`。
- `providers/` 不写业务逻辑。
- 是否校验 size 或 sha256 由 `ObjectReadPolicy` 显式表达。
- 没有 AK/SK 但有公网可访问 URL 时，使用 `PublicUrlReader`，不要伪造成 OSS provider。
- provider config 不做静默兜底，不接受未知字段。

允许直接接触 provider 的位置：

- `factory.py` 内置 provider builder。
- provider 自身单元测试。
- 应用装配阶段注册自定义 provider builder。
- 迁移期的临时适配代码，但不能作为新业务规范。

## 与项目和 job_type 的关系

`app/object_storage` 可以复用到其他项目。它只依赖 Python 标准库和本目录内合同，不依赖本项目的 Job kernel、Taskiq、FastAPI route、数据库模型或具体业务 job type。

在本项目内，它未来应作为新 `job_type` 的对象存储仓储层。每个 `job_type` 可以有自己的 storage adapter，这样业务可以按自己的输入来源和校验策略接入，同时不把业务规则写进通用仓储层。

```text
audio_stem_separation
  -> AudioStemStorageAdapter
    -> app.object_storage

poster_title_image
  -> PosterTitleImageStorageAdapter
    -> app.object_storage

其他项目
  -> 自己的业务 adapter
    -> app.object_storage
```

当前文档只描述新 `app/object_storage` 的通用方案，不表示旧 OSS 业务链路已经完成替换。

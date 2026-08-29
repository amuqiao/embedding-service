# scripts 维护规范

本文说明 `scripts/` 目录下本地入口脚本的职责边界和维护规则。具体参数以各脚本自身 `-h` 输出为准，本文不复制完整命令手册。

## 目录治理模型

`scripts/` 目录提供本仓库稳定的本地操作入口。目录治理目标是让日常编排、单项能力和下沉实现分层清楚：日常命令组合稳定入口，单项能力可被独立调用，也可被其他入口编排复用。

```text
scripts/<entry>.sh        稳定用户入口；负责 help、参数分发和组合编排
scripts/<domain>/         某个入口的复杂实现；只服务对应入口
scripts/lib/              多入口共享 shell helper；不放业务流程
docs/current/...          当前脚本合同和治理事实
docs/runbooks/...         长流程、排障路径和场景说明
```

入口脚本应把“日常 recipe、宿主机进程、模板验证、部署形态、对象存储、业务 smoke/E2E、只读排障、写操作运维”分开，避免一个脚本承担跨领域职责。

```text
run.sh          日常快捷 recipe
dev.sh          宿主机 API / worker 生命周期
verify.sh       一次性验证任务
deploy.sh       docker compose 部署形态
k8s.sh          已部署 Pod 内手动运维入口，具体动作下沉到 scripts/k8s/
redis.sh        Redis 只读排障事实源
oss.sh          OSS 配置、URL Ref、连通性和显式上传检查事实源
load.sh         项目级压测入口
triton-bench.sh Triton 推理服务直压入口
jobs.sh         Job 只读查询与排障
job-ops.sh      Job 写操作运维入口
smoke.sh       标准业务 smoke/E2E 验证入口
ai-providers.sh 云模型 provider、AI catalog 和 resolver 只读诊断入口
models.sh       本地模型资产下载、路径和必需文件检查
media.sh        本地音视频素材探测、校验和准备
tools.sh        无默认持久副作用的本地开发辅助工具和只读代码清单查看
```

## 新入口准入

新增脚本或子命令前，先按以下顺序判断归属：

1. 能不能作为已有入口的子命令。
2. 能不能作为已有入口下沉目录中的实现模块。
3. 是否存在独立生命周期、独立安全边界或独立排障事实源。
4. 是否需要被 `run.sh`、`k8s.sh`、`smoke.sh`、`jobs.sh` 等多个入口编排复用。

只有职责边界、生命周期或安全边界不同，才新增顶层 `*.sh`。新增顶层入口必须先更新本文和 [`../docs/current/script-entrypoint-contract.md`](../docs/current/script-entrypoint-contract.md)，再实现脚本。

常见归属规则：

| 能力 | 归属 | 不应放入 |
|---|---|---|
| 日常启动、停止、状态、重启组合 | `run.sh` recipe | `dev.sh` / `deploy.sh` 内部硬编码组合流程 |
| 宿主机 API / worker 生命周期 | `dev.sh` | `run.sh` / `deploy.sh` |
| Compose 依赖或全量服务 | `deploy.sh` | `dev.sh` |
| Redis 连接、Stream、broker key、内存和 keyspace 证据 | `redis.sh` | `k8s.sh` / `jobs.sh` 各自复制诊断逻辑 |
| OSS 配置、URL Ref、PUT/GET/HEAD、上传检查 | `oss.sh`；`verify.sh oss-config` 和 `k8s.sh check oss` 只编排它 | `smoke.sh` 的通用实现或业务 Job 逻辑 |
| Job 只读状态、attempt、callback、timeline、broker/runtime 证据 | `jobs.sh` | `job-ops.sh` |
| Job 重放、软删除、恢复等写操作 | `job-ops.sh`，且必须显式 `--confirm` | `jobs.sh` |
| 真实业务 HTTP 合同验证 | `smoke.sh` | `verify.sh` |
| 云模型 provider 配置、模型 catalog、resolver 路由诊断 | `ai-providers.sh` | `models.sh` / `smoke.sh` / `tools.sh` |
| 模型资产下载、路径和必需文件检查 | `models.sh` | `tools.sh` |
| 本地素材探测、校验和准备 | `media.sh` | `models.sh` / `smoke.sh` |
| 小型无默认持久副作用工具 | `tools.sh` | 业务或运维入口 |

模型权重这类会写入 `.data/models/`、可能访问远端但不执行真实推理的本地资产准备，归属 `models.sh`，不放入默认无持久副作用的 `tools.sh`。
云模型 provider 凭证摘要、全局模型 catalog 和 capability route 解析归属 `ai-providers.sh`。它默认不访问远端 provider、不产生费用、不提交 Job；真实业务链路验收仍归 `smoke.sh`。
音视频素材探测、转码准备和业务输入格式校验归属 `media.sh`；它只处理本地素材文件，不下载模型、不执行推理、不提交 Job、不上传对象存储。
Triton 直压归属 `triton-bench.sh`；它只直连推理服务，不创建 FastAPI Job，不访问 DB/Redis/OSS，不触发 callback，不替代 `load.sh` 的业务链路压测。
已注册 tool、capability 和 job_type capability 关系归属 `tools.sh registry` 只读查看；当前治理事实见 `docs/current/registry-governance.md`。
Redis 连接、服务端版本、命令能力、内存、keyspace、Stream 和 broker key 证据归属 `redis.sh`。`k8s.sh`、`jobs.sh` 或业务脚本需要 Redis 证据时只编排或复用该入口，不各自维护 Redis 诊断逻辑。
OSS 配置摘要、URL Ref、显式远端连通性和显式上传检查归属 `oss.sh`。`verify.sh oss-config` 只是 `oss.sh check` 的验证别名；`k8s.sh check oss --confirm` 只负责 Pod 环境确认和编排 `oss.sh check --remote --confirm`。运维权限不支持 `DeleteObject`，远端连通性检查只执行 `PUT / GET / HEAD`，检查对象会保留在 OSS。
K8s Pod 内运维归属 `k8s.sh` 入口和 `scripts/k8s/` 下沉实现。`k8s.sh` 只维护 help、参数分发和命令合同，`scripts/k8s/ops.sh` 维护 PostgreSQL / Redis / OSS / dashboard / Alembic 原子动作。
业务 smoke/E2E 归属 `smoke.sh` 和 `python -m smoke`。它只验证已经运行的服务是否符合 HTTP 合同，负责 health/ready/list、提交场景、轮询终态、断言结果和输出证据；不启动或停止 API/worker，不执行 Alembic migration，不直接查库推进流程，也不替代 `jobs.sh` 排障查询。`smoke.sh` 的公开调用格式统一为 `./scripts/smoke.sh [global options] <scenario> [standard job options] [business options]`；`--base-url`、`--env-file`、`--timeout`、`--poll-interval`、`--output-dir` 和 `--json` 等全局参数放在场景名前，`--confirm-run`、`--confirm-cost`、`--confirm-upload`、`--client-request-id`、`--expect-status`、`--callback-url`、`--local-callback`、`--callback-event`、`--wait-callback/--no-wait-callback` 和 `--callback-timeout-seconds` 等标准参数由支持的 Job 场景复用，业务私有参数只放在对应场景命令后。

## 入口职责

Shell 入口默认只负责：

- 定位仓库根目录和运行时。
- 加载必要的公共 shell helper。
- 做轻量参数分发。
- 提供稳定、可读的 help。
- 调用下沉实现脚本或 Python CLI。

不要在顶层 shell 入口里堆复杂业务逻辑。复杂逻辑应下沉到 `scripts/<domain>/` 下的 Python 或 shell 模块，并保持函数边界清楚。

`run.sh` 是编排入口，不是能力入口。新增 recipe 时只能组合已有稳定入口，例如 `deploy.sh`、`dev.sh`、`verify.sh`、`smoke.sh`、`jobs.sh`、`redis.sh` 或 `oss.sh`；不要在 `run.sh` 里新增数据库、Redis、OSS、Job 查询或业务 smoke 的实现细节。

## Help 分层

脚本 help 的完整 envelope 合同维护在 [`../docs/current/script-entrypoint-contract.md`](../docs/current/script-entrypoint-contract.md)。这里不重复维护大纲、示例数量、输出边界或 exit code 细则。

维护脚本 help 时只记住三条：

- 顶层 help 回答“这个入口是什么、有哪些命令、最小怎么开始”。
- 子命令 help 回答“这个具体动作如何配置和复制修改”。
- 命令索引只能有一个事实源；已有自动 `Commands` 时，不要在手写区再维护一份完整命令目录。
- 新标准只检查当前合同，不保留旧 help 格式兼容；如果旧测试或旧文档要求与本合同冲突，删除旧要求。

## 配置边界

本地运行形态配置、应用业务配置、密钥、模型参数和数据库连接统一放在仓库根目录 `.env`。`.env.example` 是唯一可提交配置模板；不要再维护 `scripts/.env` 或 `scripts/.env.example`。

新增脚本读取配置时应沿用现有优先级和 helper，不要重新发明配置加载规则。`smoke.sh` 默认只能面向本地 API；验证远端测试环境必须显式传 `--allow-remote-api` 和 `--base-url`。

## 运行模式边界

`run.sh` 只编排日常 recipe，不直接实现进程管理、Compose 管理或迁移细节。默认本地开发路径是 `./scripts/run.sh up dev`，它按顺序调用 `deploy.sh up compose-deps`、`dev.sh migrate`、`dev.sh start api` 和 `dev.sh start worker`。这里的 `worker` 是本地 worker-bundle，包含 Taskiq worker、dispatcher、callbacker 和 reconciler。

`dev.sh` 只管理宿主机 API / worker 进程，不启动或停止 PostgreSQL / Redis。`deploy.sh` 只管理 `compose-deps` 和 `compose-full`。不要把 recipe 塞回 `dev.sh` 或 `deploy.sh`。

运行角色入口按 role-first 设计：`start-worker.sh` 只运行 Taskiq worker，`start-dispatcher.sh` 只运行 dispatch outbox publisher，`start-callbacker.sh` 只运行 callback outbox delivery，`start-reconciler.sh` 只运行状态修复；`start-worker-bundle.sh` 只负责把四个角色组合成单个本地/compose worker 服务。

`local` 与当前仓库下任何 `compose-full` 的 API / worker 不能混跑。`local` 可以复用 `compose-deps` 的 PostgreSQL / Redis，但当 `compose-full` 的 API / worker 已运行时，`dev.sh start` / `migrate` 应直接失败；当本地 API / worker 或残留本地进程仍在运行时，`deploy.sh up compose-full` 应直接失败。

运行模式检测放在 `scripts/lib/modes.sh`，不要在各入口里重新实现一套。检测到冲突时不要自动杀进程，应提示用户执行明确的停止命令。

## 验证要求

修改 `scripts/` 后至少运行与改动匹配的最小验证：

```bash
./scripts/<changed-entry>.sh -h
uv run python -m compileall scripts
```

修改入口 help、公共 helper 或多脚本规则后，运行：

```bash
./scripts/verify.sh check
```

修改服务启动、Job workflow、对象存储或业务 smoke/E2E 执行路径时，还应按项目根目录 `AGENTS.md` 的验证要求补充 `./scripts/smoke.sh` 对应场景验证。

`verify.sh check` 只维护当前脚本合同：入口 help 可访问、自动 `Commands` 不被手写目录重复、关键子命令 help 可访问、语法和 Python 编译通过。不要为了兼容旧 help 格式新增测试或保留旧断言。

## 新增脚本 Checklist

新增或扩展脚本时检查：

- 职责是否不能放入已有入口。
- 文件名是否稳定、可预测，并使用 `.sh` 作为顶层入口。
- `-h` 是否说明作用域、不负责什么、命令、环境变量、副作用、示例和 exit code。
- 是否区分顶层基础用法和子命令进阶用法。
- 多子命令入口是否逐个子命令支持 `-h` 和 `--json`。
- 是否复用 `scripts/lib/` 或既有 Python helper。
- 是否避免 silent fallback；配置错误应快速失败。
- 是否明确 stdout / stderr / `--json` 行为。
- 默认输出是否保持人读，且没有夹带完整 JSON 或大段 JSON 摘要。
- 是否为费用、上传、写库或业务 smoke/E2E 设置显式确认参数。
- 是否完成最小验证，并在需要时运行 `./scripts/verify.sh check`。

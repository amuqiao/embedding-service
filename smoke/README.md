# Smoke/E2E Runtime

本文记录本仓库当前的标准 smoke/E2E 入口。`smoke` 只验证已经运行的 FastAPI Job 服务是否符合外部 HTTP 合同；服务生命周期、模板级质量门和排障查询由其他入口负责。

## 工作模型

```text
run.sh / dev.sh / deploy.sh
  -> 启动、停止、迁移和运行形态

scripts/smoke.sh / python -m smoke
  -> HTTP E2E、轮询、断言和证据输出

jobs.sh / job-ops.sh
  -> 只读排障查询和显式运维写操作
```

`smoke` 不启动 API/worker，不执行 Alembic migration，不直接查库推进流程，也不替代 `jobs.sh` 的 timeline、attempts 或 billing 排障查询。

## 目录边界

- `smoke/harness/errors.py`：通用 smoke 错误和 exit code 合同。
- `smoke/harness/env_runtime.py`：通用 env file 解析、`ENV_FILE` 选择和运行时环境变量优先级。
- `smoke/harness/http_runtime.py`：通用 HTTP JSON 请求和 response envelope 解析。
- `smoke/harness/service_runtime.py`：通用 FastAPI 服务 base URL、远端 URL 保护、认证 header 和服务运行上下文。
- `smoke/harness/cli_contract.py`：通用全局参数、Callback 参数和场景可用全局参数校验。
- `smoke/harness/callback_capture.py`：通用本地 callback receiver、签名校验、事件等待和 capture snapshot。
- `smoke/jobs/`：可选 Job 服务扩展层；放 Job 标准参数、`jobs_url` 推导、Job 轮询和 Job 服务依赖检查。
- `smoke/scenarios.py`：当前项目的 `smoke.sh list` 场景元数据事实源。
- `smoke/cli.py`：当前项目的命令装配层；公共 `list/health/ready` 不依赖业务 flow，Job/业务命令按需导入具体 flow。
- `smoke/flows/examples/`：标准示例场景，用于验证 Job 服务平台链路，也可作为新项目接入 smoke 的参考。
- `smoke/flows/<domain>/`：当前项目业务或 provider smoke；业务参数、提交 payload、结果断言和业务证据放在各自子目录。

## 入口

```bash
./scripts/smoke.sh list
./scripts/smoke.sh health
./scripts/smoke.sh ready
```

`health` 和 `ready` 是普通 FastAPI 服务级检查：只解析 env、推导 base URL、检查认证 header 配置，并访问 `/health` 或 `/healthz`。Job 场景会在自己的 summary 里额外输出 `jobs_url`、OSS 配置和 Job 服务依赖检查结果。

`scripts/smoke.sh` 是 thin wrapper；业务执行最终进入：

```bash
ENV_FILE=.env uv run python -m smoke [global options] <scenario> [standard job options] [business options]
```

Smoke 全局选项统一放在场景命令前，例如 `--base-url`、`--env-file`、`--allow-remote-api`、`--service-api-key`、`--caller-id`、`--timeout`、`--poll-interval`、`--output-dir` 和 `--json`。

标准 Job 参数由支持的 Job 场景复用，例如 `--confirm-run`、`--confirm-cost`、`--confirm-upload`、`--client-request-id` 和 `--expect-status`。

标准 Callback 参数由支持 callback 的 Job 场景复用，例如 `--callback-url`、`--local-callback`、`--callback-event`、`--wait-callback/--no-wait-callback` 和 `--callback-timeout-seconds`。

## 场景

当前场景以 `python -m smoke --json list` 为事实源；其中 `entrypoints` 是可直接执行的入口。业务 Job 场景会真实提交 Job、等待终态并查询结果证据；provider probe/helper 必须显式确认费用或上传副作用。

`example-lifecycle-probe` 使用 `visibility=demo` 的标准探针 Job，仅用于 `local` / `dev` 平台链路验收；它不调用真实模型，不产生模型费用。配置 `--local-callback` 时可以验证 callbacker 投递；普通成功链路不会证明 reconciler 被触发。

常用场景：

```bash
ENV_FILE=.env ./scripts/smoke.sh --json example-lifecycle-probe \
  --confirm-run \
  --local-callback

ENV_FILE=.env ./scripts/smoke.sh --timeout 180 llm-job-billing --confirm-cost

ENV_FILE=.env ./scripts/smoke.sh \
  --timeout 300 \
  --poll-interval 2 \
  tagged-text-translation \
  --confirm-cost \
  --source-language en \
  --target-language zh \
  --text '<span>Hello {user_name}, welcome back!</span>'

ENV_FILE=.env ./scripts/smoke.sh poster-title-image \
  --confirm-cost \
  --reference .data/title/example.png \
  --language es \
  --title-text "Cuando el amor se alejo"

ENV_FILE=.env ./scripts/smoke.sh audio-stem-separation run \
  --confirm-run \
  --confirm-upload \
  --input-file .data/misc/2485_0003_S6_梁萧.wav
```

带全局轮询参数的音频示例：

```bash
ENV_FILE=.env ./scripts/smoke.sh \
  --timeout 7200 \
  --poll-interval 5 \
  audio-stem-separation run \
  --confirm-run \
  --confirm-upload \
  --input-file .data/misc/2485_0003_S6_梁萧.wav
```

## Exit Codes

| Code | 含义 |
|---|---|
| `0` | 通过 |
| `1` | 场景失败 |
| `2` | 参数错误或配置缺失 |
| `3` | 服务未 ready |
| `4` | 外部依赖不可用或证据不可达 |
| `5` | 超时 |

## 新增业务 Flow

新增业务 smoke 时按这个拆分：

```text
smoke/cli.py
  -> 声明 Typer 命令、标准参数和业务参数
  -> 把全局参数组装成 harness dataclass
  -> 调用 smoke/flows/<domain>/<business>.py

smoke/harness/
  -> 复用 env/http/service/callback/CLI contract
  -> 不放 Job payload、业务 payload 和业务断言

smoke/jobs/
  -> 复用 Job 参数、Job runtime context、Job 轮询
  -> 普通 FastAPI 项目不需要 Job smoke 时可以整体裁剪

smoke/flows/<domain>/<business>.py
  -> 构造 create-job payload
  -> 提交 Job、轮询终态、查询 billing 或 artifact
  -> 做业务结果断言并输出 summary/responses
```

`example-lifecycle-probe` 是标准参考：它演示了 `api -> dispatcher -> taskiq_worker` 验收，以及配置 callback 后的 `callback_outbox -> callbacker -> receiver -> callback.status=delivered` 验收。

## 跨项目复用

普通 FastAPI 项目的最小复用层是：

```text
smoke/harness/
smoke/__init__.py
smoke/__main__.py
smoke/cli.py
smoke/scenarios.py
scripts/smoke.sh
```

其中 `smoke/harness/` 是不用动的公共层；`smoke/cli.py` 和 `smoke/scenarios.py` 是项目装配层。普通 FastAPI 项目复制后保留 `list/health/ready`，删除或替换当前 Job/业务命令即可，不需要复制 `smoke/jobs/` 和当前 `smoke/flows/<domain>/`。Job 服务项目再额外复制 `smoke/jobs/` 和 `smoke/flows/examples/lifecycle_probe.py`。

## 维护规则

- 新增业务 E2E 场景时，先把场景加入 `smoke/scenarios.py`，再实现命令。
- env 解析、HTTP 请求、服务上下文、response envelope 解析、callback receiver、签名校验和事件等待等跨项目能力放在 `smoke/harness/`。
- Job 轮询、`jobs_url`、Job 标准参数和 Job 服务依赖检查放在 `smoke/jobs/`。
- 业务场景只负责声明业务 payload、业务断言、业务证据，以及 callback URL 如何注入 create payload。
- 场景参数表达业务输入，不暴露服务生命周期或数据库排障细节。
- `preflight -> prepare -> submit -> poll -> assert -> collect evidence -> cleanup` 是业务场景的目标结构；新增场景应按这个生命周期组织。
- 需要真实费用、上传或写远端资源的命令必须保留显式确认参数。

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

- `smoke/harness/`：可复用的 smoke 基础能力，例如本地 callback receiver、签名校验和事件等待。
- `smoke/flows/`：具体业务或探针场景；业务参数、提交 payload、结果断言放在这里。
- `smoke/flows/examples/`：标准示例场景，用于验证 Job 服务平台链路，也可作为新项目接入 smoke 的参考。

## 入口

```bash
./scripts/smoke.sh list
./scripts/smoke.sh health
./scripts/smoke.sh ready
```

`scripts/smoke.sh` 是 thin wrapper；业务执行最终进入：

```bash
ENV_FILE=.env uv run python -m smoke [smoke options] <scenario> [args...]
```

Smoke 全局选项统一放在场景命令前，例如 `--base-url`、`--env-file`、`--timeout`、`--poll-interval`、`--output-dir` 和 `--json`。

## 场景

当前场景以 `python -m smoke --json list` 为事实源。业务 Job 场景会真实提交 Job、等待终态并查询结果证据；provider probe/helper 必须显式确认费用或上传副作用。

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

## 维护规则

- 新增业务 E2E 场景时，先把场景加入 `SCENARIOS`，再实现命令。
- callback receiver、签名校验、事件等待等通用能力放在 `smoke/harness/`，业务场景只负责声明 callback URL 如何注入 create payload。
- 场景参数表达业务输入，不暴露服务生命周期或数据库排障细节。
- `preflight -> prepare -> submit -> poll -> assert -> collect evidence -> cleanup` 是业务场景的目标结构；新增场景应按这个生命周期组织。
- 需要真实费用、上传或写远端资源的命令必须保留显式确认参数。

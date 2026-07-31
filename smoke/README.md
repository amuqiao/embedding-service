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

## 入口

```bash
./scripts/smoke.sh list
./scripts/smoke.sh health
./scripts/smoke.sh ready
```

`scripts/smoke.sh` 是 thin wrapper；业务执行最终进入：

```bash
ENV_FILE=.env uv run python -m smoke <scenario> [args...]
```

## 场景

当前场景以 `python -m smoke list --json` 为事实源。业务 Job 场景会真实提交 Job、等待终态并查询结果证据；provider probe/helper 必须显式确认费用或上传副作用。

常用场景：

```bash
ENV_FILE=.env ./scripts/smoke.sh llm-job-billing --confirm-cost

ENV_FILE=.env ./scripts/smoke.sh poster-title-image \
  --confirm-cost \
  --reference .data/title/example.png \
  --language es \
  --title-text "Cuando el amor se alejo"

ENV_FILE=.env ./scripts/smoke.sh audio-stem-separation run \
  --confirm-run \
  --input-file .data/audio/htdemucs-input.wav
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
- 场景参数表达业务输入，不暴露服务生命周期或数据库排障细节。
- `preflight -> prepare -> submit -> poll -> assert -> collect evidence -> cleanup` 是业务场景的目标结构；新增场景应按这个生命周期组织。
- 需要真实费用、上传或写远端资源的命令必须保留显式确认参数。

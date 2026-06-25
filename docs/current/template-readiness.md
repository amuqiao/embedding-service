# 模板采用就绪度

本文说明本仓库作为新业务 AI Job 微服务模板时的当前就绪边界。它只记录已经落地的事实、复制前必须处理的事项和最小验收命令；不定义具体业务 `job_type`。

## 结论

当前仓库可以作为 **试点级 AI Job 微服务模板** 复制给新业务使用。复制后应先完成模板身份替换、测试能力边界处理、生产安全配置确认和本地 smoke，再接入业务 `job_type`。

不应把本仓库当作公司级跨服务 workflow 平台。它适合单个业务微服务内部处理异步 AI Job、Taskiq worker、对象存储产物、状态查询、Callback、billing 查询和 DAG-lite root/child workflow。

## 当前可复用能力

| 能力 | 当前事实 |
|---|---|
| API | 默认前缀 `/api/v1/ai-jobs` 下的 Job、Billing、模型和 Prompt 元信息 route |
| 异步执行 | Taskiq worker + PostgreSQL/Redis，Job attempt 使用 lease、heartbeat 和 token 校验 |
| 可靠发布 | `dispatch_outbox` 负责 DB -> Taskiq，`callback_outbox` 负责 DB -> caller callback |
| 提交幂等 | `job_submission_keys` 按 caller + `client_request_id` 保证重复提交可控 |
| 恢复 | recovery loop 处理 due dispatch、stale attempt、callback、AI ledger stale pending 和 workflow reconciler |
| Workflow | `job_aggregates` 自索引表达 root/child，DAG-lite 支持 `chain`、`group`、`chord`、`map`、`starmap`、`chunks`；child AI 调用聚合到 root Job billing |
| 验证 | `check`、单 Job smoke、六模式 workflow smoke 都已有稳定脚本入口 |

## 复制后必须改

| 项 | 处理方式 |
|---|---|
| 项目身份 | 替换 `TEMPLATE_NAME`、`SERVICE_NAME`、`SERVICE_TITLE`、`POSTGRES_DB`、`COMPOSE_PROJECT_NAME` |
| API 前缀 | 按业务服务确定 `SERVICE_API_PREFIX`，默认 `/api/v1/ai-jobs` 可保留 |
| 数据库与 Redis | 为新服务使用独立 database 和独立 Redis URL/实例 |
| 模型配置 | 按业务更新 `MODEL_CONFIG_PATH` 指向的模型目录和 required env |
| Prompt 配置 | 按业务更新 `PROMPT_CONFIG_PATH` 和 Prompt/output schema 绑定 |
| 价格配置 | 如果启用 billing，更新 `PRICING_CONFIG_PATH`，并保留 ledger 事实源 |
| 对象存储 | 多副本或平台部署不能使用 `STORAGE_BACKEND=local`，应接外部对象存储 |
| Callback 签名 | 设置业务级 `CALLBACK_SIGNING_SECRET`，不要复用模板本地值 |

## 测试和示例能力边界

模板内置以下测试或示例能力用于本地验证、smoke 或真实链路样例：

- `job_test_echo`
- `job_test_add`
- `job_test_collect`
- `job_test_workflow`
- `arithmetic`
- `job_real_llm_echo`
- `job_real_llm_double_echo`

这些能力不是新业务的正式 API 合同。业务服务复制模板后有两种选择：

| 选择 | 适用场景 |
|---|---|
| 保留 | 仅用于本地或内部验证环境继续运行模板 smoke |
| 移除、禁用或由业务网关限制 | 共享环境或生产服务不允许任何测试 `job_type` 被外部调用 |

不要把 `job_test_*` 包装成正式业务能力。正式业务应新增自己的 `job_type`、schema、executor、workflow definition 和验证脚本。

## 当前不包含

- `poster_title_image` 尚未实现为当前 route 或 `job_type`。
- 当前稳定 Job cost 查询是 `GET /api/v1/ai-jobs/jobs/{job_id}/billing`，不是 `/cost`。
- 非终态 Job 不返回增量 `job_result`。
- `scripts/verify.sh` 的稳定命令不覆盖真实模型 e2e 或外部对象存储 e2e。

## 生产前必须确认

| 检查项 | 要求 |
|---|---|
| 本地绕过认证 | 生产不得启用 `DISABLE_HTTP_AUTH_HEADER=true` 或 `DISABLE_CALLER_ID_HEADER=true` |
| 本地存储 | 多副本部署不得使用 `STORAGE_BACKEND=local` |
| 部署入口 | 当前只提供 `compose-deps` 和 `compose-full`，不提供 Kubernetes、CI/CD 或云平台 Secrets 管理 |
| 跨服务编排 | 跨多个微服务的业务流程不应塞进本服务内部 workflow |
| 真实模型 e2e | 接入正式 `job_type` 后，应在 `examples/business/` 或业务仓库内维护真实业务 e2e |

## 排障入口

只读排障使用 `jobs.sh`，不要直接写修复脚本作为默认流程：

```bash
./scripts/jobs.sh list --status running --since 24h --limit 20
./scripts/jobs.sh show <job_id>
./scripts/jobs.sh inspect <job_id>
./scripts/jobs.sh timeline <job_id> --limit 50
./scripts/jobs.sh stuck --older-than 10m
```

`job_audit_events` 只做时间线和排障证据，不驱动恢复。

## 模板采用验收

复制模板并完成业务改名后，先跑离线检查：

```bash
./scripts/verify.sh check
```

涉及 Job、Taskiq、Callback、Workflow 或 Recovery 的改动，还要跑本地服务 smoke：

```bash
./scripts/dev.sh start
./scripts/verify.sh workflow-smoke
./scripts/verify.sh workflow-modes-smoke
./scripts/dev.sh stop
```

接入正式业务 `job_type` 后，新增业务 e2e。业务 e2e 应验证真实输入、真实 child Job、真实对象存储产物、Callback mock 和 billing 读模型。

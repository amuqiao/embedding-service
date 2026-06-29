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
| Workflow | `job_aggregates` 自索引表达 root/child，DAG-lite 支持 `single`、`chain`、`group`、`chord`、`map`、`starmap`、`chunks`；child AI 调用聚合到 root Job billing |
| 验证 | `check`、单 Job smoke、workflow modes smoke 都已有稳定脚本入口 |

## 复制后必须改

| 项 | 处理方式 |
|---|---|
| 项目身份与 compose 命名空间 | 替换 `TEMPLATE_NAME`、`SERVICE_NAME`、`SERVICE_TITLE`、`POSTGRES_DB`；为当前仓库设置独立 `COMPOSE_PROJECT_NAME` |
| API 前缀 | 按业务服务确定 `SERVICE_API_PREFIX`，默认 `/api/v1/ai-jobs` 可保留 |
| 数据库与 Redis | 为新服务使用独立 database 和独立 Redis URL/实例 |
| 模型配置 | 按业务更新 `MODEL_CONFIG_PATH` 指向的模型目录和 required env |
| `poster_title_image` 模型配置 | 使用 `poster_title_image` 时，确认 `app/jobs/types/poster_title_image/models.yaml` 引用的公开生图模型和内部 style probe 模型都存在于模型目录且能力匹配 |
| Prompt 配置 | 按业务更新 `PROMPT_CONFIG_PATH` 指向的基础配置，或维护 `app/jobs/types/<job_type>/prompts.yaml`；同步检查 Prompt/output schema 绑定 |
| 价格配置 | 如果启用 billing，更新 `PRICING_CONFIG_PATH`，并保留 ledger 事实源 |
| 对象存储 | 多副本或平台部署不能使用 `STORAGE_BACKEND=local`，应接外部对象存储 |
| 业务输入 OSS 白名单 | 使用 `poster_title_image` 时，按 CPP 输入来源配置 `POSTER_TITLE_IMAGE_ALLOWED_OSS_BUCKETS` 和 `POSTER_TITLE_IMAGE_ALLOWED_OSS_REGIONS` |
| Callback 签名 | 设置业务级 `CALLBACK_SIGNING_SECRET`，不要复用模板本地值 |
| 运行环境 | 设置 `APP_ENV`；`test/prd` 使用同一套发布模式校验 |

## 测试和示例能力边界

模板内置以下测试或示例能力用于本地验证、smoke 或真实链路样例：

- `job_test_echo`
- `job_test_add`
- `job_test_collect`
- `job_test_workflow`
- `arithmetic`
- `job_real_llm_echo`
- `job_real_llm_double_echo`

这些 job_type 在 registry 中使用 `visibility="demo"` 标记。`jobs.sh types` 的人读输出默认展示非 internal 的 `role="root"` 入口；需要查看 leaf、`root_or_leaf` 或全部 demo 类型时使用：

```bash
./scripts/jobs.sh types --all
./scripts/jobs.sh types --visibility demo
./scripts/jobs.sh types --role leaf
```

这些能力不是新业务的正式 API 合同。业务服务复制模板后有两种选择：

| 选择 | 适用场景 |
|---|---|
| 保留 | 仅用于本地或内部验证环境继续运行模板 smoke |
| 移除或禁用 | 不需要保留模板验证能力的业务服务 |

不要把 `job_test_*` 包装成正式业务能力。正式业务应新增自己的 `job_type`、schema、executor、workflow definition 和验证脚本。`APP_ENV=test` 或 `APP_ENV=prd` 时，服务只允许外部提交 `visibility="public"` 的 `job_type`；模板 `demo` 类型仍可保留在代码中供 `local/dev` 验证，但不能作为发布环境的外部入口。

## 当前不包含

- `poster_title_image` 是当前模板内的真实业务示例 `job_type`，不属于通用模板 smoke；复制模板时应按业务需要保留、替换或移除。
- 当前稳定 Job cost 查询是 `GET /api/v1/ai-jobs/jobs/{job_id}/billing`，不是 `/cost`。
- 通用模板不保证非终态 Job 返回增量 `job_result`；具体 `job_type` 只有显式声明 `result_snapshot_statuses` 后才可返回运行中或失败态结果快照。当前 `poster_title_image` 例外支持 `running/failed` 快照，用于返回已成功生成的 item 子集。
- `scripts/verify.sh` 的稳定命令不覆盖真实模型 e2e 或外部对象存储 e2e。

## 生产前必须确认

| 检查项 | 要求 |
|---|---|
| 运行环境 | `APP_ENV=test` 和 `APP_ENV=prd` 使用同一套发布模式校验 |
| 本地绕过认证 | 生产不得启用 `DISABLE_HTTP_AUTH_HEADER=true` 或 `DISABLE_CALLER_ID_HEADER=true` |
| 本地存储 | 多副本部署不得使用 `STORAGE_BACKEND=local` |
| 本地配置文件 | `deploy.sh` 默认 `ENV_FILE=.env`，`up` 要求该文件存在；`.env.dev`、`.env.test`、`.env.prd` 不会因 `APP_ENV` 自动加载，必须通过 `ENV_FILE` 或平台注入显式选择 |
| compose 命名空间 | 复制模板后先确认 `COMPOSE_PROJECT_NAME` 独立，避免 compose 入口拒绝复用其他目录的资源 |
| 部署入口 | 当前只提供 `compose-deps` 和 `compose-full`，不提供 Kubernetes、CI/CD 或云平台 Secrets 管理 |
| 跨服务编排 | 跨多个微服务的业务流程不应塞进本服务内部 workflow |
| 真实模型 e2e | 接入正式 `job_type` 后，应在 `examples/business/` 或业务仓库内维护真实业务 e2e |

## 排障入口

只读排障使用 `jobs.sh`，不要直接写修复脚本作为默认流程：

```bash
./scripts/jobs.sh doctor --since 10m
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

如果业务项目会使用 `compose-deps` 或 `compose-full`，还应运行 compose 部署入口检查：

```bash
./scripts/deploy.sh check
```

准备发布到测试或生产环境前，先用目标配置文件在本地跑启动配置校验，提前发现 `APP_ENV=test/prd` 下的安全配置问题：

```bash
./scripts/verify.sh env-config --env-file .env.test --app-env test
./scripts/verify.sh env-config --env-file .env.prd --app-env prd
```

涉及 Job、Taskiq、Callback、Workflow 或 Recovery 的改动，还要跑本地服务 smoke：

```bash
./scripts/dev.sh start
./scripts/verify.sh workflow-smoke
./scripts/verify.sh workflow-modes-smoke
./scripts/dev.sh stop
```

接入正式业务 `job_type` 后，新增业务 e2e。业务 e2e 应验证真实输入、真实 child Job、真实对象存储产物、Callback mock 和 billing 读模型。

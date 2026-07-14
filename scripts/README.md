# scripts 维护规范

本文说明 `scripts/` 目录下本地入口脚本的职责边界和维护规则。具体参数以各脚本自身 `-h` 输出为准，本文不复制完整命令手册。

## 工作模型

`scripts/` 目录提供本仓库稳定的本地操作入口。入口脚本应把“开发、验证、部署形态、真实流程、只读排障”分开，避免一个脚本承担跨领域职责。

```text
dev.sh          本地服务生命周期
verify.sh       一次性验证任务
deploy.sh       docker compose 部署形态
k8s.sh          已部署 Pod 内手动运维
load.sh         项目级压测入口
triton-bench.sh Triton 推理服务直压入口
jobs.sh         Job 只读查询与排障
job-ops.sh      Job 写操作运维入口
real-flow.sh    手动真实模型/对象存储流程验证
models.sh       本地模型资产下载、路径和必需文件检查
media.sh        本地音视频素材探测、校验和准备
tools.sh        无默认持久副作用的本地开发辅助工具和只读代码清单查看
```

新增脚本前，先判断它是否属于已有入口的子命令。只有当职责边界不同、生命周期不同或安全边界不同，才新增顶层 `*.sh`。模型权重这类会写入 `.data/models/`、可能访问远端但不执行真实推理的本地资产准备，归属 `models.sh`，不放入默认无持久副作用的 `tools.sh`。
音视频素材探测、转码准备和业务输入格式校验归属 `media.sh`；它只处理本地素材文件，不下载模型、不执行推理、不提交 Job、不上传对象存储。
Triton 直压归属 `triton-bench.sh`；它只直连推理服务，不创建 FastAPI Job，不访问 DB/Redis/OSS，不触发 callback，不替代 `load.sh` 的业务链路压测。
已注册 tool、capability 和 job_type capability 关系归属 `tools.sh registry` 只读查看；当前治理事实见 `docs/current/registry-governance.md`。

## 入口职责

Shell 入口默认只负责：

- 定位仓库根目录和运行时。
- 加载必要的公共 shell helper。
- 做轻量参数分发。
- 提供稳定、可读的 help。
- 调用下沉实现脚本或 Python CLI。

不要在顶层 shell 入口里堆复杂业务逻辑。复杂逻辑应下沉到 `scripts/<domain>/` 下的 Python 或 shell 模块，并保持函数边界清楚。

## Help 分层

脚本 help 的完整 envelope 合同维护在 [`../docs/current/script-entrypoint-contract.md`](../docs/current/script-entrypoint-contract.md)。这里不重复维护大纲、示例数量、输出边界或 exit code 细则。

维护脚本 help 时只记住三条：

- 顶层 help 回答“这个入口是什么、有哪些命令、最小怎么开始”。
- 子命令 help 回答“这个具体动作如何配置和复制修改”。
- 命令索引只能有一个事实源；已有自动 `Commands` 时，不要在手写区再维护一份完整命令目录。

## 配置边界

本地运行形态配置、应用业务配置、密钥、模型参数和数据库连接统一放在仓库根目录 `.env`。`.env.example` 是唯一可提交配置模板；不要再维护 `scripts/.env` 或 `scripts/.env.example`。

新增脚本读取配置时应沿用现有优先级和 helper，不要重新发明配置加载规则。真实流程脚本只能面向本地 API，不能默认指向远程生产服务。

## 运行模式边界

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

修改服务启动、Job workflow、对象存储或真实流程执行路径时，还应按项目根目录 `AGENTS.md` 的验证要求补充 smoke 或真实流程验证。

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
- 是否为费用、上传、写库或真实流程设置显式确认参数。
- 是否完成最小验证，并在需要时运行 `./scripts/verify.sh check`。

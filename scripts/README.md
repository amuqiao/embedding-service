# scripts 维护规范

本文说明 `scripts/` 目录下本地入口脚本的职责边界和维护规则。具体参数以各脚本自身 `-h` 输出为准，本文不复制完整命令手册。

## 工作模型

`scripts/` 目录提供本仓库稳定的本地操作入口。入口脚本应把“开发、验证、部署形态、真实流程、只读排障”分开，避免一个脚本承担跨领域职责。

```text
dev.sh          本地服务生命周期
verify.sh       一次性验证任务
deploy.sh       docker compose 部署形态
k8s.sh          已部署 Pod 内手动运维
jobs.sh         Job 只读查询与排障
real-flow.sh    手动真实模型/对象存储流程验证
```

新增脚本前，先判断它是否属于已有入口的子命令。只有当职责边界不同、生命周期不同或安全边界不同，才新增顶层 `*.sh`。

## 入口职责

Shell 入口默认只负责：

- 定位仓库根目录和运行时。
- 加载必要的公共 shell helper。
- 做轻量参数分发。
- 提供稳定、可读的 help。
- 调用下沉实现脚本或 Python CLI。

不要在顶层 shell 入口里堆复杂业务逻辑。复杂逻辑应下沉到 `scripts/<domain>/` 下的 Python 或 shell 模块，并保持函数边界清楚。

## Help 分层

顶层 help 回答“这个入口是什么、有哪些命令、最小怎么开始”。子命令 help 回答“这个具体命令如何配置和复制修改”。

对于单层 Bash 入口，例如 `dev.sh`、`verify.sh`、`deploy.sh`、`k8s.sh`：

- 顶层 `-h` 可以列出命令、边界、环境变量、常用示例和 exit code。
- 示例应保持入口级，不展开过深排障流程。

对于多子命令 Typer 入口，例如 `jobs.sh`、`real-flow.sh`：

- 顶层 `-h` 只放基础用法和少量最短示例。
- 复杂过滤、批量输入、真实调用、下载、上传、JSON 格式等进阶示例放在对应子命令 `-h`。
- 顶层 help 应提示用户运行 `./scripts/<name>.sh <command> -h` 查看进阶用法。

示例命令必须可复制修改。长命令使用反斜杠换行，并用 Click/Typer 的 `\b` 保留格式。

## 示例放置

示例应遵循这些规则：

- 顶层示例只展示最常用、低歧义的入口路径。
- 子命令示例可以展示多个典型变体。
- 涉及真实费用的命令必须展示 `--confirm-cost`。
- 涉及上传或远程写入的命令必须展示对应确认参数，例如 `--confirm-upload`。
- 涉及本地文件输入时，应使用仓库内约定的 `.data/` 示例路径。
- JSON 输入示例只放最小结构，不复制完整业务合同。

不要把同一条复杂示例同时维护在顶层 help、子命令 help 和 README 中。README 只写规则，命令细节以脚本 help 为准。

## 输出与副作用

脚本输出应稳定、可扫描，并明确 stdout / stderr 边界：

- 人读输出使用项目已有 `section` / `event` / table 风格。
- 机器读输出使用 `--json`，并保证 stdout 只包含 JSON。
- 错误原因输出到 stderr。
- 会产生费用、写远程对象、写数据库或触发真实 Job 的命令必须显式确认。
- 只读排障入口不得修改数据库、投递消息、重试 Job 或重放 callback。

Exit code 应保持小而稳定：

- `0` 表示成功。
- `2` 表示参数、配置或前置条件错误。
- 其他非 0 code 由具体入口按现有约定定义，并在 help 中说明。

## 配置边界

本地运行形态配置放在 `scripts/.env`，应用业务配置、密钥、模型参数和数据库连接放在仓库根目录 `.env`。

新增脚本读取配置时应沿用现有优先级和 helper，不要重新发明配置加载规则。真实流程脚本只能面向本地 API，不能默认指向远程生产服务。

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
- 是否复用 `scripts/lib/` 或既有 Python helper。
- 是否避免 silent fallback；配置错误应快速失败。
- 是否明确 stdout / stderr / `--json` 行为。
- 是否为费用、上传、写库或真实流程设置显式确认参数。
- 是否完成最小验证，并在需要时运行 `./scripts/verify.sh check`。

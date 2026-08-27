# 脚本入口合同

本文记录当前仓库脚本入口和子命令的 help / output 合同。它约束 `scripts/` 下稳定入口脚本的 `-h` / `--help` 输出结构、子命令 help、输出格式和副作用边界，避免每个脚本各自组织说明、重复维护命令目录或遗漏默认行为。

## 心智模型

脚本 help 不是完整手册，而是可执行入口的合同 envelope。读者打开 `-h` 时，应该能快速判断：

- 这个入口负责什么，不负责什么。
- 应该复制哪条命令开始。
- 执行前需要哪些环境、配置或确认参数。
- 默认会读取什么、输出什么、修改什么。
- 失败时应按哪个 exit code 和 stderr 定位问题。

长期解释、排障路径和业务背景不放进 help 主体；应放入 `docs/current/`、`docs/runbooks/` 或对应子命令 `-h`。

入口目录分层、能力归属和新增顶层脚本准入规则由 [`../../scripts/README.md`](../../scripts/README.md) 维护。本文不重复维护入口清单，只规定每个入口和子命令对用户暴露时必须满足的 help、输出和副作用合同。

运行角色启动脚本是部署拓扑事实源：`start-api.sh`、`start-worker.sh`、`start-dispatcher.sh`、`start-callbacker.sh` 和 `start-reconciler.sh` 分别只启动一个角色；`start-worker-bundle.sh` 只组合 Taskiq worker、dispatcher、callbacker 和 reconciler。`scripts/run.sh` 仍只编排 recipe，不能直接复制这些进程管理细节。

## 生成方式

不同脚本可以使用不同实现方式，但命令索引只能有一个事实源：

| 类型 | 当前例子 | 规则 |
|---|---|---|
| 纯 shell 手写 help | `run.sh`、`dev.sh`、`verify.sh`、`deploy.sh`、`k8s.sh`、`tools.sh` | 可以手写 `用法`、`命令`、`选项` 和说明区块。 |
| shell wrapper + Typer CLI | `jobs.sh`、`redis.sh`、`job-ops.sh`、`load.sh` | Typer 自动生成的 `Usage`、`Options`、`Commands` 是命令和参数事实源；手写 epilog 不能再重复一份完整命令目录。 |
| shell wrapper + Python 参数规范层 + Typer 场景实现 | `smoke.sh` | wrapper help 和 `python -m smoke` 共同定义 `./scripts/smoke.sh [smoke options] <scenario> [scenario options]`。全局 smoke 参数必须在场景名前；场景实现仍复用 Typer 命令函数。 |
| shell wrapper + 普通 Python / argparse | `oss.sh`、`tools.sh env-url` 这类下沉实现 | 参数事实源以实际执行层为准；wrapper 只在需要统一入口体验时手写薄 help。 |

如果 CLI 框架已经生成 `Commands`，手写区不要再出现“命令说明”“命令列表”“子命令索引”等全量目录。需要帮助选择命令时，写“常用示例”“默认行为”或“排障路径”，不要按命令名逐个复述。

本项目只维护当前合同，不保留旧 help 格式兼容。旧测试、旧文档或旧脚本注释如果要求另一套区块名、命令目录格式或示例组织方式，应删除或改成本文标准。

## 顶层 Envelope

顶层 `-h` 回答“这个入口是什么、有哪些命令、最小怎么开始”。区块按以下顺序组织；没有对应内容时可以省略，但不要随意换名或改变主顺序。

```text
用法 / Usage
一句话职责
选项 / Options
命令 / Commands
作用域
不负责
运行环境
默认行为
配置与环境变量
输出
关键概念
成功标准
运行产物
副作用与保护边界
常用示例
进阶用法
Exit Codes
```

区块职责如下：

| 区块 | 职责 |
|---|---|
| `用法 / Usage` | 展示最小命令形态、参数位置和 help 入口。 |
| 一句话职责 | CLI 框架 help 的短描述，或手写 help 开头的一句入口定位。 |
| `选项 / Options` | 由框架或手写 help 展示入口级选项；不要塞业务解释。 |
| `命令 / Commands` | 入口支持的命令索引；有自动 `Commands` 时只用自动生成区。 |
| `作用域` | 入口负责什么，允许在哪些环境使用。 |
| `不负责` | 容易误用或风险高时单列；简单边界可并入 `作用域`。 |
| `运行环境` | 必需 shell、Python、Docker、Pod、外部 CLI、服务依赖。 |
| `默认行为` | 无参默认动作、默认窗口、默认 limit、默认输出模式。没有默认动作时省略。 |
| `配置与环境变量` | 配置加载顺序、关键环境变量和运行时覆盖规则。 |
| `输出` | stdout / stderr / `--json` 约定，必要时说明日志或文件输出。 |
| `关键概念` | `scope`、`mode`、`service`、`record_scope` 等入口级概念。 |
| `成功标准` | 只在入口存在聚合命令或生命周期命令时出现。 |
| `运行产物` | PID、日志、临时目录、下载目录等用户需要知道的位置。 |
| `副作用与保护边界` | 写库、上传、费用、远端访问、进程生命周期、幂等性和 fail-fast 边界。 |
| `常用示例` | 3 到 6 条最高频、可复制修改的入口级路径。 |
| `进阶用法` | 指向 `./scripts/<name>.sh <command> -h` 或 runbook。 |
| `Exit Codes` | 稳定退出码；复杂工具原样透传时说明“其他非 0 由子任务返回”。 |

顶层 help 不维护完整排障手册，也不穷举所有参数组合。复杂过滤、批量输入、远端环境、JSON 输出、上传下载和真实费用示例应下放到子命令 `-h` 或 runbook。

## 子命令 Envelope

子命令 `-h` 回答“这个具体动作如何配置和复制修改”。区块按以下顺序组织：

```text
用法 / Usage
一句话职责
参数 / Options / Arguments
默认行为
输入要求
输出
副作用与保护边界
常用示例
补充格式
Exit Codes
```

子命令 help 可以比顶层更具体，但仍应遵守：

- `-h` / `--help` 必须在任何执行动作前拦截。长耗时、写库、上传、费用、远端访问、启动/停止服务和迁移类子命令尤其不能把 `-h` 当作普通参数继续执行。
- 至少提供 1 条可复制示例。
- 有 `--json`、过滤条件、确认参数、远端环境、批量输入或输出文件时，提供 2 到 3 条常用示例。
- 高风险或高复杂度命令可以超过 3 条示例，但必须按场景分组。
- 复杂输入格式只放最小可用结构，例如 JSON 最小格式；完整业务合同链接到 `docs/api/`。
- 输入要求、素材要求、语种要求、下载路径等领域说明可以放在示例后，但不要冲掉前面的 envelope。

多级子命令按“每一级都能 help”治理：

- `./scripts/<entry>.sh -h` 说明入口边界和可用命令。
- `./scripts/<entry>.sh <command> -h` 说明该动作的参数、输入、输出和副作用。
- `./scripts/<entry>.sh <domain> <command> -h` 说明具体领域动作，例如 `media.sh audio prepare -h`。

新增子命令必须有自己的 help 可达路径。不要只在顶层 README 或 runbook 说明参数，而让 `-h` 无法拦截。

## 示例规则

示例的职责是帮助用户判断“现在该复制哪条命令”，不是公平展示每个命令。

- 顶层示例优先选择最高频、最低前置条件、最能代表入口边界的 3 到 6 条。
- 子命令示例展示该命令自己的关键参数组合，不只重复顶层最短示例。
- 占位符只用于必须由用户提供的值，例如 `<job_id>`、`<api-pod>`。
- 涉及费用必须展示 `--confirm-cost`。
- 涉及上传或远程写入必须展示对应确认参数，例如 `--confirm-upload`。
- 涉及远端 API 必须展示 `--allow-remote-api` 或等价保护参数。
- 涉及本地文件输入时，优先使用仓库约定的 `.data/` 示例路径。
- 长命令使用反斜杠换行；Typer / Click epilog 使用 `\b` 保留格式。

## 输出与副作用

脚本输出必须稳定、可扫描，并明确 stdout / stderr 边界：

- 人读输出使用项目已有 `section` / `event` / table 风格。
- 机器读输出使用 `--json`，并保证 stdout 只包含 JSON。
- 错误原因输出到 stderr。
- 默认人读输出不得夹带完整 JSON 或大段 JSON 摘要。
- 只读排障入口不得修改数据库、投递消息、重试 Job 或重放 callback。
- 会产生费用、写远程对象、写数据库、触发真实 Job、访问远端 API 或迁移数据库的命令必须显式确认。
- 不要为了“更稳”新增 silent fallback；配置错误、非法环境和越界参数应 fail-fast。

只读、显式写入和费用路径必须在入口合同中分开：

| 路径 | 默认行为 | 必需保护 |
|---|---|---|
| 只读排障 | 不修改 DB / Redis / OSS / Job，不投递消息 | 可支持 `--json`，错误直接失败。 |
| 本地写文件 | 只写明确输出路径或 `.run` / `.data` 约定目录 | 默认拒绝覆盖，覆盖必须显式参数。 |
| 远端写入 | 默认不写；写入必须由子命令语义或确认参数触发 | `--confirm`、`--confirm-upload` 或等价确认。 |
| 费用动作 | 默认不产生真实模型费用 | `--confirm-cost` 或等价确认。 |
| 写库运维 | 默认只读 | `--confirm`，并在 help 中说明前置条件和失败语义。 |

## 维护与验证

修改脚本 help 时至少运行：

```bash
./scripts/<changed-entry>.sh -h
uv run python -m compileall scripts
```

修改入口 help、公共 helper 或本合同后，运行：

```bash
./scripts/verify.sh check
```

`verify.sh check` 只维护当前脚本合同，不维护旧格式兼容。自动 `Commands` 入口由当前合同检查防止回归：

- 顶层 help 必须包含自动 `Commands`。
- 手写区不能再出现重复命令目录、旧“命令说明”目录或另一个子命令索引。
- `jobs.sh`、`smoke.sh`、`load.sh` 等入口的子命令 help 必须保持可访问。

新增脚本前，先判断是否属于已有入口的子命令。只有职责边界、生命周期或安全边界不同，才新增顶层脚本。

新增或调整脚本时，验收标准是新合同成立：

- 顶层入口职责清楚，且能说明“不负责什么”。
- 子命令 help 能在执行动作前拦截。
- 命令索引事实源只有一个。
- `--json` 时 stdout 只输出 JSON。
- 写远端、写库、产生费用和覆盖文件都有显式确认或明确参数。
- 编排入口只组合稳定入口，不新增底层实现。

# Trae Python 虚拟环境自动激活排障复盘

本文复盘 Trae 新建终端无法自动激活本项目 `.venv` 的问题，并给出用户级和项目级配置示例。

本文只处理 Trae / VS Code 风格编辑器内置终端的 Python 虚拟环境激活问题，不负责 Python 依赖安装、`uv` 同步、项目服务启动或 shell 主题配置。

## 先理解这件事

Trae 中有两条容易混淆的链路：

```text
右下角 Python 解释器
  -> 编辑器、Pylance、运行/调试选择哪个 Python

新建终端自动激活
  -> 终端启动时是否执行或等效完成 .venv 激活
```

右下角显示 `.venv` 只说明解释器已经选对，不代表普通新建终端一定会执行 `.venv/bin/activate`。

本次问题的关键事实是：

```text
Trae 报错:
Python Environments: Failed to initialize environment managers.

新建终端:
which python
-> python not found

手动激活:
source .venv/bin/activate
which python
-> /Users/admin/Code/cms/embedding-service/.venv/bin/python
```

这说明 `.venv` 本身可用，失败点在 Trae 的 Python Environments 自动激活链路，而不是虚拟环境损坏。

## 典型现象

打开本项目后，Trae 右下角已经显示：

```text
Python 3.13.13 ('.venv': venv)
```

但新建普通终端后：

```bash
which python
```

输出：

```text
python not found
```

手动执行：

```bash
source .venv/bin/activate
```

再检查：

```bash
which python
python -V
```

输出变为：

```text
/Users/admin/Code/cms/embedding-service/.venv/bin/python
Python 3.13.13
```

同时 Trae 可能提示：

```text
Python Environments: Failed to initialize environment managers.
Some features may not work correctly. Check the Output panel for details.
```

## 根因判断

这不是项目 `.vscode/settings.json` 的解释器路径写错，也不是 `.venv/bin/python` 不存在。

根因更接近以下链路：

```text
Trae Python Environments 初始化失败
  -> 没有完成环境管理器初始化
  -> 新终端自动激活 hook 没有执行
  -> PATH 没有包含 .venv/bin
  -> which python 找不到 python
```

`python.defaultInterpreterPath` 和右下角解释器选择，只能保证编辑器知道要使用哪个 Python。终端能否自动激活，还依赖 Python / Python Environments 扩展在新终端创建时正常运行。

## 用户级配置

用户级配置文件路径：

```text
~/Library/Application Support/Trae CN/User/settings.json
```

用户级配置只放全局通用偏好，不放具体项目的 `.venv` 路径。

最小需要确认的是这一项：

```json
{
  "python-envs.terminal.autoActivationType": "shellStartup"
}
```

如果用户级 `settings.json` 已经有其他配置，只把这一项合并进去，不要用上面的最小示例整体覆盖原文件。

不要把下面这种项目路径放进用户级配置：

```text
/Users/admin/Code/cms/embedding-service/.venv
```

否则打开其他项目时，也可能被强行注入本项目的 Python 环境。

## 项目级配置

项目级配置文件路径：

```text
.vscode/settings.json
```

项目级配置需要合并到现有 `.vscode/settings.json`，不要直接覆盖已有项目配置。

推荐配置：

```json
{
  "python.defaultInterpreterPath": "${workspaceFolder}/.venv/bin/python",
  "python.terminal.activateEnvironment": true,
  "python.terminal.activateEnvInCurrentTerminal": true,
  "python-envs.terminal.autoActivationType": "shellStartup",
  "terminal.integrated.defaultProfile.osx": "zsh",
  "terminal.integrated.profiles.osx": {
    "zsh": {
      "path": "zsh",
      "args": ["-l"]
    }
  },
  "terminal.integrated.env.osx": {
    "VIRTUAL_ENV": "${workspaceFolder}/.venv",
    "PATH": "${workspaceFolder}/.venv/bin:${env:PATH}"
  }
}
```

其中真正绕过 Trae 自动激活问题的是：

```json
{
  "terminal.integrated.env.osx": {
    "VIRTUAL_ENV": "${workspaceFolder}/.venv",
    "PATH": "${workspaceFolder}/.venv/bin:${env:PATH}"
  }
}
```

如果当前 Trae 版本没有展开 `${workspaceFolder}`，把它替换成当前项目绝对路径：

```text
/Users/admin/Code/cms/embedding-service
```

这不是执行完整的 `activate` 脚本，而是在 Trae 集成终端启动时直接注入两个关键环境变量：

```text
VIRTUAL_ENV
  -> 标记当前虚拟环境路径

PATH
  -> 把 .venv/bin 放到查找路径最前面
```

对普通 Python venv 来说，这足以让 `python`、`pip` 等命令优先命中项目 `.venv`。

## 验证步骤

修改配置后执行：

```text
Developer: Reload Window
```

然后关闭所有旧终端，重新新建终端。

在新终端中检查：

```bash
echo $VIRTUAL_ENV
which python
python -V
```

期望输出：

```text
/Users/admin/Code/cms/embedding-service/.venv
/Users/admin/Code/cms/embedding-service/.venv/bin/python
Python 3.13.13
```

如果 `which python` 仍然是 `python not found`，说明项目级 `terminal.integrated.env.osx` 没有被 Trae 当前窗口加载。先确认打开的是项目根目录，再执行 `Developer: Reload Window`，并关闭旧终端后重新创建。

## 排查 Trae 扩展报错

如果要继续定位 Trae 原生自动激活失败原因，打开输出面板：

```text
Cmd + Shift + U
```

在右侧下拉选择：

```text
Python Environments
Python
Trae
```

重点看是否存在环境管理器初始化、解释器发现、权限、缓存或扩展加载失败信息。

这一步用于定位 Trae 或扩展问题，不影响项目级 `terminal.integrated.env.osx` 的绕过方案。

## 维护规则

保留这条分工：

```text
用户级 settings.json
  -> 全局偏好，不写具体项目路径

项目级 .vscode/settings.json
  -> 本项目解释器和终端环境
```

当 Trae 后续修复 `Python Environments` 初始化失败后，可以尝试移除项目级 `terminal.integrated.env.osx`，只保留 Python 扩展的原生自动激活配置。移除前必须重新验证：

```bash
echo $VIRTUAL_ENV
which python
python -V
```

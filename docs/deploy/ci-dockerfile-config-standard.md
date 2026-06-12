# CI 与 Dockerfile 通用配置规范

本文说明项目接入 GitLab CI 容器构建时，`.gitlab-ci.yml`、`Dockerfile` 和 `Dockerfile_OS` 三类文件的职责边界、推荐配置和维护规则。

本文适用于需要通过 GitLab CI 构建代码镜像与运行环境镜像的后端服务项目；不负责说明 Kuboard 平台操作、Kubernetes 工作负载配置或业务代码发布流程。

## 一、整体关系

三类文件的职责应保持分离：

```text
.gitlab-ci.yml
  负责定义 CI 触发条件、构建任务、镜像 tag、镜像 push 和通知输出

Dockerfile
  负责构建代码镜像，通常只包含当前项目代码或发布包

Dockerfile_OS
  负责构建运行环境镜像，通常包含语言运行时、系统依赖和基础 Python 包
```

代码镜像和运行环境镜像应独立维护。开发日常发布通常只触发 `Dockerfile` 构建；运行环境镜像只有在基础镜像、系统依赖、语言版本或通用依赖变化时才需要更新。

## 二、`.gitlab-ci.yml` 配置规范

`.gitlab-ci.yml` 是 CI 构建入口，应明确回答四个问题：

```text
谁来执行：runner tag
什么时候执行：branch 和 commit message 规则
构建什么：Dockerfile 或 Dockerfile_OS
产物在哪里：镜像仓库地址、镜像 tag 和通知文件
```

推荐模板：

```yaml
include:
  - project: 'ci-base/ci-template'
    ref: send-msg
    file: '/shell-msg.yaml'

variables:
  dingtoken: "${DINGTALK_TOKEN}"

stages:
  - build

before_script:
  - cp -r $CI_PROJECT_DIR /data/gitlab-ci/$CI_COMMIT_REF_NAME
  - cd /data/gitlab-ci/$CI_COMMIT_REF_NAME/$CI_PROJECT_NAME

go-build:
  extends: .notify
  stage: build
  tags:
    - build-k8s
  rules:
    - if: '$CI_COMMIT_REF_NAME =~ /^(test|gray|master)$/ && $CI_COMMIT_MESSAGE =~ /\bbuild\b/'
  script:
    - |
      TAG=`date "+%Y%m%d%H%M%S"`
      OS_TAG='OS-'`date "+%Y%m%d%H%M%S"`
      images=cms-images-sz-registry-vpc.cn-shenzhen.cr.aliyuncs.com/cms-images/$CI_COMMIT_REF_NAME-$CI_PROJECT_NAME:$TAG
      os_images=cms-images-sz-registry-vpc.cn-shenzhen.cr.aliyuncs.com/os/$CI_PROJECT_NAME:$OS_TAG

      if echo "$CI_COMMIT_MESSAGE" | grep -q '\[ci build\]'; then
        docker build -t $images -f Dockerfile .
        docker push $images
        echo "$images" > /tmp/ci_images.txt
        rm -rf /data/gitlab-ci/$CI_COMMIT_REF_NAME/$CI_PROJECT_NAME
      fi

      if echo "$CI_COMMIT_MESSAGE" | grep -q '\[build:os\]'; then
        docker build --add-host gitlab.example.com:192.168.0.228 -t $os_images -f Dockerfile_OS .
        docker push $os_images
        echo "$os_images" > /tmp/ci_images.txt
        rm -rf /data/gitlab-ci/$CI_COMMIT_REF_NAME/$CI_PROJECT_NAME
      fi
```

关键规则：

| 配置项 | 规范 |
| --- | --- |
| `include` | 复用统一通知模板，避免每个项目重复维护通知逻辑 |
| `dingtoken` | 应优先使用 GitLab CI/CD 变量注入，不应在规范文档或业务代码中写真实 token |
| `stages` | 至少保留 `build` 阶段；没有明确需求时不要扩展多阶段 |
| `tags` | 使用运维提供的 runner tag，例如 `build-k8s` |
| `rules` | 限制分支和 commit message，避免普通提交误触发镜像构建 |
| `[ci build]` | 构建代码镜像，使用 `Dockerfile` |
| `[build:os]` | 构建运行环境镜像，使用 `Dockerfile_OS` |
| `/tmp/ci_images.txt` | 写入最终镜像地址，供通知模板读取 |

commit message 应显式表达构建意图：

```text
[ci build] release code changes
[build:os] update python runtime image
```

不推荐使用含糊提交信息触发构建：

```text
update
fix
merge branch
```

## 三、`Dockerfile` 配置规范

`Dockerfile` 用于构建代码镜像。当前项目的代码镜像只负责把项目文件复制到临时目录，运行环境由 `Dockerfile_OS` 对应镜像提供。

推荐模板：

```dockerfile
FROM alpine:latest

COPY . /tmp/
```

配置说明：

| 指令 | 说明 |
| --- | --- |
| `FROM alpine:latest` | 使用轻量基础镜像承载代码文件 |
| `COPY . /tmp/` | 将当前项目内容复制到镜像内 `/tmp/`，供运行环境容器或初始化流程使用 |

维护规则：

- 不在代码镜像中安装 Python、系统包或运行时依赖。
- 不在代码镜像中写入真实环境变量、API Key、数据库密码或通知 token。
- 不把本地缓存、虚拟环境、构建产物和临时文件复制进镜像；应通过 `.dockerignore` 控制上下文。
- 如需改变代码在容器内的目标路径，应同步确认 Kubernetes 初始化容器或启动脚本的读取路径。

## 四、`Dockerfile_OS` 配置规范

`Dockerfile_OS` 用于构建运行环境镜像。它承载语言版本、基础工具和项目运行依赖，变更频率应低于业务代码。

推荐模板：

```dockerfile
FROM cms-images-sz-registry-vpc.cn-shenzhen.cr.aliyuncs.com/os/python:3.13.11

ENV UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple

WORKDIR /root

COPY pyproject.toml /root/

RUN pip3 install uv
RUN uv pip install . --system
```

配置说明：

| 指令 | 说明 |
| --- | --- |
| `FROM .../os/python:3.13.11` | 以运维维护的 Python 基础镜像作为运行环境基底 |
| `ENV UV_INDEX_URL=...` | 配置 Python 依赖安装源，提高内网或国内构建稳定性 |
| `WORKDIR /root` | 固定运行环境构建目录 |
| `COPY pyproject.toml /root/` | 只复制依赖声明文件，避免运行环境镜像绑定业务代码 |
| `RUN pip3 install uv` | 安装依赖管理工具 |
| `RUN uv pip install . --system` | 将项目依赖安装到系统 Python 环境 |

维护规则：

- 只有运行时、系统依赖、基础 Python 依赖或包管理方式变化时，才更新 `Dockerfile_OS`。
- `Dockerfile_OS` 不应承载业务代码发布职责，业务代码变更应通过 `Dockerfile` 对应代码镜像发布。
- 基础镜像版本应显式写清楚，不建议使用含义不稳定的浮动 tag。
- 构建源地址可以按环境切换，但应通过 `ARG` 或 CI 变量配置，避免在多个项目中分散硬编码。
- 更新 `Dockerfile_OS` 后，应使用 `[build:os]` 单独触发运行环境镜像构建，并确认 Kuboard 中工作容器镜像版本是否需要同步调整。

## 五、触发与发布边界

两类构建的边界如下：

| 构建类型 | commit 标记 | 构建文件 | 镜像 tag | 常见负责人 | 常见场景 |
| --- | --- | --- | --- | --- | --- |
| 代码镜像 | `[ci build]` | `Dockerfile` | `<branch>-<project>:<timestamp>` | 开发 | 业务代码、接口逻辑、项目文档随代码发布 |
| 运行环境镜像 | `[build:os]` | `Dockerfile_OS` | `<project>:OS-<timestamp>` | 运维或开发与运维协同 | Python 版本、系统依赖、基础依赖变化 |

开发日常发布代码时，只应使用：

```bash
git commit -m "[ci build] release code changes"
git push origin test
```

运行环境变化时，才使用：

```bash
git commit -m "[build:os] update runtime dependencies"
git push origin test
```

如果一次提交同时包含业务代码和运行环境变化，应先确认发布顺序。通常先构建并验证运行环境镜像，再发布代码镜像，避免代码镜像已经更新但工作容器运行环境不匹配。

## 六、检查清单

提交前检查：

- `.gitlab-ci.yml` 是否只包含必要的分支、commit message 和镜像构建规则。
- `Dockerfile` 是否只承担代码镜像职责。
- `Dockerfile_OS` 是否只承担运行环境镜像职责。
- 真实 token、密码、API Key 是否没有写入仓库。
- 镜像仓库地址、分支名、项目名和 Kuboard 容器名称是否一致。
- 本次 commit message 是否包含正确构建标记。

构建后检查：

- GitLab CI pipeline 是否成功。
- 通知中是否输出了预期镜像地址。
- Kuboard 中调整的是正确容器的镜像版本。
- 服务启动后健康检查是否通过。
- 日志中是否存在依赖缺失、入口文件缺失或权限错误。

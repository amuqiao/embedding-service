# K8s 部署使用与维护扫盲

本文用于把 `chapters-short-lang-detector` 已部署到 K8s 后的运行状态、使用方式、维护动作和截图里的两个容器说明清楚。

本文不替代 K8s 官方手册，也不负责完整讲解集群、网络、存储和云厂商控制台。它只解释这个服务在 K8s 里应该怎么看、怎么调用、怎么发布、怎么排障。

## 先建立整体模型

这个项目本质上仍然是一个单一 FastAPI API 服务。K8s 没有改变业务形态，只是把原来“手动运行一个容器”的方式，变成由集群持续托管一个或多个 Pod。

```text
调用方
  |
  v
Service / Ingress / NodePort
  |
  v
Deployment
  |
  v
ReplicaSet
  |
  v
Pod
  |
  +-- init container: 启动前一次性准备，完成后退出
  +-- app container: 真正提供 FastAPI 接口，持续运行
```

理解这条链路后，截图里的对象就不再是孤立名词：

| 对象 | 在本服务里的作用 |
| --- | --- |
| `Deployment` | 声明“这个服务应该怎样运行”，例如镜像、环境变量、副本数、更新策略 |
| `ReplicaSet` | Deployment 自动创建的副本控制器，用来维持指定数量的 Pod |
| `Pod` | K8s 最小运行单元，里面装着一个或多个容器 |
| `容器` | 具体运行镜像的进程单元；本服务真正对外提供 API 的是业务容器 |
| `Service` | 给 Pod 提供稳定访问入口，避免直接访问会变化的 Pod IP |
| `Ingress` 或 `NodePort` | 把集群内服务暴露给集群外调用方 |

## 这张截图说明了什么

截图来自 Kuboard 的 Deployment 运行时页面，核心结论是：服务已经被 K8s 接管，并且当前至少有一个 Pod 正在正常运行。

从截图可读到的信息：

| 项目 | 当前状态 |
| --- | --- |
| 集群 | `fyhd-kube-us-test` |
| namespace | `test-ai-service` |
| Deployment | `test-chapters-short-lang-detector` |
| 当前副本 | `1 / 1` |
| 当前 ReplicaSet | `test-chapters-short-lang-detector-68586c676` |
| Pod | `test-chapters-short-lang-detector-68586c676-rtj4g` |
| Pod 状态 | `Running` |
| Pod IP | `10.244.145.240` |
| 所在节点 | `k8s-node3.epubgame.com` |
| 节点 IP | `172.20.48.172` |
| 业务容器 | `os-chapters-short-lang-detector`，状态 `running` |
| 初始化容器 | `test-chapters-short-lang-detector`，状态 `terminated` |

这里的 `terminated` 不一定代表故障。因为它出现在“初始化容器”区域，init container 的正常生命周期就是“先运行、完成、退出”。只要主业务容器是 `running`，Pod 是 `Running`，并且健康检查通过，这种状态通常是正常的。

## 为什么会有 2 个容器

截图里的 2 个容器不是两个独立服务，也不是服务启动了两份。它们属于同一个 Pod，但职责不同。

### 初始化容器

截图中的初始化容器名为：

```text
test-chapters-short-lang-detector
```

它的状态是：

```text
terminated
```

init container 的特点是：

- 在业务容器启动前执行。
- 可以用于初始化文件、等待依赖、准备目录、拉取或转换运行所需资源。
- 必须成功结束后，业务容器才会启动。
- 成功完成后会显示为已终止或完成状态，这是预期行为。

如果 init container 失败，常见现象是业务容器一直起不来，Pod 卡在 `Init`、`Init:Error`、`Init:CrashLoopBackOff` 等状态。

### 业务容器

截图中的业务容器名为：

```text
os-chapters-short-lang-detector
```

它的状态是：

```text
running
```

这个容器才是真正运行 FastAPI 服务的容器。它应该持续运行，并监听应用端口。

根据仓库里的 `Dockerfile`，应用容器默认执行：

```bash
uv run python main.py
```

容器内默认配置为：

```text
APP_HOST=0.0.0.0
APP_PORT=8000
```

所以从 K8s 角度看，最终要确认的是：Service 是否把流量转发到了业务容器的 `8000` 端口。

## 如何使用这个服务

### 访问入口

调用方不要直接访问 Pod IP。Pod 重建后 IP 会变化，直接访问 Pod IP 不适合作为稳定接口地址。

应优先使用以下入口之一：

| 入口类型 | 适用场景 |
| --- | --- |
| `Ingress` 域名 | 对外提供正式 HTTP 入口 |
| `NodePort` | 临时或测试环境对外暴露端口 |
| `ClusterIP Service` | 只允许集群内部服务调用 |
| Kuboard 服务页面 | 查看当前 Service 暴露方式和端口映射 |

如果使用项目现有接口文档中的联调地址，调用方式如下：

```bash
curl -s http://47.119.149.179:13001/healthz
```

预期返回：

```json
{"status":"ok"}
```

如果 K8s 这次发布换了新的 Service、NodePort 或 Ingress 地址，应以 Kuboard 的“服务”或“应用路由”页面为准，然后把 `API.md` 里的服务地址同步更新。

### 业务接口调用

本服务不是自动猜测语种，而是判断文本是否符合调用方指定的语种。

```text
输入: text + expected_language
输出: is_match + confidence + reason
```

健康检查不需要认证，业务接口需要 `X-API-Key`。

```bash
curl -s -X POST http://47.119.149.179:13001/language-verifications/batch \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: test-key-001' \
  -d '[
    {"id":"1","text":"Cześć","expected_language":"pl-PL"},
    {"id":"2","text":"Привет","expected_language":"pl-PL"}
  ]'
```

预期返回数组，且顺序与请求数组一致：

```json
[
  {
    "id": "1",
    "is_match": true,
    "confidence": 0.98,
    "reason": "expected_language_strong_chars"
  },
  {
    "id": "2",
    "is_match": false,
    "confidence": 0.98,
    "reason": "script_conflict"
  }
]
```

## 日常维护看什么

日常维护不要只看“页面是否有绿色状态”，而要按链路确认。

### 1. 看 Deployment 副本数

在 Kuboard 的 Deployment 运行时页面，优先看：

```text
当前副本 / 期望副本
```

截图中是：

```text
1 / 1
```

这表示当前已有 1 个副本可用，符合期望。

如果显示 `0 / 1`、`1 / 2` 或长时间不收敛，说明 Pod 创建、调度、镜像拉取、启动或健康检查可能有问题。

### 2. 看 Pod 状态

正常状态通常应是：

```text
Running
```

如果看到以下状态，需要进一步查看事件和日志：

| 状态 | 常见含义 |
| --- | --- |
| `Pending` | Pod 还没调度成功，可能资源不足、节点选择不匹配、镜像拉取前置条件未满足 |
| `ImagePullBackOff` | 镜像拉取失败，常见于镜像地址、tag、权限或网络问题 |
| `CrashLoopBackOff` | 容器启动后反复崩溃，常见于配置错误、启动命令错误、依赖缺失 |
| `Init:Error` | 初始化容器失败，业务容器还没真正启动 |
| `Running` 但接口不通 | 可能是 Service 端口、Ingress、应用监听端口或健康检查路径配置问题 |

### 3. 看容器日志

业务问题优先看业务容器日志，不要只看 init container 日志。

在 Kuboard 中可以点业务容器的“追踪日志”或“下载日志”。

如果使用 `kubectl`，典型命令是：

```bash
kubectl -n test-ai-service logs deploy/test-chapters-short-lang-detector -c os-chapters-short-lang-detector --tail=200
```

看初始化容器日志：

```bash
kubectl -n test-ai-service logs pod/test-chapters-short-lang-detector-68586c676-rtj4g -c test-chapters-short-lang-detector
```

如果 Pod 已重启过，可以加 `--previous` 查看上一次崩溃前日志：

```bash
kubectl -n test-ai-service logs pod/test-chapters-short-lang-detector-68586c676-rtj4g -c os-chapters-short-lang-detector --previous
```

### 4. 看事件

日志说明应用内部发生了什么，事件说明 K8s 调度和生命周期发生了什么。

```bash
kubectl -n test-ai-service describe pod test-chapters-short-lang-detector-68586c676-rtj4g
```

重点看末尾 `Events`：

- 镜像是否拉取成功。
- init container 是否成功完成。
- readiness probe 或 liveness probe 是否失败。
- 是否因为资源不足、权限、节点异常而调度失败。

### 5. 看资源使用

截图中业务容器大约显示：

```text
CPU 1m / 内存 178.12 MiB
```

这表示当时 CPU 使用很低，内存约 178 MiB。维护时要关注趋势，而不是单个瞬间值。

如果内存持续上涨，可能需要检查是否存在缓存增长、模型加载重复、请求批量过大等问题。如果 CPU 长时间打满，可能需要增加副本、限制 batch 大小或优化检测逻辑。

## 发布和回滚怎么理解

K8s 发布通常不是进入容器改文件，而是替换 Deployment 使用的镜像 tag 或配置，然后让 Deployment 滚动更新 Pod。

推荐发布理解：

```text
代码变更
  -> 构建新镜像
  -> 推送镜像仓库
  -> 修改 Deployment 镜像 tag 或通过 CI/CD 发布
  -> 新 ReplicaSet 创建新 Pod
  -> 新 Pod Ready
  -> 旧 Pod 下线
```

截图左侧能看到历史版本，当前副本集是 `#2`，上一版副本集是 `#1`，并且上一版当前副本为 `0`。这就是 Deployment 滚动发布后留下的历史 ReplicaSet。

如果新版本异常，回滚的本质是让 Deployment 回到旧版本镜像或旧版本模板。Kuboard 页面上通常可以通过历史版本执行回滚；命令行则通常使用：

```bash
kubectl -n test-ai-service rollout undo deploy/test-chapters-short-lang-detector
```

回滚后仍要验证：

```bash
curl -s http://47.119.149.179:13001/healthz
```

并执行业务接口冒烟请求。

## 配置和密钥怎么维护

本服务关键配置包括：

| 配置项 | 含义 |
| --- | --- |
| `APP_HOST` | 容器内监听地址，K8s 中应为 `0.0.0.0` |
| `APP_PORT` | 容器内监听端口，默认 `8000` |
| `LOG_LEVEL` | 日志级别，生产建议 `info` |
| `VALID_API_KEYS` | 有效 API Key，多个值用英文逗号分隔 |
| `MAX_TEXT_LENGTH` | 单条 `text` 最大字符数 |

配置变更后要区分两类：

| 变更类型 | 是否需要重建镜像 | 是否需要重启 Pod |
| --- | --- | --- |
| Python 代码、依赖、`Dockerfile` | 需要 | 需要 |
| 环境变量、Secret、ConfigMap | 通常不需要 | 通常需要 |

API Key 不应写死在镜像里。正式环境应由 K8s Secret 或平台密钥配置注入到 `VALID_API_KEYS`。

## 常见故障定位路径

### 服务页面显示 Running，但接口不通

按这个顺序查：

1. 访问 `/healthz` 是否正常。
2. Service 是否指向正确的 Pod label。
3. Service 的 targetPort 是否是容器内的 `8000`。
4. Ingress 或 NodePort 是否指向正确 Service 端口。
5. 容器是否监听 `0.0.0.0`，而不是只监听 `127.0.0.1`。

本项目镜像内默认设置了：

```text
APP_HOST=0.0.0.0
APP_PORT=8000
```

如果 K8s 运行时覆盖了这两个变量，要重点检查覆盖值。

### 业务接口返回 401

说明服务可达，但认证失败。

检查：

- 请求是否带了 `X-API-Key`。
- Header 名是否拼写正确。
- K8s 环境变量或 Secret 中的 `VALID_API_KEYS` 是否包含当前 key。
- 多个 key 是否使用英文逗号分隔。

### 业务接口返回 422

说明请求格式或字段校验失败。

常见原因：

- 请求体不是数组。
- 缺少 `id`、`text` 或 `expected_language`。
- `text` 超过 `MAX_TEXT_LENGTH`。
- 传了接口不允许的额外字段。

### Pod 一直重启

先看业务容器上一轮日志：

```bash
kubectl -n test-ai-service logs pod/test-chapters-short-lang-detector-68586c676-rtj4g -c os-chapters-short-lang-detector --previous
```

再看事件：

```bash
kubectl -n test-ai-service describe pod test-chapters-short-lang-detector-68586c676-rtj4g
```

重点判断是应用启动失败、配置错误、镜像问题、探针失败，还是资源限制导致容器被杀。

## 最小维护清单

每次发布或调整配置后，至少完成以下检查：

1. Deployment 当前副本等于期望副本。
2. Pod 状态为 `Running`。
3. init container 已成功完成。
4. 业务容器为 `running`。
5. `/healthz` 返回 `{"status":"ok"}`。
6. batch 接口带 `X-API-Key` 后返回预期数组。
7. 业务容器日志没有持续异常。

## 边界提醒

K8s 只负责托管和调度容器，不会自动保证业务规则正确。这个服务是否“可用”要分两层看：

```text
平台可用: Pod Running、Service 可访问、健康检查通过
业务可用: batch 接口返回正确语种符合性判断
```

截图证明的是平台层已经基本跑起来；最终仍需要用 `/healthz` 和 `/language-verifications/batch` 做接口验证。

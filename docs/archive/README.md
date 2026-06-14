# 归档文档

本目录保存历史方案、阶段性讨论、旧接口稿和外部平台操作材料。归档文档不作为当前代码、接口、配置或部署方式的事实来源。

## 当前事实来源

| 主题 | 当前文档 |
|---|---|
| 服务架构和边界 | [`../架构/架构总览.md`](../架构/架构总览.md) |
| Job 当前实现 | [`../job-implementation-guide.md`](../job-implementation-guide.md) |
| 后端接口对接 | [`../接口层/小说本地化AI能力层_后端对接接口文档.md`](../接口层/小说本地化AI能力层_后端对接接口文档.md) |
| 本地开发和 compose 部署 | [`../部署与发布手册.md`](../部署与发布手册.md) |
| 生产准入和风险评审 | [`../架构/production-readiness-review.md`](../架构/production-readiness-review.md) |

## 归档分类

| 路径 | 类型 | 说明 |
|---|---|---|
| [`async-job-spec.md`](async-job-spec.md) | 通用规范 | 跨项目异步 Job 设计材料，本项目实际选择以 `job-implementation-guide.md` 为准 |
| [`job-env-vars-quick-reference.md`](job-env-vars-quick-reference.md) | 重复速查 | 与部署手册、生产评审存在重叠；只在提炼配置说明时参考 |
| [`MVP_生产配置规范.md`](MVP_生产配置规范.md) | 阶段性讨论 | 配置面收敛思路，不直接代表当前配置事实 |
| [`接口层/小说本地化AI能力层接口文档.md`](接口层/小说本地化AI能力层接口文档.md) | 旧接口稿 | 当前接口契约以后端对接主文档为准 |
| [`架构/配置项.md`](架构/配置项.md) | 配置盘点 | 当前配置事实以 `.env.example`、`Settings` 和部署手册为准 |
| [`deploy/`](deploy/) | 外部平台资料 | K8s、Kuboard、CI、测试环境发布等材料，不属于本仓库当前维护的 local / compose 部署主线 |
| [`localization_workflow_v2.html`](localization_workflow_v2.html) | 业务流程图 | 仅作历史流程参考，不作为接口或实现契约 |

## 维护规则

- 修当前行为时，不直接修改归档文档；应更新主线文档。
- 如果归档文档中仍有有效内容，先提炼到主线文档，再在归档说明中标记已吸收。
- 新增阶段性排查记录默认不进入主线文档；确需保留时放入本目录，并说明归档原因。

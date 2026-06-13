# 架构文档

本目录记录 `cms-novel-localize` 的稳定架构说明。当前入口是：

- [架构总览](架构总览.md)：服务定位、边界、API、Job 生命周期、异步执行、Callback、恢复机制、数据模型和扩展边界。
- [生产就绪性评审](production-readiness-review.md)：生产化问题清单、修复状态和上线前关注点。

## 阅读顺序

第一次理解服务时，先读 [架构总览](架构总览.md)。

需要评估上线风险或复核 P1/P2 修复状态时，再读 [生产就绪性评审](production-readiness-review.md)。

## 维护规则

- 架构文档只记录当前代码和稳定设计，不记录临时排查过程。
- API、Job 状态机、Prompt 契约、Callback、数据库字段或部署模式变化时，应同步更新 [架构总览](架构总览.md)。
- 生产评审类结论、风险清单和修复追踪放在 [生产就绪性评审](production-readiness-review.md)，不要混入总览文档。

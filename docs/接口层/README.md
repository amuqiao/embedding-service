# 接口层文档

本目录包含两份接口规范文档，用于不同场景。

## 文件说明

| 文档 | 用途 | 受众 | 内容特点 |
|---|---|---|---|
| **小说本地化AI能力层接口文档.md** | 🔧 内部架构参考 | AI 能力层团队 | 详细规范、完整错误码、限制规则、所有细节 |
| **小说本地化AI能力层_后端对接接口文档.md** | 🤝 **给后端用** | 业务后端团队 | 精简格式、关键字段突出、完整代码示例、标准流程 |

## 给后端团队的快速导航

👉 **后端开发者请使用** `小说本地化AI能力层_后端对接接口文档.md`

这份文档包含：
- 5 个 API 接口的简洁说明
- 标准调用流程（13 节）
- 完整的 Python 代码示例
- 常见错误和处理方式
- Callback 签名验证示例

配合 `../设计承诺/Work_note与优化建议的角色.md` 理解重跑流程。

## 核心接口

```
GET  /health                    # 健康检查
GET  /models                    # 模型列表
GET  /prompt-templates          # Prompt 模板
POST /jobs                      # 创建任务
GET  /jobs/{job_id}             # 查询任务
```

## 快速开始

**第一步**：查询可用模型和模板
```bash
curl -H "Authorization: Bearer <api_key>" http://api/models
curl -H "Authorization: Bearer <api_key>" http://api/prompt-templates
```

**第二步**：创建本地化任务
```bash
curl -X POST -H "Authorization: Bearer <api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "novel_localization.step1_localize",
    "model_id": "gpt-4o-mini",
    "input": {"type": "text", "content": "..."},
    "prompt": {"blocks": [...]},
    ...
  }' http://api/jobs
```

**第三步**：轮询任务状态
```bash
curl -H "Authorization: Bearer <api_key>" http://api/jobs/{job_id}
```

## 关键约定

### 请求体原则
- 业务后端必须传入**本次任务完整输入** → 不通过 project_id 查询原文
- 业务后端必须传入**完整 prompt.blocks** → AI 层不自动补齐
- 业务后端必须指定**输出位置** → 大文本写入 OSS

### 响应体约定
- 所有产物统一用 `artifacts[]` 表示 → key, type, content/storage
- 所有业务信号统一用 `signals{}` 表示 → passed, merge_scan_applied 等
- 所有错误统一用 `error{code, message, details}` 表示

## 版本管理

当前版本：**v1.0.0** (2026-06-08)

### API 稳定性承诺
✅ 5 个接口结构永不变更  
✅ request/response 数据结构可按需扩展（新字段），不删改现有字段  
✅ job_type 枚举可添加新值（step4, step5...），不删改现有值  
✅ 错误码可添加新值，不改现有语义  

详见：`../设计承诺/AI能力层API稳定性承诺.md`

## 常见问题

**Q: 是否支持 batch 请求？**  
A: 首版不支持。每个任务独立提交，可并发创建多个 Job。

**Q: 是否支持实时进度推送？**  
A: 首版不支持 WebSocket/SSE。使用轮询 `GET /jobs/{job_id}` 查询（推荐间隔 2-5 秒）。

**Q: 输入超过 1MB 怎么办？**  
A: 使用 `input.type=oss_object` 传递，最大支持 5MB。AI 层使用自身 OSS 凭证读取。

**Q: 如何处理 Callback 通知？**  
A: 实现 POST `/internal/ai-callbacks/novel-localization` 端点，验证 HMAC-SHA256 签名，实现幂等去重。

## 相关文档

- 📖 后端对接清单：`../后端对接/后端对接清单.md`
- 📋 Prompt 使用指南：`../设计承诺/Work_note与优化建议的角色.md`
- 🔧 内部实现透明说明：`../内部实现/内部实现选项指南.md`

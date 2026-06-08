# 小说本地化 AI 能力层文档导航

欢迎来到小说本地化 AI 能力层的文档库。本导航帮你快速找到需要的信息。

---

## 🎯 我是...

### 📊 产品经理 / 架构师
**需要理解整体能力和约束**

建议阅读顺序：
1. `接口层/README.md` — 了解 5 个接口和基本原则
2. `设计承诺/AI能力层API稳定性承诺.md` — 了解长期承诺和版本管理规则
3. `后端对接/后端对接协议.md` — 了解 SLA、职责、费用

**时间**：30 分钟

---

### 💻 后端开发者（首次对接）
**需要快速上手，实现 API 调用**

建议阅读顺序：
1. `接口层/README.md` — 快速了解 5 个接口
2. `设计承诺/Work_note与优化建议的角色.md` — 理解 work_note 的用法（含代码示例）
3. `后端对接/后端对接清单.md` — 按步骤实现，从初始化到部署前检查

**时间**：2-3 小时完成对接

**快速跳转**：
- 创建 step1 Job：`后端对接/后端对接清单.md` → 第二部分 2.1 节
- 轮询 Job 状态：`后端对接/后端对接清单.md` → 第二部分 2.2 节
- 处理 step2 失败重跑：`设计承诺/Work_note与优化建议的角色.md` → 快速参考
- Callback 签名验证：`后端对接/后端对接清单.md` → 第三部分 3.2 节

---

### 🔧 AI 能力层开发者
**需要理解内部实现和扩展方式**

建议阅读顺序：
1. `接口层/小说本地化AI能力层接口文档v3.md` — 了解 API 合同
2. `内部实现/内部实现选项指南.md` — 了解 P1/P5/扫描三种模式
3. `内部实现/扩展示例_新增Job类型.md` — 了解如何扩展 job_type

**时间**：1 小时

---

### 🚨 运维 / 技术支持
**需要快速排障和告警**

建议阅读顺序：
1. `接口层/README.md` → 常见问题
2. `后端对接/后端对接协议.md` → 第 6 节故障排查
3. 日志查询：
   ```bash
   # AI 能力层日志
   journalctl -u ai-novel-localization -f
   
   # 关键字
   grep "job_id\|error\|timeout" /var/log/ai-novel-localization/*.log
   ```

**时间**：15 分钟

---

## 📚 文档结构一览

```
docs/
├── README.md                              ← 你在这里
│
├── 接口层/                                 ← API 规范
│   ├── README.md                          快速引导
│   ├── 小说本地化AI能力层接口文档v3.md    详细接口规范（给架构师和后端）
│   └── 小说本地化AI能力层_后端对接接口文档.md  简化版本（给后端开发）
│
├── 设计承诺/                               ← 核心设计原则
│   ├── README.md                          快速引导
│   ├── AI能力层API稳定性承诺.md           API 长期承诺 + 内部实现灵活性
│   └── Work_note与优化建议的角色.md       prompt block 深度指南（含代码）
│
├── 内部实现/                               ← 架构和优化（对后端透明）
│   ├── README.md                          快速引导
│   ├── 内部实现选项指南.md                P1/P5/扫描模式、work_items、监控
│   └── 扩展示例_新增Job类型.md            新增 job_type 端到端示例
│
├── 后端对接/                               ← 实操指南
│   ├── README.md                          快速引导
│   ├── 后端对接清单.md                    6 部分的实操指南 + 代码示例
│   └── 后端对接协议.md                    SLA + 职责 + 费用（签署）
│
├── 独立服务抽取与流程说明.md               建立能力层的背景说明
└── localization_workflow_v2.html          业务流程图
```

---

## 🔍 按需求查找

### "我想..."

#### ...了解整个 API 是什么样的
→ `接口层/小说本地化AI能力层接口文档v3.md` 第 5-8 节

#### ...快速上手后端对接
→ `后端对接/README.md` 快速入门部分

#### ...理解 work_note 怎么用
→ `设计承诺/Work_note与优化建议的角色.md` 快速参考 + 代码示例

#### ...给后端团队培训
→ 按顺序展示：
1. `接口层/README.md` 的核心接口部分
2. `设计承诺/Work_note与优化建议的角色.md` 的迭代闭环图
3. `后端对接/后端对接清单.md` 的快速流程

#### ...排查某个 API 问题
→ `接口层/小说本地化AI能力层接口文档v3.md` 第 10 节错误码

#### ...添加新的 job_type
→ `内部实现/扩展示例_新增Job类型.md`

#### ...了解内部是怎么分块的
→ `内部实现/内部实现选项指南.md` 模式 2 部分

#### ...签署对接协议
→ `后端对接/后端对接协议.md` 第 8 节签署

---

## ✅ 文档检查清单

### 从后端视角
- ✅ 接口是否清晰可理解？
- ✅ work_note 的用法是否有具体代码示例？
- ✅ 错误处理是否覆盖所有场景？
- ✅ Callback 验证的代码是否可复用？
- ✅ 部署前检查清单是否完整？

### 从架构视角
- ✅ API 稳定性承诺是否明确？
- ✅ 内部实现的灵活性是否有保障？
- ✅ 扩展方式是否文档化？
- ✅ 监控和告警指标是否完整？

### 从运维视角
- ✅ 故障排查指南是否清晰？
- ✅ 日志记录规范是否明确？
- ✅ SLA 指标是否可量化？

---

## 📞 文档维护

### 发现问题？
- 接口规范有误：→ `接口层/` 下的文件
- 代码示例不工作：→ `后端对接/` 下的清单
- 概念解释不清：→ 对应 README.md 或设计承诺文档

### 更新文档时
- 同步更新相关 README.md
- 更新根 README 的文档结构
- 如新增目录，补充此导航

---

## 📈 推荐阅读路径

### 第一天：快速了解
```
接口层/README.md (15 min)
  ↓
设计承诺/Work_note与优化建议的角色.md 快速参考 (10 min)
  ↓
后端对接/README.md 快速入门 (10 min)
```

### 第二天：深入实现
```
后端对接/后端对接清单.md 第一部分（准备） (30 min)
  ↓
后端对接/后端对接清单.md 第二部分（核心流程） (2 hours)
  ↓
后端对接/后端对接清单.md 第三-五部分（错误、监控、部署） (1 hour)
```

### 第三天：部署和对接
```
后端对接/后端对接清单.md 第五部分（部署检查）
  ↓
后端对接/后端对接协议.md （签署）
```

---

## 🎁 快速参考

### 创建 Job 的最小请求
```json
POST /jobs
{
  "job_type": "novel_localization.step1_localize",
  "model_id": "gpt-4o-mini",
  "input": {"type": "text", "content": "..."},
  "prompt": {
    "blocks": [
      {"key": "system", "role": "system", "content": "..."},
      {"key": "user", "role": "user", "content": "..."},
      {"key": "work_note", "role": "user", "content": ""}
    ]
  },
  "output": {"type": "oss_prefix", ...},
  "callback": {"url": "..."}
}
```

### 重跑流程（三行代码）
```python
opt = next(a for a in step2['artifacts'] if a['key'] == 'optimization_prompt')
step1_retry = post_job('step1_localize', work_note=opt['content'], ...)
```

### Callback 签名验证
```python
# 签名原文：timestamp + "." + request_body
# 算法：HMAC-SHA256
expected = "sha256=" + hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
assert hmac.compare_digest(signature, expected)
```

---

## 💡 核心概念速览

### API 层（永久稳定）
- 5 个接口永远不变
- request/response 结构永远不变
- job_type 枚举可扩展

### 内部实现（完全灵活）
- P1（单步）vs P5（分块）自动选择
- work_items 是内部细节，后端不需要知道
- 分块数、并行度可自由优化

### Work_note 双重角色
- 角色 1：后端输入（空或业务说明）
- 角色 2：AI 建议的注入点（`optimization_prompt.content`）

---

**最后更新**：2026-06-08  
**版本**：1.0.0  
**维护者**：AI 能力层团队

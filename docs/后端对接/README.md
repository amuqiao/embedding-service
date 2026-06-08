# 后端对接文档

本目录是业务后端接入小说本地化 AI 能力层的实操指南。包含详细的实现清单、代码示例、协议承诺。

## 文件清单

| 文档 | 用途 | 受众 |
|---|---|---|
| **后端对接清单.md** | 分 6 个部分的实操指南：准备、6 大核心流程、错误处理、监控、部署前检查。含完整代码示例 | 后端开发者 |
| **后端对接协议.md** | SLA 承诺、职责边界、费用说明、故障排查、未来演进。适合双方签署 | 后端负责人、架构师 |

---

## 快速入门

### 👋 初次对接？从这里开始

**第一步**（5 分钟）：了解全貌
```bash
# 读接口文档的第 1-6 节
cat ../接口层/小说本地化AI能力层接口文档v3.md | head -300

# 看 work_note 的快速参考
cat ../设计承诺/Work_note与优化建议的角色.md | sed -n '5,45p'
```

**第二步**（30 分钟）：按对接清单实现

1. 准备阶段（第一部分）
   - [ ] 获取 API 密钥和地址
   - [ ] 获取 Callback 签名密钥
   - [ ] 初始化模型和模板

2. 核心流程（第二部分 2.1-2.6）
   - [ ] 创建 step1 本地化任务
   - [ ] 轮询任务状态
   - [ ] 读取 step1 结果
   - [ ] 创建 step2 校验任务
   - [ ] 处理 step2 失败的重跑
   - [ ] 创建 step3 翻译任务

3. 错误和监控（第三部分）
   - [ ] 实现 22 个错误码处理
   - [ ] 实现 Callback 签名验证
   - [ ] 添加关键指标采集

**第三步**（2 小时）：部署前检查

- [ ] 单元测试（请求体、签名验证、轮询逻辑）
- [ ] 集成测试（step1 → step2 → step3 完整流程）
- [ ] 部署检查（环境变量、权限、日志）

---

## 核心实现概览

### 三步调用流程

```python
# Step 1: 本地化
step1_job = post_job('novel_localization.step1_localize', {
    'input': {'type': 'text', 'content': original_text},
    'prompt': {
        'blocks': [
            {'key': 'system', 'role': 'system', 'content': ...},
            {'key': 'user', 'role': 'user', 'content': ...},
            {'key': 'work_note', 'role': 'user', 'content': ''}
        ]
    }
})
localized = poll_until_complete(step1_job)

# Step 2: 校验
step2_job = post_job('novel_localization.step2_review', {
    'input': {'type': 'text', 'content': localized},
    'prompt': {...}
})
review = poll_until_complete(step2_job)

# 根据校验结果决策
if review['signals']['passed']:
    # Step 3: 翻译
    step3_job = post_job('novel_localization.step3_translate', {
        'input': {'type': 'text', 'content': localized},
        'prompt': {...}
    })
    translated = poll_until_complete(step3_job)
else:
    # 失败重跑：提取建议，注入 work_note
    opt = next(a for a in review['artifacts'] if a['key'] == 'optimization_prompt')
    step1_retry = post_job('novel_localization.step1_localize', {
        'input': {'type': 'text', 'content': original_text},
        'prompt': {
            'blocks': [
                ...,
                {'key': 'work_note', 'role': 'user', 'content': opt['content']}
            ]
        }
    })
    # 回到 Step 2 重新校验
```

### 关键代码片段

#### 创建 Job
```python
def submit_job(job_type, prompt_config, input_text):
    response = requests.post(
        f"{AI_SERVICE_HOST}/api/v1/novel-localization-ai/jobs",
        json={
            'job_type': job_type,
            'model_id': 'gpt-4o-mini',
            'input': {'type': 'text', 'content': input_text},
            'prompt': {'blocks': prompt_config},
            'output': {...},
            'callback': {...},
            'metadata': {'external_project_ref': project_id}
        },
        headers={'Authorization': f'Bearer {API_KEY}'}
    )
    return response.json()['job_id']
```

#### 轮询状态
```python
def poll_until_complete(job_id, timeout=1800):
    poll_interval = 2
    start = time.time()
    
    while time.time() - start < timeout:
        result = requests.get(
            f"{AI_SERVICE_HOST}/api/v1/novel-localization-ai/jobs/{job_id}",
            headers={'Authorization': f'Bearer {API_KEY}'}
        ).json()
        
        if result['status'] in ['succeeded', 'failed']:
            return result
        
        time.sleep(poll_interval)
    
    raise TimeoutError(f"Job {job_id} timeout")
```

#### 验证 Callback 签名
```python
def verify_callback(request_headers, request_body, secret):
    import hmac, hashlib
    
    timestamp = request_headers['X-AI-Service-Timestamp']
    signature = request_headers['X-AI-Service-Signature']
    
    # 验证 timestamp（防重放）
    if abs((datetime.utcnow() - datetime.fromisoformat(timestamp.replace('Z', '+00:00'))).total_seconds()) > 300:
        return False
    
    # 验证签名
    expected = "sha256=" + hmac.new(
        secret.encode(),
        f"{timestamp}.{request_body}".encode(),
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(signature, expected)
```

---

## 常见问题排查

### API 连接问题

```bash
# 1. 检查服务健康
curl -H "Authorization: Bearer $API_KEY" http://$AI_HOST/health

# 2. 检查 API 密钥
# 错误：401 INVALID_API_KEY → 确认密钥格式 "Bearer <token>"

# 3. 检查网络连接
ping $AI_HOST
curl -v http://$AI_HOST/health
```

### Job 执行问题

| 问题 | 原因 | 解决 |
|---|---|---|
| 202 Created 后立即查询返回 404 | 网络延迟或任务未创建 | 等待 1 秒后重试 |
| JOB_TIMEOUT | 任务执行超过 30 分钟 | 检查输入大小，考虑分割 |
| INPUT_TOO_LARGE | 输入超过 1MB | 改用 `input.type=oss_object` |
| MODEL_CALL_FAILED | 模型调用失败 | 重试（最多 3 次），或查看模型状态 |

### Callback 问题

| 问题 | 原因 | 解决 |
|---|---|---|
| 收不到 callback | 后端 URL 不可访问 | 检查防火墙、DNS、路由 |
| 签名验证失败 | 签名密钥不匹配或时间差 | 确认密钥一致，检查系统时间 |
| 重复收到 callback | 未做去重 | 实现 `job_id + event` 的幂等去重 |

---

## 检查清单

### 初始化阶段
- [ ] API 地址和密钥已配置
- [ ] `/health` 可访问
- [ ] `/models` 返回至少 1 个模型
- [ ] `/prompt-templates` 返回 3 个 job_type

### 功能实现
- [ ] step1 Job 可创建
- [ ] 轮询逻辑工作正常
- [ ] 能读取 OSS 中的 `localized_text`
- [ ] step2 Job 可创建
- [ ] 能识别 `signals.passed` 和 `optimization_prompt`
- [ ] 能重跑 step1（注入建议）
- [ ] step3 Job 可创建

### Callback 实现
- [ ] 实现接收端点 `/internal/ai-callbacks/novel-localization`
- [ ] 验证签名（HMAC-SHA256）
- [ ] 验证 timestamp（防重放）
- [ ] 实现去重（`job_id + event`）

### 监控和告警
- [ ] 日志包含 job_id、job_type、status
- [ ] 采集成功率和执行时间指标
- [ ] 设置告警规则（成功率 < 95%）

---

## 参考文档

| 文档 | 内容 |
|---|---|
| `../接口层/小说本地化AI能力层接口文档v3.md` | API 详细规范 |
| `../设计承诺/Work_note与优化建议的角色.md` | work_note 使用指南 |
| `后端对接清单.md` | 分步骤的实操指南（本目录） |
| `后端对接协议.md` | SLA 承诺和署名（本目录） |

---

## 支持联系

- 📧 邮件：ai-team@example.com（工作时间 48 小时内回复）
- 💬 钉钉：@AI 能力层团队（工作时间 2 小时内回复）
- 🔗 内部 wiki：问题排查指南

---

## 下一步

✅ 已准备好对接？  
→ 打开 `后端对接清单.md`，从第一部分开始

❓ 有疑问？  
→ 查看 `../设计承诺/` 了解设计原理

🚀 准备上线？  
→ 签署 `后端对接协议.md`，开启对接会议

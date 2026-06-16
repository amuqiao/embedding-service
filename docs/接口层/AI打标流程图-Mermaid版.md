# AI 打标流程图 - Mermaid 版

本文用 Mermaid 表达 CPP、AI、RS 的短剧 AI 打标主流程。文字说明以 [AI打标任务流程.md](AI打标任务流程.md) 为准。

```mermaid
flowchart TD
  CPP_READY["CPP：素材准备完成"]
  CREATE["CPP -> AI：创建打标 Job\n携带素材资源 + callback.url"]
  JOB["AI：创建 queued job\n返回 job_id"]

  RS_SCHEMA["AI -> RS：获取默认标签体系响应\ncategories + mutual_exclusion_rules"]

  RUN["AI：剧情理解 + 标签判断\n输入 = CPP 素材 + RS 标签结构体 + RS 互斥结构体"]
  RESULT{"AI 结果"}
  PERSIST["AI：持久化 canonical result\nfinal_tags 使用 label_id"]

  WRITE_RS["AI -> RS：写入 ai_auto 打标结果\n独立发送，payload 来自 canonical result"]
  CALLBACK_OK["AI -> CPP callback\n发送成功 JobView"]
  CALLBACK_FAIL["AI -> CPP callback\n发送失败 JobView"]
  RS_FAIL["AI：RS 写入失败\njob.failed + callback CPP"]

  CPP_READY --> CREATE --> JOB
  JOB --> RS_SCHEMA --> RUN --> RESULT
  RESULT -->|"成功"| PERSIST --> WRITE_RS
  WRITE_RS -->|"RS 接受写入"| CALLBACK_OK
  WRITE_RS -->|"RS 写入失败"| RS_FAIL
  RESULT -->|"模型 / 素材 / 标签校验失败"| CALLBACK_FAIL
```

## 关键约束

- CPP 不传标签结构体、互斥结构体或 `tag_schema_version`。
- RS 提供默认标签数据。
- 每个标签必须有全局唯一 `label_id`。
- 互斥规则必须使用 `label_id` / `mutex_label_ids`。
- AI 写 RS payload 来自同一份内部 canonical result；CPP callback 只发送终态 JobView。

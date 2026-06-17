# RS ⇄ AI 打标对接接口定义（RS 定稿）

> 原 AI 侧接口文档（标签事实 / 打标写入）作废。接口定义由 RS 定稿，以本文为准。AI 仅提供 JSON 结构示例，字段补充与存储语义由 RS 定义。
>
>

## 一、通用约定

- **label\_id**：标签全局唯一 id，取 RS `ai_tag._id`（hex 字符串），不可变；是标签引用、互斥规则、打标结果回写的唯一权威字段。

- **category\_id**：分类 id，6 位补零字符串（如 `000001`），取 RS `ai_tag_category.category_id`。

- **情绪** 分类与其他分类结构完全一致，按普通标签处理；无 `output_type`，无情绪序列（虐/紧张/爽 各为独立标签项）。

- 名称统一用标签名；AI 基于 cpp 传入语言对应的标签体系打标。

- 业务字段统一英文 key：`name` / `weight` / `reason` / `definition`。

- **鉴权**：服务间鉴权方式与 URL 前缀待双方对齐。

- **请求域名：测试：****https://test\-v\-adm\-api\.stardustworld\.cn/****  正式：****https://v\-adm\-api\.stardustgod\.com/**

## 二、接口 B：拉取标签体系（RS 提供，AI 调）

AI 打标前拉取当前**启用**的标签体系与互斥规则。

**请求**：`GET /api/v1/tag-schemas/default?lang=zh`（URL 前缀 / 鉴权待对齐）。入参 `lang`：三方业务语种合约代码，单选必需，取 `业务语种规范.md` / `language-codes.md` 22 种之一（如 `zh`/`en`/`es`/`pt`/`in`）。AI 按 CPP 传入语言拉对应语言标签体系，标签 `name` / `definition` 按该语言返回；非法或缺省语言应在调用侧校验失败，不在 AI 侧静默回退。分类 `name` 暂无多语言固定人工填入内容。

**响应**：

```json
{
  "version": "v1.1",
  "generated_at": 1700000000,
  "categories": [
    {
      "category_id": "000001",
      "name": "受众",
      "required": true,
      "min_items": 1,
      "max_items": 1,
      "labels": [
        { "label_id": "65f0a1b2c3d4e5f6a7b8c901", "name": "男频", "definition": "核心受众为男性群体……" },
        { "label_id": "65f0a1b2c3d4e5f6a7b8c902", "name": "女频", "definition": "核心受众为女性群体……" }
      ]
    }
  ],
  "mutual_exclusion_rules": [
    { "label_id": "65f0a1b2c3d4e5f6a7b8c9f1", "mutex_label_ids": ["65f0a1b2c3d4e5f6a7b8c9f2", "65f0a1b2c3d4e5f6a7b8c9f3"] }
  ]
}
```

**字段说明**

|字段|层级|必需|说明|
|---|---|---|---|
|version|顶层|否|标签体系版本标识（展示用）|
|generated\_at|顶层|是|生成时间戳（秒）|
|category\_id|分类|是|6 位分类 id|
|name|分类/标签|是|名称|
|required|分类|是|该分类是否必打|
|min\_items|分类|是|最少打几个（默认 0）|
|max\_items|分类|是|最多打几个（null = 不限）|
|label\_id|标签|是|标签全局唯一 id（= ai\_tag `_id`）|
|definition|标签|是|标签定义|
|mutual\_exclusion\_rules\[\]\.label\_id|互斥|是|主标签 id|
|mutual\_exclusion\_rules\[\]\.mutex\_label\_ids|互斥|是|与主标签互斥的标签 id 列表|

> 互斥规则独立于 categories，放顶层 `mutual_exclusion_rules[]`，与 RS `ai_tag_mutex`（一标签一行）直接对应。
>
>

## 三、接口 C：写打标结果（RS 提供，AI 调）

AI 打标完成后写入 `source=ai_auto` 的结果。

**请求**：`POST /api/v1/ai-tag-results`（URL 前缀 / 鉴权待对齐）

> `tags` 的 key 为 `category_id`（6 位补零字符串），value 为该分类下的标签数组。
>
>

```json
{
  "t_book_id": "300000000300000279",
  "job_id": "0a9be3fb-f01b-4f5d-90b5-4148c4a61df1",
  "tags": {
    "000001": [
      { "label_id": "65f0a1b2c3d4e5f6a7b8c902", "name": "女频", "weight": 1, "reason": "剧情以女主视角展开。", "definition": "核心受众为女性群体。" }
    ],
    "000006": [
      { "label_id": "65f0a1b2c3d4e5f6a7b8ca01", "name": "虐", "weight": 0.9, "reason": "女主遭受冤屈羞辱。", "definition": "刻意营造悲伤、压抑的情绪。" },
      { "label_id": "65f0a1b2c3d4e5f6a7b8ca02", "name": "爽", "weight": 0.8, "reason": "最终获证据平反并反击成功。", "definition": "畅快、解气的愉悦感。" }
    ]
  }
}
```

**字段说明**

|字段|层级|必需|说明|
|---|---|---|---|
|t\_book\_id|顶层|是|作品对接键|
|job\_id|顶层|是|AI 任务 id，RS 幂等键|
|tags|顶层|是|打标结果，category\_id 为 key|
|tags\.\{category\_id\}\[\]\.label\_id|标签|是|标签 id（= ai\_tag `_id`）|
|tags\.\{category\_id\}\[\]\.name|标签|是|标签名（展示/排查用）|
|tags\.\{category\_id\}\[\]\.weight|标签|否|置信/强度，取值 0 \< weight ≤ 1|
|tags\.\{category\_id\}\[\]\.reason|标签|是|打标原因/依据|
|tags\.\{category\_id\}\[\]\.definition|标签|是|打标时的标签定义快照|

**幂等与存储（RS 侧）**

- 来源固定 `source=ai_auto`。

- 按 `job_id` 幂等：首次新增一条 `ai_tag_result` 流水（原样存 `tags`）并覆盖 `ai_work_tag` 的 ai\_auto 标签；重复 `job_id` 直接返回已有，不重复处理。

- AI 写 RS 不传 `tag_schema_version`；RS 按 `job_id` 和本次 `tags` payload 处理。

- 无 `result_checksum`、无 initial/incremental 区分：每次按全量覆盖 ai\_auto 处理，人工（manual）标签保留。

- 同 ipid 其他语言作品联动更新 ai\_auto。

- 响应：RS 内部标准 `{code, msg, data}`。

**错误情形**

|情形|处理|
|---|---|
|作品（t\_book\_id）不存在|失败返回，提示 work not found|
|tags 为空 / 结构非法|失败返回，提示参数错误|
|重复 job\_id|返回已有结果，不重复写入|

> (注：内容由 AI 生成，请谨慎参考）

# 接口 JSON 示例

本目录只保存接口结构体示例，字段语义、必需性和错误处理以接口文档为准。

示例文件用于联调、mock 和人工检查，不作为接口契约源头。

## 文件说明

| 文件 | 说明 |
| --- | --- |
| `tag_schema_snapshot.example.json` | RS 返回给 AI 的默认标签体系响应示例，包含 `categories` 和 `mutual_exclusion_rules`。 |
| `mutual_exclusion_rules.example.json` | 独立 `MutualExclusionRule[]` 结构示例，用于需要单独传递互斥规则的接口场景。 |
| `final_tags.example.json` | AI 写入 RS 的 `ai_auto` 打标结果结构示例。 |

## 使用约定

- 打标对接以 `RS ⇄ AI 打标对接接口定义（RS 定稿）.md` 为准。
- `category_id` 是分类唯一引用字段，作为 `tags` 对象的 key。
- `label_id` 是标签唯一引用字段，互斥规则和打标结果都必须使用它引用标签。
- 标签名和定义只用于展示、排查和快照，不作为规则引用或结果写回的唯一依据。

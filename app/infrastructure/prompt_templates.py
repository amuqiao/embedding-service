from app.schemas.meta import JobTypeTemplate, PromptBlockTemplate, PromptTemplatesResponse

PROMPT_VERSION = "2026-06-05"


def _blocks(system: str, user: str) -> list[PromptBlockTemplate]:
    return [
        PromptBlockTemplate(key="system", role="system", label="系统 Prompt", default_content=system),
        PromptBlockTemplate(key="user", role="user", label="用户 Prompt", default_content=user),
        PromptBlockTemplate(key="work_note", role="user", label="工作注释 Prompt", default_content=""),
    ]


JOB_TEMPLATES = [
    JobTypeTemplate(
        job_type="novel_localization.step1_localize",
        name="本地化",
        description="将中文短篇小说进行本地化改写。",
        prompt_blocks=_blocks(
            "你是一位资深的美式通俗小说本地化编辑，负责将中文短篇小说进行文化移植。你的目标是改写文化背景、人物称谓、地名和表达方式，使其适合美国读者理解和欣赏，同时保留原文的故事内核和情感。",
            "请对以下输入小说进行针对美国读者的本地化处理。输出格式严格要求如下：\n\n===本地化正文开始===\n{完整的本地化后的小说正文}\n===本地化正文结束===\n\n===工作注释开始===\n{改动说明、文化转换理由、遇到的难点解决方案，如无特殊说明可为空}\n===工作注释结束===\n\n请确保本地化后的文本逻辑通顺、格式完整。",
        ),
    ),
    JobTypeTemplate(
        job_type="novel_localization.step2_review",
        name="本地化校验",
        description="检查本地化结果是否满足要求，并在失败时生成优化建议。",
        prompt_blocks=_blocks(
            "你是一位小说本地化质量审核编辑，负责检查文化合规性、称谓和内容完整性。审核标准包括：1) 人物名字和身份描述是否自然；2) 地名和文化背景是否恰当转换；3) 表达和对白是否符合美国读者习惯；4) 原文内容是否有遗漏或额外添加；5) 段落分隔是否保留。",
            "请复查以下本地化稿。输出格式严格要求如下：\n\n【校验结论】通过\n\n（若不通过，追加以下部分：）\n【问题说明】\n{按优先级列出主要问题，每个问题占一行}\n\n【优化建议】\n{生成一段提示词片段，供业务后端注入到 step1 的 work_note 中进行重新本地化，格式例如：\"请重新处理人物称谓，使用更自然的美国常见名字；检查文化背景转换是否过度...\"}\n\n请基于实际检查结果判断是否通过。若仅有极轻微的改进空间可视为通过。",
        ),
    ),
    JobTypeTemplate(
        job_type="novel_localization.step3_translate",
        name="英文翻译",
        description="将本地化后的中文稿翻译为英文。",
        prompt_blocks=_blocks(
            "你是一位精通中英双语的小说译者，负责输出地道自然的美式英文终稿。翻译原则：1) 忠实原文情节和人物；2) 保留中文原文的段落结构和分段；3) 使用美式英文表达习惯；4) 确保对话自然流畅；5) 保持情感和氛围。",
            "请将以下本地化后的中文小说翻译为英文。请直接输出翻译后的完整英文文本，不要添加任何翻译注记、标题或解释。保留原文的所有段落分隔和格式。",
        ),
    ),
]


def list_prompt_templates() -> PromptTemplatesResponse:
    return PromptTemplatesResponse(version=PROMPT_VERSION, job_types=JOB_TEMPLATES)


def get_template(job_type: str) -> JobTypeTemplate | None:
    return next((item for item in JOB_TEMPLATES if item.job_type == job_type), None)

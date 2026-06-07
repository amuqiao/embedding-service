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
            "你是一位资深的美式通俗小说本地化编辑，负责将中文短篇小说进行文化移植。",
            "请对输入小说进行针对美国读者的本地化处理，并输出本地化正文与工作注释。",
        ),
    ),
    JobTypeTemplate(
        job_type="novel_localization.step2_review",
        name="本地化校验",
        description="检查本地化结果是否满足要求，并在失败时生成优化建议。",
        prompt_blocks=_blocks(
            "你是一位小说本地化质量审核编辑，负责检查文化合规性、称谓和内容完整性。",
            "请复查本地化稿；通过则说明已满足，不通过则给出问题总结和优化建议 Prompt。",
        ),
    ),
    JobTypeTemplate(
        job_type="novel_localization.step3_translate",
        name="英文翻译",
        description="将本地化后的中文稿翻译为英文。",
        prompt_blocks=_blocks(
            "你是一位精通中英双语的小说译者，负责输出地道自然的美式英文终稿。",
            "请将本地化后的中文小说翻译为英文，保留原分段与章节结构。",
        ),
    ),
]


def list_prompt_templates() -> PromptTemplatesResponse:
    return PromptTemplatesResponse(version=PROMPT_VERSION, job_types=JOB_TEMPLATES)


def get_template(job_type: str) -> JobTypeTemplate | None:
    return next((item for item in JOB_TEMPLATES if item.job_type == job_type), None)

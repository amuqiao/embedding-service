from app.workflows.novel_localization.handler import register_all as _register_novel_localization


def register_all_workflows() -> None:
    _register_novel_localization()

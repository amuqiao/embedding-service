from app.workflows.generic.handler import register_all as _register_generic
from app.workflows.novel_localization.handler import register_all as _register_novel_localization
from app.workflows.short_drama_tagging.handler import register_all as _register_short_drama_tagging


def register_all_workflows() -> None:
    _register_generic()
    _register_novel_localization()
    _register_short_drama_tagging()

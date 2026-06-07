from app.services.job_planner import build_job_plan


def test_job_planner_uses_p1_for_short_text():
    plan = build_job_plan("novel_localization.step1_localize", "短文本")
    assert plan.execution_mode == "p1"
    assert [item.kind for item in plan.work_items] == ["whole"]


def test_job_planner_uses_p5_for_long_step1_text(monkeypatch):
    monkeypatch.setattr("app.infrastructure.config.settings.NOVEL_LOCALIZATION_P1_MAX_CHARS", 10)
    monkeypatch.setattr("app.infrastructure.config.settings.NOVEL_LOCALIZATION_CHUNK_SIZE", 8)

    plan = build_job_plan("novel_localization.step1_localize", "第一段很长很长\n\n第二段也很长很长")

    assert plan.execution_mode == "p5"
    assert plan.chunk_count >= 2
    assert "memory" in [item.kind for item in plan.work_items]
    assert "merge" in [item.kind for item in plan.work_items]


def test_job_planner_uses_p5_for_long_step3_with_scan(monkeypatch):
    monkeypatch.setattr("app.infrastructure.config.settings.NOVEL_LOCALIZATION_P1_MAX_CHARS", 10)
    monkeypatch.setattr("app.infrastructure.config.settings.NOVEL_LOCALIZATION_CHUNK_SIZE", 8)

    plan = build_job_plan("novel_localization.step3_translate", "第一段很长很长\n\n第二段也很长很长")

    assert plan.execution_mode == "p5"
    assert "memory" not in [item.kind for item in plan.work_items]
    assert "merge" in [item.kind for item in plan.work_items]
    assert "scan" in [item.kind for item in plan.work_items]

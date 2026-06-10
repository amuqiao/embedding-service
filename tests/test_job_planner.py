from app.services.job_planner import build_job_plan, split_text_with_registry


def test_job_planner_uses_single_for_short_text():
    plan = build_job_plan("novel_localization.step1_localize", "短文本")
    assert plan.execution_mode == "single"
    assert [item.kind for item in plan.work_items] == ["whole"]


def test_job_planner_uses_single_when_chunking_disabled(monkeypatch):
    monkeypatch.setattr("app.infrastructure.config.settings.NOVEL_LOCALIZATION_CHUNKING_ENABLED", False)
    monkeypatch.setattr("app.infrastructure.config.settings.NOVEL_LOCALIZATION_SINGLE_MAX_CHARS", 1)

    plan = build_job_plan("novel_localization.step1_localize", "第一段很长很长\n\n第二段也很长很长")

    assert plan.execution_mode == "single"
    assert [item.kind for item in plan.work_items] == ["whole"]


def test_job_planner_uses_chunked_for_long_step1_text(monkeypatch):
    monkeypatch.setattr("app.infrastructure.config.settings.NOVEL_LOCALIZATION_CHUNKING_ENABLED", True)
    monkeypatch.setattr("app.infrastructure.config.settings.NOVEL_LOCALIZATION_SINGLE_MAX_CHARS", 10)
    monkeypatch.setattr("app.infrastructure.config.settings.NOVEL_LOCALIZATION_CHUNK_SIZE", 8)

    plan = build_job_plan("novel_localization.step1_localize", "第一段很长很长\n\n第二段也很长很长")

    assert plan.execution_mode == "chunked"
    assert plan.chunk_count >= 2
    assert "memory" in [item.kind for item in plan.work_items]
    assert "merge" in [item.kind for item in plan.work_items]


def test_job_planner_uses_chunked_for_long_step3_with_scan(monkeypatch):
    monkeypatch.setattr("app.infrastructure.config.settings.NOVEL_LOCALIZATION_CHUNKING_ENABLED", True)
    monkeypatch.setattr("app.infrastructure.config.settings.NOVEL_LOCALIZATION_SINGLE_MAX_CHARS", 10)
    monkeypatch.setattr("app.infrastructure.config.settings.NOVEL_LOCALIZATION_CHUNK_SIZE", 8)

    plan = build_job_plan("novel_localization.step3_translate", "第一段很长很长\n\n第二段也很长很长")

    assert plan.execution_mode == "chunked"
    assert "memory" not in [item.kind for item in plan.work_items]
    assert "merge" in [item.kind for item in plan.work_items]
    assert "scan" in [item.kind for item in plan.work_items]


def test_splitter_splits_single_oversized_paragraph():
    chunks = split_text_with_registry("甲" * 25, max_chars=8)

    assert len(chunks) == 4
    assert [chunk["char_count"] for chunk in chunks] == [8, 8, 8, 1]


def test_splitter_prefers_sentence_boundaries_for_oversized_paragraph():
    chunks = split_text_with_registry("第一句很长。第二句也很长。第三句也很长。", max_chars=8)

    assert len(chunks) >= 2
    assert all(chunk["char_count"] <= 8 for chunk in chunks)

"""Project-level pytest configuration.

Ensures workflow handlers are registered before any test that calls
workflow_registry.get() (e.g. via run_ai_job or merge_work_items).
"""
from app.workflows.novel_localization.handler import register_all

register_all()

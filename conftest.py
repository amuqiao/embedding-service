"""Project-level pytest configuration.

Ensures workflow handlers are registered before any test that calls
workflow_registry.get() (e.g. via run_ai_job or merge_work_items).
"""
from app.workflows.register import register_all_workflows

register_all_workflows()

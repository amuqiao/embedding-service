"""Project-level pytest configuration.

Ensures job executors are registered before any test that calls the job
registry (e.g. via run_ai_job or job status validation).
"""
import os

os.environ["ENABLED_JOB_TYPES"] = ""

from app.jobs.types.register import register_all_job_types

register_all_job_types()

"""Project-level pytest configuration.

Ensures job executors are registered before any test that calls the job
registry (e.g. via run_ai_job or job status validation).
"""
import os

os.environ["ENABLED_BUSINESS_PACKAGES"] = ""

from app.business_packages.register import register_all_business_packages

register_all_business_packages()

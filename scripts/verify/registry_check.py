"""Validate registry consistency through the real FastAPI app object.

The shell wrapper prints the "Registry" section. This script emits one success
event and suppresses app import logs unless validation fails.
"""

import contextlib
import io
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))


def validate_registry_with_captured_logs(stdout: io.StringIO, stderr: io.StringIO) -> None:
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        from app.core.registry_checks import validate_all_registries
        from app.jobs.types.register import register_all_job_types
        from app.main import app

        register_all_job_types()
        validate_all_registries(app)


def main() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        validate_registry_with_captured_logs(stdout, stderr)
    except Exception:
        # App startup logs are noise on success, but useful context when validation fails.
        output = stdout.getvalue().strip()
        errors = stderr.getvalue().strip()
        if output:
            print(output, file=sys.stderr)
        if errors:
            print(errors, file=sys.stderr)
        raise
    print(f"{'OK':<9} {'registry':<10} consistency")


if __name__ == "__main__":
    main()

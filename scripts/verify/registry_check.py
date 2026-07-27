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
        from app.api.operations import all_operation_specs
        from app.core.config import settings
        from app.core.registry_checks import validate_all_registries
        from app.jobs.types.register import register_all_job_types
        from app.main import app

        register_all_job_types()
        validate_all_registries(app)
        validate_contract_docs(
            service_contract_path=ROOT_DIR / "docs" / "api" / "service-contract.md",
            api_prefix=settings.service.api_prefix,
            operation_specs=all_operation_specs(),
        )


def validate_required_docs() -> None:
    required = [
        ROOT_DIR / "docs" / "api" / "service-contract.md",
        ROOT_DIR / "docs" / "api" / "extension-guide.md",
        ROOT_DIR / "docs" / "current" / "job-kernel.md",
        ROOT_DIR / "docs" / "current" / "workflow-kernel.md",
        ROOT_DIR / "docs" / "current" / "registry-governance.md",
    ]
    missing = [path.relative_to(ROOT_DIR).as_posix() for path in required if not path.exists()]
    if missing:
        raise RuntimeError(f"required docs missing: {', '.join(missing)}")


def validate_contract_docs(*, service_contract_path: Path, api_prefix: str, operation_specs: dict) -> None:
    validate_required_docs()
    service_contract = service_contract_path.read_text(encoding="utf-8")
    missing_routes: list[str] = []
    for spec in operation_specs.values():
        if spec.channel != "http":
            continue
        route = f"{spec.method} {api_prefix}{spec.path}"
        if route not in service_contract:
            missing_routes.append(route)
    if missing_routes:
        joined = ", ".join(missing_routes)
        raise RuntimeError(f"service contract missing route entries: {joined}")


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

"""Validate Alembic revision identifiers before database migration tests run."""

from __future__ import annotations

import ast
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
VERSIONS_DIR = ROOT_DIR / "alembic" / "versions"
MAX_ALEMBIC_VERSION_NUM_LENGTH = 32


def _literal_assignment(module: ast.Module, name: str) -> str | None:
    for node in module.body:
        target_name: str | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                continue
            target_name = node.targets[0].id
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target_name = node.target.id
            value = node.value

        if target_name == name:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                return value.value
            return None
    return None


def check_revision_files(paths: list[Path]) -> list[str]:
    issues: list[str] = []
    seen: dict[str, Path] = {}

    for path in sorted(paths):
        try:
            module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            issues.append(f"{path}: syntax error while reading Alembic revision: {exc}")
            continue

        revision = _literal_assignment(module, "revision")
        if revision is None:
            issues.append(f"{path}: revision must be a string literal")
            continue
        if not revision:
            issues.append(f"{path}: revision must not be empty")
            continue
        if len(revision) > MAX_ALEMBIC_VERSION_NUM_LENGTH:
            issues.append(
                f"{path}: revision '{revision}' length {len(revision)} exceeds "
                f"alembic_version.version_num limit {MAX_ALEMBIC_VERSION_NUM_LENGTH}"
            )
        if revision in seen:
            issues.append(f"{path}: duplicate revision '{revision}' also declared in {seen[revision]}")
        else:
            seen[revision] = path

    return issues


def default_revision_files() -> list[Path]:
    return sorted(path for path in VERSIONS_DIR.glob("*.py") if path.name != "__init__.py")


def main() -> int:
    paths = default_revision_files()
    issues = check_revision_files(paths)
    if issues:
        for issue in issues:
            print(issue, file=sys.stderr)
        return 1

    print(f"{'OK':<9} {'alembic':<10} revisions={len(paths)} max_revision_length={MAX_ALEMBIC_VERSION_NUM_LENGTH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

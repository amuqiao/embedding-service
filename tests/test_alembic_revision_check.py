from pathlib import Path

from scripts.verify.alembic_revision_check import MAX_ALEMBIC_VERSION_NUM_LENGTH, check_revision_files


def _write_revision(path: Path, revision: str) -> None:
    path.write_text(
        "\n".join(
            [
                '"""test migration"""',
                "",
                "from collections.abc import Sequence",
                "",
                f'revision: str = "{revision}"',
                'down_revision: str | Sequence[str] | None = None',
                "branch_labels = None",
                "depends_on = None",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_alembic_revision_check_rejects_revision_longer_than_version_num_limit(tmp_path):
    migration = tmp_path / "0001_too_long.py"
    revision = "x" * (MAX_ALEMBIC_VERSION_NUM_LENGTH + 1)
    _write_revision(migration, revision)

    issues = check_revision_files([migration])

    assert issues == [
        f"{migration}: revision '{revision}' length 33 exceeds alembic_version.version_num limit 32"
    ]


def test_alembic_revision_check_rejects_duplicate_revisions(tmp_path):
    first = tmp_path / "0001_first.py"
    second = tmp_path / "0002_second.py"
    _write_revision(first, "0001_same")
    _write_revision(second, "0001_same")

    issues = check_revision_files([first, second])

    assert issues == [f"{second}: duplicate revision '0001_same' also declared in {first}"]

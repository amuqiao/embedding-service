import json
from pathlib import Path

from typer.testing import CliRunner

from scripts.triton_bench import cli


RUNNER = CliRunner()


def test_run_dry_run_writes_manifest_without_calling_triton(tmp_path):
    output_dir = tmp_path / "triton-bench"

    result = RUNNER.invoke(
        cli.app,
        [
            "run",
            "--url",
            "127.0.0.1:8000",
            "--models",
            "drums",
            "--concurrency",
            "1,2",
            "--requests-per-level",
            "3",
            "--stage-cooldown-seconds",
            "0",
            "--run-id",
            "dry",
            "--output-dir",
            str(output_dir),
            "--dry-run",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    manifest_path = output_dir / "dry" / "manifest.json"
    results_path = output_dir / "dry" / "results.csv"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload == manifest
    assert payload["status"] == "dry_run"
    assert payload["models"] == ["htdemucs_ft_drums"]
    assert payload["sources"] == ["drums"]
    assert payload["concurrency_levels"] == [1, 2]
    assert payload["requests_per_level"] == 3
    assert payload["input_source"] == "random"
    assert payload["input_shape"] == [1, 2, 343980]
    assert payload["results"] == []
    assert results_path.read_text(encoding="utf-8").startswith("concurrency,requests,successes")


def test_run_requires_confirmation_for_aggressive_levels(tmp_path):
    result = RUNNER.invoke(
        cli.app,
        [
            "run",
            "--url",
            "127.0.0.1:8000",
            "--models",
            "drums",
            "--concurrency",
            "8",
            "--output-dir",
            str(tmp_path),
            "--dry-run",
        ],
    )

    assert result.exit_code == 2
    assert "--confirm-aggressive" in result.stderr


def test_script_help_is_available():
    result = RUNNER.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "doctor" in result.output
    assert "run" in result.output

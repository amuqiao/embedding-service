from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


EXIT_USAGE = 2
EXIT_INSPECT_FAILED = 4
HTDEMUCS_EXPERT_FILES = {
    "drums": "htdemucs_ft_drums.onnx",
    "bass": "htdemucs_ft_bass.onnx",
    "other": "htdemucs_ft_other.onnx",
    "vocals": "htdemucs_ft_vocals.onnx",
}


@dataclass(frozen=True)
class TensorInfo:
    name: str
    type: str
    dtype: str
    shape: list[Any]

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.type, "dtype": self.dtype, "shape": self.shape}


@dataclass(frozen=True)
class FileInfo:
    file: str
    path: Path
    present: bool
    size_bytes: int | None
    sha256: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "path": str(self.path),
            "present": self.present,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class ExpertInfo:
    stem: str
    file: str
    path: Path
    size_bytes: int
    sha256: str
    metadata: dict[str, Any]
    session_providers: list[str]
    inputs: list[TensorInfo]
    outputs: list[TensorInfo]

    def to_dict(self) -> dict[str, Any]:
        return {
            "stem": self.stem,
            "file": self.file,
            "path": str(self.path),
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "metadata": self.metadata,
            "session_providers": self.session_providers,
            "inputs": [item.to_dict() for item in self.inputs],
            "outputs": [item.to_dict() for item in self.outputs],
        }


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def die(message: str, code: int = EXIT_USAGE) -> None:
    eprint(f"ERROR: {message}")
    raise SystemExit(code)


def section(title: str) -> None:
    print(f"\n== {title} ==")


def row(key: str, value: object | None, detail: object | None = None) -> None:
    text = "-" if value is None else str(value)
    suffix = "" if detail in (None, "") else f" {detail}"
    print(f"  {key:<14} {text}{suffix}")


def event(status: str, subject: str, detail: str = "") -> None:
    print(f"{status:<9} {subject:<18} {detail}")


def require_onnxruntime():
    try:
        import onnxruntime as ort
    except ModuleNotFoundError as exc:
        die(
            "onnxruntime is not available; run: uv sync --extra audio-separation",
            EXIT_USAGE,
        )
        raise AssertionError("unreachable") from exc
    except Exception as exc:
        die(f"onnxruntime import failed: {type(exc).__name__}: {exc}", EXIT_USAGE)
        raise AssertionError("unreachable") from exc
    return ort


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_shape(shape: list[Any] | tuple[Any, ...] | None) -> list[Any]:
    if shape is None:
        return []
    normalized: list[Any] = []
    for value in shape:
        if isinstance(value, (int, str)) or value is None:
            normalized.append(value)
        else:
            normalized.append(str(value))
    return normalized


def tensor_info(node: Any) -> TensorInfo:
    type_name = str(getattr(node, "type", ""))
    return TensorInfo(
        name=str(getattr(node, "name", "")),
        type=type_name,
        dtype=dtype_from_type(type_name),
        shape=normalize_shape(getattr(node, "shape", None)),
    )


def dtype_from_type(type_name: str) -> str:
    if type_name.startswith("tensor(") and type_name.endswith(")"):
        return type_name[len("tensor(") : -1]
    return type_name


def metadata_dict(session: Any) -> dict[str, Any]:
    try:
        metadata = session.get_modelmeta()
    except Exception:
        return {}
    return {
        "producer_name": getattr(metadata, "producer_name", ""),
        "graph_name": getattr(metadata, "graph_name", ""),
        "domain": getattr(metadata, "domain", ""),
        "description": getattr(metadata, "description", ""),
        "version": getattr(metadata, "version", None),
        "custom_metadata": dict(getattr(metadata, "custom_metadata_map", {}) or {}),
    }


def auxiliary_file_info(model_dir: Path, filename: str) -> FileInfo:
    path = model_dir / filename
    if not path.is_file() or path.stat().st_size == 0:
        return FileInfo(file=filename, path=path, present=False, size_bytes=None, sha256=None)
    return FileInfo(
        file=filename,
        path=path,
        present=True,
        size_bytes=path.stat().st_size,
        sha256=sha256_file(path),
    )


def session_providers(session: Any) -> list[str]:
    try:
        return list(session.get_providers())
    except Exception:
        return []


def inspect_expert(ort: Any, stem: str, model_dir: Path, providers: list[str] | None) -> ExpertInfo:
    filename = HTDEMUCS_EXPERT_FILES[stem]
    path = model_dir / filename
    if not path.is_file() or path.stat().st_size == 0:
        die(f"required ONNX file missing or empty: {path}", EXIT_INSPECT_FAILED)

    try:
        if providers:
            session = ort.InferenceSession(str(path), providers=providers)
        else:
            session = ort.InferenceSession(str(path))
    except Exception as exc:
        die(f"failed to inspect {filename}: {exc}", EXIT_INSPECT_FAILED)

    return ExpertInfo(
        stem=stem,
        file=filename,
        path=path,
        size_bytes=path.stat().st_size,
        sha256=sha256_file(path),
        metadata=metadata_dict(session),
        session_providers=session_providers(session),
        inputs=[tensor_info(item) for item in session.get_inputs()],
        outputs=[tensor_info(item) for item in session.get_outputs()],
    )


def print_human(
    model: str,
    source: str,
    repo: str,
    model_dir: Path,
    onnxruntime_version: str,
    available_providers: list[str],
    experts: list[ExpertInfo],
    auxiliary_files: list[FileInfo],
) -> None:
    section("ONNX Inspect")
    row("model", model)
    row("source", source, repo)
    row("repo", repo)
    row("model-dir", model_dir)
    row("ort-version", onnxruntime_version)
    row("providers", ", ".join(available_providers) if available_providers else "-")

    section("Experts")
    for expert in experts:
        event("OK", expert.stem, expert.file)
        row("size-bytes", expert.size_bytes)
        row("sha256", expert.sha256)
        row("session-providers", ", ".join(expert.session_providers) if expert.session_providers else "-")
        for index, item in enumerate(expert.inputs):
            label = "input" if index == 0 else f"input[{index}]"
            row(label, item.name, f"{item.type} {item.shape}")
        for index, item in enumerate(expert.outputs):
            label = "output" if index == 0 else f"output[{index}]"
            row(label, item.name, f"{item.type} {item.shape}")

    section("Auxiliary Files")
    for item in auxiliary_files:
        event("OK" if item.present else "MISSING", item.file, str(item.path))
        if item.present:
            row("size-bytes", item.size_bytes)
            row("sha256", item.sha256)


def print_json(
    model: str,
    source: str,
    repo: str,
    model_dir: Path,
    onnxruntime_version: str,
    available_providers: list[str],
    experts: list[ExpertInfo],
    auxiliary_files: list[FileInfo],
) -> None:
    payload = {
        "model": model,
        "source": source,
        "repo": repo,
        "model_dir": str(model_dir),
        "scope": "required",
        "complete": True,
        "onnxruntime_version": onnxruntime_version,
        "available_providers": available_providers,
        "experts": [item.to_dict() for item in experts],
        "auxiliary_files": [item.to_dict() for item in auxiliary_files],
    }
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def parse_providers(value: str | None) -> list[str] | None:
    if value is None:
        return None
    providers = [item.strip() for item in value.split(",") if item.strip()]
    if not providers:
        raise argparse.ArgumentTypeError("must contain at least one provider")
    return providers


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="./scripts/models.sh inspect",
        description="Inspect local htdemucs-ft ONNX expert model signatures and sha256 values.",
    )
    parser.add_argument("--model", required=True, choices=["htdemucs-ft"], help="Known model asset name.")
    parser.add_argument("--source", required=True, help="Source kind for output metadata.")
    parser.add_argument("--repo", required=True, help="Source repository id for output metadata.")
    parser.add_argument("--model-dir", required=True, help="Local model directory.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON only.")
    parser.add_argument(
        "--providers",
        type=parse_providers,
        help="Optional comma-separated ONNX Runtime providers for InferenceSession.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    model_dir = Path(args.model_dir)
    if not model_dir.is_dir():
        die(f"model directory not found: {model_dir}", EXIT_INSPECT_FAILED)

    ort = require_onnxruntime()
    available_providers = list(ort.get_available_providers())
    onnxruntime_version = str(getattr(ort, "__version__", "unknown"))
    experts = [
        inspect_expert(ort, stem, model_dir, args.providers)
        for stem in ("drums", "bass", "other", "vocals")
    ]
    auxiliary_files = [
        auxiliary_file_info(model_dir, "bag_infer.py"),
        auxiliary_file_info(model_dir, "requirements.txt"),
    ]

    if args.json:
        print_json(
            args.model,
            args.source,
            args.repo,
            model_dir,
            onnxruntime_version,
            available_providers,
            experts,
            auxiliary_files,
        )
    else:
        print_human(
            args.model,
            args.source,
            args.repo,
            model_dir,
            onnxruntime_version,
            available_providers,
            experts,
            auxiliary_files,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

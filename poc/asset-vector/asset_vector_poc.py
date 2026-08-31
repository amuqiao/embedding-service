from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Annotated, Any
import hashlib
import html
import json
import os
import sys
from urllib.parse import urlsplit

import httpx
import psycopg2
import psycopg2.extras
import typer
from dotenv import dotenv_values


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from storage_adapter import (  # noqa: E402
    AssetVectorPocStorageAdapter,
    build_oss_adapter_from_env,
    build_public_reader_adapter,
    public_host_from_url,
    uploaded_asset_to_dict,
)


APP = typer.Typer(
    help="POC for asset vector image search with DashScope multimodal embeddings and pgvector.",
    no_args_is_help=True,
)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
DEFAULT_MODEL = "tongyi-embedding-vision-flash"
DEFAULT_DIMENSION = 768
DEFAULT_TABLE = "poc_asset_vectors"
DEFAULT_DASHSCOPE_BASE_URL = "https://dashscope-intl.aliyuncs.com/api/v1"
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_REPORTS_DIR = SCRIPT_DIR / "reports"
DEFAULT_MANIFEST = DEFAULT_REPORTS_DIR / "manifests/assets-oss-manifest.jsonl"
DEFAULT_HTML_REPORTS_DIR = DEFAULT_REPORTS_DIR / "html"
DEFAULT_RESOURCE_REPORTS_DIR = DEFAULT_HTML_REPORTS_DIR / "resource"
DEFAULT_IMAGE_REPORTS_DIR = DEFAULT_HTML_REPORTS_DIR / "image"
DEFAULT_TEXT_REPORTS_DIR = DEFAULT_HTML_REPORTS_DIR / "text"
REPORT_MODE_DIRS = {
    "resource": "资源搜相似图",
    "image": "图片搜图",
    "text": "文字搜图",
}
REPORT_TITLE_OVERRIDES = {
    "apple-text.html": "文字搜图：苹果",
    "champagne-query-image.html": "图片搜图：香槟",
    "champagne-query-image-after-reindex.html": "图片搜图：香槟（重建索引后）",
    "champagne-resource.html": "资源搜相似图：香槟",
    "champagne-text.html": "文字搜图：香槟",
    "golden-dice-text.html": "文字搜图：金色骰子",
    "pistol-text.html": "文字搜图：带消音器手枪",
}


@dataclass(frozen=True)
class RuntimeConfig:
    env: dict[str, str]
    database_url: str
    dashscope_api_key: str | None
    dashscope_base_url: str | None
    model: str
    dimension: int
    table: str
    image_max_bytes: int


@dataclass(frozen=True)
class AssetInput:
    resource_id: str
    group_id: str | None
    local_path: Path | None
    public_url: str | None
    text_payload: str | None
    sha256: str | None
    content_type: str | None


@dataclass(frozen=True)
class SearchResult:
    resource_id: str
    group_id: str | None
    score: float
    public_url: str | None
    local_path: str | None
    text_payload: str | None
    embedding_input_kind: str
    source_hash: str
    updated_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "resource_id": self.resource_id,
            "group_id": self.group_id,
            "score": self.score,
            "public_url": self.public_url,
            "local_path": self.local_path,
            "text_payload": self.text_payload,
            "embedding_input_kind": self.embedding_input_kind,
            "source_hash": self.source_hash,
            "updated_at": self.updated_at,
        }


_CONTEXT: dict[str, Any] = {}


@APP.callback()
def configure(
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", help="Env file. Explicit value overrides current shell env."),
    ] = None,
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", help="PostgreSQL URL. Defaults to DATABASE_URL from env."),
    ] = None,
    dashscope_base_url: Annotated[
        str | None,
        typer.Option(
            "--dashscope-base-url",
            help="DashScope native api/v1 base URL, not compatible-mode/v1.",
        ),
    ] = None,
    model: Annotated[str, typer.Option("--model", help="DashScope multimodal embedding model.")] = DEFAULT_MODEL,
    dimension: Annotated[int, typer.Option("--dimension", help="Embedding dimension.")] = DEFAULT_DIMENSION,
    table: Annotated[str, typer.Option("--table", help="POC table name.")] = DEFAULT_TABLE,
) -> None:
    env = load_effective_env(env_file)
    if database_url is not None:
        env["DATABASE_URL"] = database_url
    if dashscope_base_url is not None:
        env["POC_DASHSCOPE_BASE_URL"] = dashscope_base_url
    _CONTEXT["config"] = build_runtime_config(env, model=model, dimension=dimension, table=table)


@APP.command("check-env")
def check_env() -> None:
    config = runtime_config()
    typer.echo("POC env is loaded.")
    typer.echo(f"database_url={redact_database_url(config.database_url)}")
    typer.echo(f"dashscope_base_url={config.dashscope_base_url}")
    typer.echo(f"dashscope_api_key={'configured' if config.dashscope_api_key else 'missing'}")
    typer.echo(f"model={config.model}")
    typer.echo(f"dimension={config.dimension}")
    typer.echo(f"table={config.table}")
    typer.echo(f"image_max_bytes={config.image_max_bytes}")
    if has_oss_upload_env(config.env):
        typer.echo("oss_upload=enabled")
        typer.echo(f"oss_bucket={config.env['OSS_BUCKET']}")
        typer.echo(f"oss_region={config.env['OSS_REGION']}")
        typer.echo(f"oss_project_root={config.env['OSS_PROJECT_ROOT']}")
        typer.echo(f"oss_public_endpoint={config.env.get('OSS_PUBLIC_ENDPOINT', '')}")
    else:
        typer.echo("oss_upload=disabled_missing_env")


@APP.command("init-db")
def init_db() -> None:
    config = runtime_config()
    with connect(config) as conn:
        create_schema(conn, config.table)
    typer.echo(f"Initialized pgvector POC table: {config.table}")


@APP.command("index-dir")
def index_dir(
    image_dir: Annotated[
        Path,
        typer.Argument(help="Local image directory."),
    ],
    oss_prefix: Annotated[str, typer.Option("--oss-prefix", help="OSS key prefix under OSS_PROJECT_ROOT.")] = "asset-vector-poc",
    text_map: Annotated[
        Path | None,
        typer.Option("--text-map", help="Optional JSON mapping resource_id to text_payload."),
    ] = None,
    group_map: Annotated[
        Path | None,
        typer.Option("--group-map", help="Optional JSON mapping resource_id to group_id."),
    ] = None,
    limit: Annotated[int | None, typer.Option("--limit", help="Max images to index.")] = None,
    verify_public_url: Annotated[
        bool,
        typer.Option("--verify-public-url/--no-verify-public-url", help="Read uploaded public URL before embedding."),
    ] = True,
    show_progress: Annotated[
        bool,
        typer.Option("--progress/--no-progress", help="Print per-item indexing progress to stderr."),
    ] = True,
    confirm_remote: Annotated[
        bool,
        typer.Option("--confirm-remote", help="Confirm OSS upload and DashScope embedding calls."),
    ] = False,
) -> None:
    require_remote_confirmation(confirm_remote, "index-dir uploads images to OSS and calls DashScope embeddings")
    config = runtime_config()
    inputs = scan_image_dir(image_dir, text_map=text_map, group_map=group_map, limit=limit)
    if not inputs:
        raise typer.BadParameter(f"no supported image found under {image_dir}")
    hosts = allowed_hosts_for_upload(config.env)
    storage = build_oss_adapter_from_env(config.env, allowed_hosts=hosts)
    with connect(config) as conn:
        create_schema(conn, config.table)
        summary = index_inputs(
            conn,
            config,
            storage=storage,
            inputs=inputs,
            oss_prefix=oss_prefix,
            verify_public_url=verify_public_url,
            show_progress=show_progress,
        )
    print_json(summary)
    fail_if_index_failed(summary)


@APP.command("index-manifest")
def index_manifest(
    manifest: Annotated[Path, typer.Argument(help="JSONL manifest path.")] = DEFAULT_MANIFEST,
    oss_prefix: Annotated[str, typer.Option("--oss-prefix", help="OSS key prefix under OSS_PROJECT_ROOT.")] = "asset-vector-poc",
    upload_local: Annotated[
        bool,
        typer.Option("--upload-local/--no-upload-local", help="Upload local_path entries that have no public_url."),
    ] = True,
    verify_public_url: Annotated[
        bool,
        typer.Option("--verify-public-url/--no-verify-public-url", help="Read public URL before embedding."),
    ] = True,
    show_progress: Annotated[
        bool,
        typer.Option("--progress/--no-progress", help="Print per-item indexing progress to stderr."),
    ] = True,
    confirm_remote: Annotated[
        bool,
        typer.Option("--confirm-remote", help="Confirm OSS upload when needed and DashScope embedding calls."),
    ] = False,
) -> None:
    require_remote_confirmation(confirm_remote, "index-manifest calls DashScope embeddings and may upload images to OSS")
    config = runtime_config()
    inputs = read_manifest(manifest)
    if not inputs:
        raise typer.BadParameter(f"manifest is empty: {manifest}")
    needs_upload = any(item.local_path is not None and item.public_url is None for item in inputs)
    if needs_upload and upload_local and not has_oss_upload_env(config.env):
        raise ValueError("manifest contains local_path entries, but OSS upload env is incomplete")
    hosts = allowed_hosts_from_inputs(inputs, config.env)
    storage = build_storage_for_manifest(config.env, allowed_hosts=hosts, upload_local=upload_local)
    with connect(config) as conn:
        create_schema(conn, config.table)
        summary = index_inputs(
            conn,
            config,
            storage=storage,
            inputs=inputs,
            oss_prefix=oss_prefix,
            upload_local=upload_local,
            verify_public_url=verify_public_url,
            show_progress=show_progress,
        )
    print_json(summary)
    fail_if_index_failed(summary)


@APP.command("search-image")
def search_image(
    query_image: Annotated[
        Path | None,
        typer.Option("--query-image", help="Local query image. It will be uploaded to OSS before embedding."),
    ] = None,
    query_url: Annotated[
        str | None,
        typer.Option("--query-url", help="Existing public HTTPS query image URL."),
    ] = None,
    top_k: Annotated[int, typer.Option("--top-k", help="Number of search results.")] = 10,
    exclude_resource_id: Annotated[
        list[str] | None,
        typer.Option("--exclude-resource-id", help="Resource IDs to exclude."),
    ] = None,
    candidate_resource_id: Annotated[
        list[str] | None,
        typer.Option("--candidate-resource-id", help="Candidate resource IDs. Omit for POC full table search."),
    ] = None,
    candidate_file: Annotated[
        Path | None,
        typer.Option("--candidate-file", help="Text file containing one candidate resource_id per line."),
    ] = None,
    html_report: Annotated[
        Path | None,
        typer.Option("--html-report", help="Optional HTML report path."),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON only.")] = False,
    confirm_remote: Annotated[
        bool,
        typer.Option("--confirm-remote", help="Confirm query image OSS upload when needed and DashScope embedding call."),
    ] = False,
) -> None:
    require_remote_confirmation(confirm_remote, "search-image calls DashScope embeddings and may upload the query image to OSS")
    config = runtime_config()
    if (query_image is None) == (query_url is None):
        raise typer.BadParameter("pass exactly one of --query-image or --query-url")
    ensure_table_matches_config(config)
    storage: AssetVectorPocStorageAdapter | None = None
    public_url = query_url
    if query_image is not None:
        storage = build_oss_adapter_from_env(config.env, allowed_hosts=allowed_hosts_for_upload(config.env))
        uploaded = storage.upload_local_image(
            query_image,
            key=f"asset-vector-poc/query/{timestamp_slug()}-{query_image.name}",
        )
        public_url = uploaded.public_url
    if public_url is None:
        raise RuntimeError("query public_url was not resolved")
    embedding = DashScopeMultimodalClient(config).embed({"image": public_url})
    results = search_by_embedding(
        config,
        embedding,
        top_k=top_k,
        excluded=set(exclude_resource_id or ()),
        candidates=load_candidates(candidate_resource_id, candidate_file),
    )
    emit_search_results(
        results,
        query_label=public_url,
        query_image_url=public_url,
        html_report=html_report,
        as_json=as_json,
    )


@APP.command("search-text")
def search_text(
    text: Annotated[str, typer.Argument(help="Query text.")],
    top_k: Annotated[int, typer.Option("--top-k", help="Number of search results.")] = 10,
    candidate_resource_id: Annotated[
        list[str] | None,
        typer.Option("--candidate-resource-id", help="Candidate resource IDs. Omit for POC full table search."),
    ] = None,
    candidate_file: Annotated[
        Path | None,
        typer.Option("--candidate-file", help="Text file containing one candidate resource_id per line."),
    ] = None,
    html_report: Annotated[
        Path | None,
        typer.Option("--html-report", help="Optional HTML report path."),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON only.")] = False,
    confirm_remote: Annotated[
        bool,
        typer.Option("--confirm-remote", help="Confirm DashScope embedding call."),
    ] = False,
) -> None:
    require_remote_confirmation(confirm_remote, "search-text calls DashScope embeddings")
    config = runtime_config()
    ensure_table_matches_config(config)
    embedding = DashScopeMultimodalClient(config).embed({"text": text})
    results = search_by_embedding(
        config,
        embedding,
        top_k=top_k,
        candidates=load_candidates(candidate_resource_id, candidate_file),
    )
    emit_search_results(results, query_label=text, query_image_url=None, html_report=html_report, as_json=as_json)


@APP.command("search-resource")
def search_resource(
    resource_id: Annotated[str, typer.Argument(help="Indexed resource_id to use as query.")],
    top_k: Annotated[int, typer.Option("--top-k", help="Number of search results.")] = 10,
    include_self: Annotated[
        bool,
        typer.Option("--include-self/--exclude-self", help="Include the query resource itself in results."),
    ] = False,
    candidate_resource_id: Annotated[
        list[str] | None,
        typer.Option("--candidate-resource-id", help="Candidate resource IDs. Omit for POC full table search."),
    ] = None,
    candidate_file: Annotated[
        Path | None,
        typer.Option("--candidate-file", help="Text file containing one candidate resource_id per line."),
    ] = None,
    html_report: Annotated[
        Path | None,
        typer.Option("--html-report", help="Optional HTML report path."),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON only.")] = False,
) -> None:
    config = runtime_config()
    ensure_table_matches_config(config)
    embedding = get_resource_embedding(config, resource_id)
    candidates = load_candidates(candidate_resource_id, candidate_file)
    results = search_by_embedding(
        config,
        embedding,
        top_k=top_k,
        excluded=set() if include_self else {resource_id},
        candidates=candidates,
    )
    emit_search_results(
        results,
        query_label=resource_id,
        query_image_url=get_resource_public_url(config, resource_id),
        html_report=html_report,
        as_json=as_json,
    )


@APP.command("generate-resource-reports")
def generate_resource_reports(
    resource_id: Annotated[
        list[str] | None,
        typer.Option("--resource-id", help="Resource ID to report. Repeat for multiple IDs."),
    ] = None,
    resource_file: Annotated[
        Path | None,
        typer.Option("--resource-file", help="Text file containing one resource_id per line."),
    ] = None,
    limit: Annotated[int | None, typer.Option("--limit", help="Generate reports for the first N indexed resources.")] = None,
    all_resources: Annotated[
        bool,
        typer.Option("--all", help="Generate reports for all indexed resources."),
    ] = False,
    top_k: Annotated[int, typer.Option("--top-k", help="Number of search results in each report.")] = 10,
    include_self: Annotated[
        bool,
        typer.Option("--include-self/--exclude-self", help="Include the query resource itself in each report."),
    ] = False,
    candidate_resource_id: Annotated[
        list[str] | None,
        typer.Option("--candidate-resource-id", help="Candidate resource IDs. Omit for POC full table search."),
    ] = None,
    candidate_file: Annotated[
        Path | None,
        typer.Option("--candidate-file", help="Text file containing one candidate resource_id per line."),
    ] = None,
    reports_dir: Annotated[
        Path,
        typer.Option("--reports-dir", help="Directory for generated resource query HTML reports."),
    ] = DEFAULT_RESOURCE_REPORTS_DIR,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON only.")] = False,
) -> None:
    config = runtime_config()
    ensure_table_matches_config(config)
    query_ids = load_report_resource_ids(
        config,
        resource_id=resource_id,
        resource_file=resource_file,
        limit=limit,
        all_resources=all_resources,
    )
    candidates = load_candidates(candidate_resource_id, candidate_file)
    generated: list[dict[str, object]] = []
    failed: list[dict[str, object]] = []

    for item in query_ids:
        try:
            embedding = get_resource_embedding(config, item)
            results = search_by_embedding(
                config,
                embedding,
                top_k=top_k,
                excluded=set() if include_self else {item},
                candidates=candidates,
            )
            report_path = reports_dir / f"search-resource-{report_slug(item)}.html"
            report_index = write_html_report(
                report_path,
                query_label=item,
                query_image_url=get_resource_public_url(config, item),
                results=results,
            )
            generated.append(
                {
                    "resource_id": item,
                    "html_report": str(report_path),
                    "html_report_index": str(report_index),
                    "result_count": len(results),
                }
            )
        except Exception as exc:
            failed.append({"resource_id": item, "error": str(exc)})

    root_index = write_reports_root_index_for(reports_dir)
    payload: dict[str, object] = {
        "mode": "resource",
        "requested": len(query_ids),
        "generated": len(generated),
        "failed": len(failed),
        "reports_dir": str(reports_dir),
        "html_root_index": str(root_index) if root_index is not None else None,
        "items": generated,
        "failed_items": failed,
    }
    if as_json:
        print_json(payload)
    else:
        typer.echo(f"mode=resource requested={len(query_ids)} generated={len(generated)} failed={len(failed)}")
        typer.echo(f"reports_dir={reports_dir}")
        if root_index is not None:
            typer.echo(f"html_root_index={root_index}")
        for item in generated:
            typer.echo(f"  {item['resource_id']} -> {item['html_report']}")
        for item in failed:
            typer.echo(f"  FAILED {item['resource_id']}: {item['error']}")
    if failed:
        raise typer.Exit(1)


@APP.command("generate-image-reports")
def generate_image_reports(
    manifest: Annotated[
        Path,
        typer.Option("--manifest", help="JSONL manifest to select image query resources from."),
    ] = DEFAULT_MANIFEST,
    resource_id: Annotated[
        list[str] | None,
        typer.Option("--resource-id", help="Manifest resource_id to use as image query. Repeat for multiple IDs."),
    ] = None,
    resource_file: Annotated[
        Path | None,
        typer.Option("--resource-file", help="Text file containing one manifest resource_id per line."),
    ] = None,
    limit: Annotated[int | None, typer.Option("--limit", help="Generate reports for the first N manifest resources.")] = None,
    all_resources: Annotated[
        bool,
        typer.Option("--all", help="Generate reports for all manifest resources."),
    ] = False,
    top_k: Annotated[int, typer.Option("--top-k", help="Number of search results in each report.")] = 10,
    exclude_resource_id: Annotated[
        list[str] | None,
        typer.Option("--exclude-resource-id", help="Resource IDs to exclude from search results."),
    ] = None,
    candidate_resource_id: Annotated[
        list[str] | None,
        typer.Option("--candidate-resource-id", help="Candidate resource IDs. Omit for POC full table search."),
    ] = None,
    candidate_file: Annotated[
        Path | None,
        typer.Option("--candidate-file", help="Text file containing one candidate resource_id per line."),
    ] = None,
    reports_dir: Annotated[
        Path,
        typer.Option("--reports-dir", help="Directory for generated image query HTML reports."),
    ] = DEFAULT_IMAGE_REPORTS_DIR,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON only.")] = False,
    confirm_remote: Annotated[
        bool,
        typer.Option("--confirm-remote", help="Confirm DashScope embedding calls for image queries."),
    ] = False,
) -> None:
    require_remote_confirmation(confirm_remote, "generate-image-reports calls DashScope embeddings")
    config = runtime_config()
    ensure_table_matches_config(config)
    inputs = load_report_manifest_inputs(
        manifest,
        resource_id=resource_id,
        resource_file=resource_file,
        limit=limit,
        all_resources=all_resources,
    )
    without_public_url = [item.resource_id for item in inputs if not item.public_url]
    if without_public_url:
        raise typer.BadParameter(f"manifest resources have no public_url: {', '.join(without_public_url[:10])}")
    query_ids = [item.resource_id for item in inputs]
    indexed_ids = set(fetch_existing_ids(config, query_ids))
    missing = [item for item in query_ids if item not in indexed_ids]
    if missing:
        raise typer.BadParameter(f"query resources are not indexed: {', '.join(missing[:10])}")
    candidates = load_candidates(candidate_resource_id, candidate_file)
    client = DashScopeMultimodalClient(config)
    generated: list[dict[str, object]] = []
    failed: list[dict[str, object]] = []

    if not as_json:
        typer.echo(f"mode=image requested={len(inputs)} reports_dir={reports_dir}", err=True)
    for index, item in enumerate(inputs, start=1):
        if not item.public_url:
            raise typer.BadParameter(f"manifest resource has no public_url: {item.resource_id}")
        if not as_json:
            typer.echo(f"[{index}/{len(inputs)}] START {display_resource_id(item.resource_id)}", err=True)
        started_at = monotonic()
        try:
            embedding = client.embed({"image": item.public_url})
            results = search_by_embedding(
                config,
                embedding,
                top_k=top_k,
                excluded=set(exclude_resource_id or ()),
                candidates=candidates,
            )
            report_path = reports_dir / f"search-image-{report_slug(item.resource_id)}.html"
            report_index = write_html_report(
                report_path,
                query_label=item.resource_id,
                query_image_url=item.public_url,
                results=results,
            )
            generated.append(
                {
                    "resource_id": item.resource_id,
                    "query_url": item.public_url,
                    "html_report": str(report_path),
                    "html_report_index": str(report_index),
                    "result_count": len(results),
                }
            )
            if not as_json:
                typer.echo(
                    f"[{index}/{len(inputs)}] OK {display_resource_id(item.resource_id)} "
                    f"elapsed={format_seconds(monotonic() - started_at)}",
                    err=True,
                )
        except Exception as exc:
            failed.append({"resource_id": item.resource_id, "error": str(exc)})
            if not as_json:
                typer.echo(
                    f"[{index}/{len(inputs)}] FAILED {display_resource_id(item.resource_id)} "
                    f"elapsed={format_seconds(monotonic() - started_at)} error={exc}",
                    err=True,
                )

    root_index = write_reports_root_index_for(reports_dir)
    payload: dict[str, object] = {
        "mode": "image",
        "requested": len(inputs),
        "generated": len(generated),
        "failed": len(failed),
        "reports_dir": str(reports_dir),
        "html_root_index": str(root_index) if root_index is not None else None,
        "items": generated,
        "failed_items": failed,
    }
    if as_json:
        print_json(payload)
    else:
        emit_batch_report_summary(payload)
    if failed:
        raise typer.Exit(1)


@APP.command("generate-text-reports")
def generate_text_reports(
    text: Annotated[
        list[str] | None,
        typer.Option("--text", help="Text query to report. Repeat for multiple text queries."),
    ] = None,
    text_file: Annotated[
        Path | None,
        typer.Option("--text-file", help="Text file containing one query text per line."),
    ] = None,
    top_k: Annotated[int, typer.Option("--top-k", help="Number of search results in each report.")] = 10,
    candidate_resource_id: Annotated[
        list[str] | None,
        typer.Option("--candidate-resource-id", help="Candidate resource IDs. Omit for POC full table search."),
    ] = None,
    candidate_file: Annotated[
        Path | None,
        typer.Option("--candidate-file", help="Text file containing one candidate resource_id per line."),
    ] = None,
    reports_dir: Annotated[
        Path,
        typer.Option("--reports-dir", help="Directory for generated text query HTML reports."),
    ] = DEFAULT_TEXT_REPORTS_DIR,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON only.")] = False,
    confirm_remote: Annotated[
        bool,
        typer.Option("--confirm-remote", help="Confirm DashScope embedding calls for text queries."),
    ] = False,
) -> None:
    require_remote_confirmation(confirm_remote, "generate-text-reports calls DashScope embeddings")
    config = runtime_config()
    ensure_table_matches_config(config)
    queries = load_text_queries(text, text_file)
    candidates = load_candidates(candidate_resource_id, candidate_file)
    client = DashScopeMultimodalClient(config)
    generated: list[dict[str, object]] = []
    failed: list[dict[str, object]] = []

    if not as_json:
        typer.echo(f"mode=text requested={len(queries)} reports_dir={reports_dir}", err=True)
    for index, query_text in enumerate(queries, start=1):
        if not as_json:
            typer.echo(f"[{index}/{len(queries)}] START {display_resource_id(query_text)}", err=True)
        started_at = monotonic()
        try:
            embedding = client.embed({"text": query_text})
            results = search_by_embedding(
                config,
                embedding,
                top_k=top_k,
                candidates=candidates,
            )
            report_path = reports_dir / f"search-text-{report_slug(query_text)}.html"
            report_index = write_html_report(
                report_path,
                query_label=query_text,
                query_image_url=None,
                results=results,
            )
            generated.append(
                {
                    "text": query_text,
                    "html_report": str(report_path),
                    "html_report_index": str(report_index),
                    "result_count": len(results),
                }
            )
            if not as_json:
                typer.echo(
                    f"[{index}/{len(queries)}] OK {display_resource_id(query_text)} "
                    f"elapsed={format_seconds(monotonic() - started_at)}",
                    err=True,
                )
        except Exception as exc:
            failed.append({"text": query_text, "error": str(exc)})
            if not as_json:
                typer.echo(
                    f"[{index}/{len(queries)}] FAILED {display_resource_id(query_text)} "
                    f"elapsed={format_seconds(monotonic() - started_at)} error={exc}",
                    err=True,
                )

    root_index = write_reports_root_index_for(reports_dir)
    payload: dict[str, object] = {
        "mode": "text",
        "requested": len(queries),
        "generated": len(generated),
        "failed": len(failed),
        "reports_dir": str(reports_dir),
        "html_root_index": str(root_index) if root_index is not None else None,
        "items": generated,
        "failed_items": failed,
    }
    if as_json:
        print_json(payload)
    else:
        emit_batch_report_summary(payload)
    if failed:
        raise typer.Exit(1)


@APP.command("list-assets")
def list_assets(
    limit: Annotated[int, typer.Option("--limit", help="Max rows to print.")] = 100,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON only.")] = False,
) -> None:
    config = runtime_config()
    sql = f"""
        SELECT resource_id, group_id, public_url, local_path, text_payload, embedding_input_kind,
               source_hash, updated_at
        FROM {quote_ident(config.table)}
        ORDER BY updated_at DESC, resource_id ASC
        LIMIT %s
    """
    with connect(config) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (limit,))
            rows = [dict(row) for row in cur.fetchall()]
    for row in rows:
        row["updated_at"] = row["updated_at"].isoformat()
    if as_json:
        print_json({"items": rows})
        return
    for row in rows:
        typer.echo(
            f"{row['resource_id']}\t{row.get('group_id') or '-'}\t"
            f"{row['embedding_input_kind']}\t{row['updated_at']}\t{row.get('public_url') or '-'}"
        )


@APP.command("exists")
def exists_assets(
    resource_id: Annotated[
        list[str] | None,
        typer.Option("--resource-id", help="Resource ID to check. Repeat for multiple IDs."),
    ] = None,
    resource_file: Annotated[
        Path | None,
        typer.Option("--resource-file", help="Text file containing one resource_id per line."),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON only.")] = False,
) -> None:
    config = runtime_config()
    requested = load_required_resource_ids(resource_id, resource_file)
    present_set = set(fetch_existing_ids(config, requested))
    present = [item for item in requested if item in present_set]
    missing = [item for item in requested if item not in present_set]
    payload = {
        "requested": len(requested),
        "present": present,
        "missing": missing,
    }
    if as_json:
        print_json(payload)
        return
    typer.echo(f"requested={len(requested)} present={len(present)} missing={len(missing)}")
    typer.echo("present:")
    for item in present:
        typer.echo(f"  {item}")
    typer.echo("missing:")
    for item in missing:
        typer.echo(f"  {item}")


@APP.command("ids")
def list_ids(
    limit: Annotated[int, typer.Option("--limit", help="Max IDs to print.")] = 1000,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON only.")] = False,
) -> None:
    if limit <= 0:
        raise typer.BadParameter("--limit must be greater than 0")
    config = runtime_config()
    sql = f"""
        SELECT resource_id
        FROM {quote_ident(config.table)}
        ORDER BY resource_id ASC
        LIMIT %s
    """
    with connect(config) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (limit,))
            ids = [row[0] for row in cur.fetchall()]
    if as_json:
        print_json({"count": len(ids), "resource_ids": ids})
        return
    for item in ids:
        typer.echo(item)


@APP.command("batch-delete")
def batch_delete(
    resource_id: Annotated[
        list[str] | None,
        typer.Option("--resource-id", help="Resource ID to delete. Repeat for multiple IDs."),
    ] = None,
    resource_file: Annotated[
        Path | None,
        typer.Option("--resource-file", help="Text file containing one resource_id per line."),
    ] = None,
    confirm_delete: Annotated[
        bool,
        typer.Option("--confirm-delete", help="Confirm deletion from the POC vector table."),
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON only.")] = False,
) -> None:
    if not confirm_delete:
        raise typer.BadParameter("batch-delete removes rows from the POC vector table; pass --confirm-delete to run it")
    config = runtime_config()
    requested = load_required_resource_ids(resource_id, resource_file)
    present_before = set(fetch_existing_ids(config, requested))
    sql = f"DELETE FROM {quote_ident(config.table)} WHERE resource_id = ANY(%s)"
    with connect(config) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (requested,))
            deleted_count = cur.rowcount
        conn.commit()
    deleted = [item for item in requested if item in present_before]
    missing = [item for item in requested if item not in present_before]
    payload = {
        "requested": len(requested),
        "deleted": deleted_count,
        "deleted_resource_ids": deleted,
        "missing_resource_ids": missing,
    }
    if as_json:
        print_json(payload)
        return
    typer.echo(f"requested={len(requested)} deleted={deleted_count} missing={len(missing)}")


def load_effective_env(env_file: Path | None) -> dict[str, str]:
    explicit = env_file is not None
    selected = env_file or Path(os.environ.get("ENV_FILE", ".env"))
    if not selected.is_absolute():
        selected = REPO_ROOT / selected
    required_source = explicit or "ENV_FILE" in os.environ
    if required_source and not selected.exists():
        raise ValueError(f"env file does not exist: {selected}")

    loaded: dict[str, str] = {}
    if selected.exists():
        for key, value in dotenv_values(selected).items():
            if value is not None:
                loaded[key] = value
    if explicit or "ENV_FILE" in os.environ:
        return {**os.environ, **loaded}
    return {**loaded, **os.environ}


def build_runtime_config(env: dict[str, str], *, model: str, dimension: int, table: str) -> RuntimeConfig:
    database_url = env.get("DATABASE_URL", "")
    if not database_url:
        raise ValueError("DATABASE_URL is required")
    api_key = env.get("DASHSCOPE_API_KEY", "").strip() or None
    base_url = (
        env.get("POC_DASHSCOPE_BASE_URL")
        or env.get("DASHSCOPE_NATIVE_BASE_URL")
        or env.get("DASHSCOPE_API_HOST")
        or DEFAULT_DASHSCOPE_BASE_URL
    )
    base_url = base_url.strip().rstrip("/") if base_url else None
    if dimension <= 0:
        raise ValueError("dimension must be greater than 0")
    if dimension != DEFAULT_DIMENSION:
        raise ValueError(f"this POC table is fixed to vector({DEFAULT_DIMENSION}); dimension must be {DEFAULT_DIMENSION}")
    if table != quote_ident(table):
        raise ValueError("table must be a simple PostgreSQL identifier")
    return RuntimeConfig(
        env=env,
        database_url=normalize_database_url(database_url),
        dashscope_api_key=api_key,
        dashscope_base_url=base_url,
        model=model,
        dimension=dimension,
        table=table,
        image_max_bytes=int(env.get("POC_ASSET_VECTOR_IMAGE_MAX_BYTES", "10485760")),
    )


def normalize_dashscope_base_url(value: str) -> str:
    base_url = value.strip().rstrip("/")
    if not base_url:
        raise ValueError("DashScope native base URL is required")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("DashScope native base URL must be an absolute HTTPS URL")
    if "compatible-mode" in parsed.path:
        raise ValueError("DashScope multimodal embeddings require native api/v1, not compatible-mode/v1")
    return base_url


def normalize_database_url(value: str) -> str:
    if value.startswith("postgresql+asyncpg://"):
        return "postgresql://" + value.removeprefix("postgresql+asyncpg://")
    return value


def runtime_config() -> RuntimeConfig:
    config = _CONTEXT.get("config")
    if not isinstance(config, RuntimeConfig):
        raise RuntimeError("runtime config was not initialized")
    return config


def require_remote_confirmation(confirmed: bool, action: str) -> None:
    if not confirmed:
        raise typer.BadParameter(f"{action}; pass --confirm-remote to run it")


def connect(config: RuntimeConfig):
    return psycopg2.connect(config.database_url)


def create_schema(conn, table: str) -> None:
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {quote_ident(table)} (
              resource_id text PRIMARY KEY,
              group_id text,
              public_url text,
              local_path text,
              text_payload text,
              embedding vector(768) NOT NULL,
              vector_kind text NOT NULL DEFAULT 'asset',
              embedding_input_kind text NOT NULL,
              model_id text NOT NULL,
              dimension integer NOT NULL,
              source_hash text NOT NULL,
              image_sha256 text,
              updated_at timestamptz NOT NULL DEFAULT now(),
              CHECK (vector_kind = 'asset'),
              CHECK (embedding_input_kind IN ('image', 'text', 'image_text')),
              CHECK (dimension = 768)
            )
            """
        )
        cur.execute(
            f"""
            CREATE INDEX IF NOT EXISTS {quote_ident(table + '_embedding_hnsw')}
            ON {quote_ident(table)}
            USING hnsw (embedding vector_cosine_ops)
            """
        )
    conn.commit()


def scan_image_dir(
    image_dir: Path,
    *,
    text_map: Path | None,
    group_map: Path | None,
    limit: int | None,
) -> list[AssetInput]:
    if not image_dir.is_dir():
        raise typer.BadParameter(f"image directory does not exist: {image_dir}")
    texts = read_json_mapping(text_map)
    groups = read_json_mapping(group_map)
    paths = sorted(path for path in image_dir.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES)
    if limit is not None:
        if limit <= 0:
            raise typer.BadParameter("--limit must be greater than 0")
        paths = paths[:limit]
    return [
        AssetInput(
            resource_id=path.stem,
            group_id=groups.get(path.stem),
            local_path=path,
            public_url=None,
            text_payload=texts.get(path.stem),
            sha256=None,
            content_type=None,
        )
        for path in paths
    ]


def read_json_mapping(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise typer.BadParameter(f"mapping file must be a JSON object: {path}")
    result: dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise typer.BadParameter(f"mapping keys and values must be strings: {path}")
        result[key] = value
    return result


def read_manifest(path: Path) -> list[AssetInput]:
    if not path.is_file():
        raise typer.BadParameter(f"manifest does not exist: {path}")
    items: list[AssetInput] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        raw = json.loads(line)
        if not isinstance(raw, dict):
            raise typer.BadParameter(f"manifest line {line_no} must be a JSON object")
        resource_id = required_str(raw, "resource_id", line_no)
        local_path = raw.get("local_path")
        image = raw.get("image") if isinstance(raw.get("image"), dict) else {}
        public_url = raw.get("public_url") or image.get("public_url")
        if public_url is not None and not isinstance(public_url, str):
            raise typer.BadParameter(f"manifest line {line_no} public_url must be a string")
        text_payload = raw.get("text_payload")
        if text_payload is not None and not isinstance(text_payload, str):
            raise typer.BadParameter(f"manifest line {line_no} text_payload must be a string")
        group_id = raw.get("group_id")
        if group_id is not None and not isinstance(group_id, str):
            raise typer.BadParameter(f"manifest line {line_no} group_id must be a string")
        if local_path is not None and not isinstance(local_path, str):
            raise typer.BadParameter(f"manifest line {line_no} local_path must be a string")
        sha256 = raw.get("sha256") or image.get("sha256")
        if sha256 is not None and not isinstance(sha256, str):
            raise typer.BadParameter(f"manifest line {line_no} sha256 must be a string")
        content_type = raw.get("content_type") or image.get("content_type")
        if content_type is not None and not isinstance(content_type, str):
            raise typer.BadParameter(f"manifest line {line_no} content_type must be a string")
        if not public_url and not local_path and not text_payload:
            raise typer.BadParameter(f"manifest line {line_no} must include public_url, local_path, or text_payload")
        items.append(
            AssetInput(
                resource_id=resource_id,
                group_id=group_id,
                local_path=Path(local_path) if local_path else None,
                public_url=public_url,
                text_payload=text_payload,
                sha256=sha256,
                content_type=content_type,
            )
        )
    return items


def required_str(raw: dict[str, Any], field: str, line_no: int) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise typer.BadParameter(f"manifest line {line_no} {field} must be a non-empty string")
    return value.strip()


class IndexProgress:
    def __init__(
        self,
        *,
        total: int,
        enabled: bool,
        table: str,
        model: str,
        verify_public_url: bool,
        upload_local: bool,
    ) -> None:
        self.total = total
        self.enabled = enabled
        self.table = table
        self.model = model
        self.verify_public_url = verify_public_url
        self.upload_local = upload_local
        self.started_at = monotonic()

    def start(self) -> None:
        if not self.enabled:
            return
        typer.echo(
            "index progress: "
            f"total={self.total} table={self.table} model={self.model} "
            f"verify_public_url={str(self.verify_public_url).lower()} "
            f"upload_local={str(self.upload_local).lower()}",
            err=True,
        )

    def item_start(self, index: int, item: AssetInput) -> None:
        if not self.enabled:
            return
        typer.echo(f"[{index}/{self.total}] START {display_resource_id(item.resource_id)}", err=True)

    def item_ok(self, index: int, item: AssetInput, result: dict[str, object], *, elapsed: float) -> None:
        if not self.enabled:
            return
        input_kind = result.get("embedding_input_kind") or "-"
        uploaded = result.get("uploaded") is not None
        typer.echo(
            f"[{index}/{self.total}] OK {display_resource_id(item.resource_id)} "
            f"input={input_kind} uploaded={str(uploaded).lower()} elapsed={format_seconds(elapsed)}",
            err=True,
        )

    def item_failed(self, index: int, item: AssetInput, exc: Exception, *, elapsed: float) -> None:
        if not self.enabled:
            return
        typer.echo(
            f"[{index}/{self.total}] FAILED {display_resource_id(item.resource_id)} "
            f"elapsed={format_seconds(elapsed)} error={exc}",
            err=True,
        )

    def done(self, *, indexed: int, failed: int) -> None:
        if not self.enabled:
            return
        typer.echo(
            f"index progress: DONE indexed={indexed} failed={failed} elapsed={format_seconds(monotonic() - self.started_at)}",
            err=True,
        )


def display_resource_id(value: str, *, max_chars: int = 120) -> str:
    if len(value) <= max_chars:
        return value
    head = max_chars // 2 - 2
    tail = max_chars - head - 5
    return f"{value[:head]} ... {value[-tail:]}"


def format_seconds(value: float) -> str:
    if value < 10:
        return f"{value:.2f}s"
    return f"{value:.1f}s"


def index_inputs(
    conn,
    config: RuntimeConfig,
    *,
    storage: AssetVectorPocStorageAdapter,
    inputs: list[AssetInput],
    oss_prefix: str,
    upload_local: bool = True,
    verify_public_url: bool,
    show_progress: bool,
) -> dict[str, object]:
    client = DashScopeMultimodalClient(config)
    indexed: list[dict[str, object]] = []
    failed: list[dict[str, object]] = []
    progress = IndexProgress(
        total=len(inputs),
        enabled=show_progress,
        table=config.table,
        model=config.model,
        verify_public_url=verify_public_url,
        upload_local=upload_local,
    )
    progress.start()
    for index, item in enumerate(inputs, start=1):
        progress.item_start(index, item)
        started_at = monotonic()
        try:
            result = index_one(
                conn,
                config,
                storage=storage,
                client=client,
                item=item,
                oss_prefix=oss_prefix,
                upload_local=upload_local,
                verify_public_url=verify_public_url,
            )
            indexed.append(result)
            progress.item_ok(index, item, result, elapsed=monotonic() - started_at)
        except Exception as exc:
            failed.append({"resource_id": item.resource_id, "error": str(exc)})
            progress.item_failed(index, item, exc, elapsed=monotonic() - started_at)
    progress.done(indexed=len(indexed), failed=len(failed))
    return {
        "total": len(inputs),
        "indexed": len(indexed),
        "failed": len(failed),
        "items": indexed,
        "failed_items": failed,
    }


def fail_if_index_failed(summary: dict[str, object]) -> None:
    failed = summary.get("failed")
    if isinstance(failed, int) and failed > 0:
        raise typer.Exit(1)


def index_one(
    conn,
    config: RuntimeConfig,
    *,
    storage: AssetVectorPocStorageAdapter,
    client: "DashScopeMultimodalClient",
    item: AssetInput,
    oss_prefix: str,
    upload_local: bool,
    verify_public_url: bool,
) -> dict[str, object]:
    public_url = item.public_url
    local_path = item.local_path
    uploaded: dict[str, object] | None = None
    image_sha256 = item.sha256
    if public_url is None and local_path is not None:
        if not upload_local:
            raise ValueError(f"resource {item.resource_id} has local_path but upload_local is disabled")
        result = storage.upload_local_image(
            local_path,
            key=f"{oss_prefix.strip('/')}/{item.resource_id}{local_path.suffix.lower()}",
        )
        uploaded = uploaded_asset_to_dict(result)
        public_url = result.public_url
        image_sha256 = result.sha256
    if public_url is not None and verify_public_url:
        storage.verify_public_image(public_url, sha256=image_sha256, max_bytes=config.image_max_bytes)
    content = embedding_content(public_url=public_url, text_payload=item.text_payload)
    embedding_input_kind = embedding_input_kind_for(content)
    embedding = client.embed(content)
    if len(embedding) != config.dimension:
        raise ValueError(f"embedding dimension mismatch: expected {config.dimension}, got {len(embedding)}")
    source_hash = source_hash_for(
        model=config.model,
        dimension=config.dimension,
        public_url=public_url,
        text_payload=item.text_payload,
        image_sha256=image_sha256,
    )
    upsert_asset(
        conn,
        config,
        resource_id=item.resource_id,
        group_id=item.group_id,
        public_url=public_url,
        local_path=str(local_path) if local_path is not None else None,
        text_payload=item.text_payload,
        embedding=embedding,
        embedding_input_kind=embedding_input_kind,
        source_hash=source_hash,
        image_sha256=image_sha256,
    )
    return {
        "resource_id": item.resource_id,
        "group_id": item.group_id,
        "public_url": public_url,
        "embedding_input_kind": embedding_input_kind,
        "source_hash": source_hash,
        "uploaded": uploaded,
    }


def embedding_content(*, public_url: str | None, text_payload: str | None) -> dict[str, str]:
    content: dict[str, str] = {}
    if text_payload:
        content["text"] = text_payload
    if public_url:
        content["image"] = public_url
    if not content:
        raise ValueError("image public_url or text_payload is required")
    return content


def embedding_input_kind_for(content: dict[str, str]) -> str:
    if "image" in content and "text" in content:
        return "image_text"
    if "image" in content:
        return "image"
    if "text" in content:
        return "text"
    raise ValueError("embedding content is empty")


def upsert_asset(
    conn,
    config: RuntimeConfig,
    *,
    resource_id: str,
    group_id: str | None,
    public_url: str | None,
    local_path: str | None,
    text_payload: str | None,
    embedding: list[float],
    embedding_input_kind: str,
    source_hash: str,
    image_sha256: str | None,
) -> None:
    sql = f"""
        INSERT INTO {quote_ident(config.table)} (
          resource_id, group_id, public_url, local_path, text_payload, embedding,
          vector_kind, embedding_input_kind, model_id, dimension, source_hash,
          image_sha256, updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s::vector, 'asset', %s, %s, %s, %s, %s, now())
        ON CONFLICT (resource_id) DO UPDATE SET
          group_id = EXCLUDED.group_id,
          public_url = EXCLUDED.public_url,
          local_path = EXCLUDED.local_path,
          text_payload = EXCLUDED.text_payload,
          embedding = EXCLUDED.embedding,
          vector_kind = EXCLUDED.vector_kind,
          embedding_input_kind = EXCLUDED.embedding_input_kind,
          model_id = EXCLUDED.model_id,
          dimension = EXCLUDED.dimension,
          source_hash = EXCLUDED.source_hash,
          image_sha256 = EXCLUDED.image_sha256,
          updated_at = now()
    """
    with conn.cursor() as cur:
        cur.execute(
            sql,
            (
                resource_id,
                group_id,
                public_url,
                local_path,
                text_payload,
                vector_literal(embedding),
                embedding_input_kind,
                config.model,
                config.dimension,
                source_hash,
                image_sha256,
            ),
        )
    conn.commit()


class DashScopeMultimodalClient:
    def __init__(self, config: RuntimeConfig) -> None:
        if not config.dashscope_api_key:
            raise typer.BadParameter("DASHSCOPE_API_KEY is required for DashScope calls")
        try:
            self.base_url = normalize_dashscope_base_url(config.dashscope_base_url or "")
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        self.config = config

    def embed(self, content: dict[str, str]) -> list[float]:
        payload = {
            "model": self.config.model,
            "input": {"contents": [content]},
            "parameters": {"dimension": self.config.dimension},
        }
        endpoint = f"{self.base_url}/services/embeddings/multimodal-embedding/multimodal-embedding"
        with httpx.Client(timeout=60) as client:
            response = client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {self.config.dashscope_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if response.status_code >= 400:
            raise RuntimeError(f"DashScope embedding failed: status={response.status_code} body={response.text[:500]}")
        data = response.json()
        return extract_embedding(data)


def extract_embedding(data: dict[str, Any]) -> list[float]:
    output = data.get("output")
    if not isinstance(output, dict):
        raise RuntimeError("DashScope response output must be an object")
    embeddings = output.get("embeddings")
    if isinstance(embeddings, list) and embeddings:
        first = embeddings[0]
        if isinstance(first, dict) and isinstance(first.get("embedding"), list):
            return normalize_embedding(first["embedding"])
    if isinstance(output.get("embedding"), list):
        return normalize_embedding(output["embedding"])
    raise RuntimeError(f"DashScope response does not contain embeddings: output_keys={sorted(output)}")


def normalize_embedding(value: list[Any]) -> list[float]:
    result: list[float] = []
    for index, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, int | float):
            raise RuntimeError(f"embedding item {index} must be a number")
        result.append(float(item))
    return result


def search_by_embedding(
    config: RuntimeConfig,
    embedding: list[float],
    *,
    top_k: int,
    excluded: set[str] | None = None,
    candidates: list[str] | None = None,
) -> list[SearchResult]:
    if top_k <= 0:
        raise typer.BadParameter("--top-k must be greater than 0")
    excluded = excluded or set()
    where = ["vector_kind = 'asset'"]
    params: list[Any] = [vector_literal(embedding)]
    if excluded:
        where.append("NOT (resource_id = ANY(%s))")
        params.append(list(excluded))
    if candidates:
        where.append("resource_id = ANY(%s)")
        params.append(candidates)
    params.append(top_k)
    sql = f"""
        SELECT resource_id, group_id, public_url, local_path, text_payload,
               embedding_input_kind, source_hash, updated_at,
               1 - (embedding <=> %s::vector) AS score
        FROM {quote_ident(config.table)}
        WHERE {" AND ".join(where)}
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """
    params.insert(-1, vector_literal(embedding))
    with connect(config) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
    return [
        SearchResult(
            resource_id=row["resource_id"],
            group_id=row["group_id"],
            score=float(row["score"]),
            public_url=row["public_url"],
            local_path=row["local_path"],
            text_payload=row["text_payload"],
            embedding_input_kind=row["embedding_input_kind"],
            source_hash=row["source_hash"],
            updated_at=row["updated_at"].isoformat(),
        )
        for row in rows
    ]


def ensure_table_matches_config(config: RuntimeConfig) -> None:
    sql = f"""
        SELECT model_id, dimension, count(*) AS row_count
        FROM {quote_ident(config.table)}
        WHERE vector_kind = 'asset'
        GROUP BY model_id, dimension
        ORDER BY model_id, dimension
    """
    with connect(config) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            rows = [dict(row) for row in cur.fetchall()]
    if not rows:
        raise typer.BadParameter(f"no indexed resources found in table {config.table}; run index-manifest first")
    if len(rows) != 1:
        details = ", ".join(f"{row['model_id']}/dimension={row['dimension']} rows={row['row_count']}" for row in rows)
        raise typer.BadParameter(f"vector table contains mixed models or dimensions: {details}")
    row = rows[0]
    if row["model_id"] != config.model or row["dimension"] != config.dimension:
        raise typer.BadParameter(
            "vector table config mismatch: "
            f"table={config.table} has model={row['model_id']} dimension={row['dimension']}; "
            f"current config uses model={config.model} dimension={config.dimension}. "
            "Use the matching --model/--table or re-index into a clean table."
        )


def get_resource_embedding(config: RuntimeConfig, resource_id: str) -> list[float]:
    sql = f"SELECT embedding::text AS embedding FROM {quote_ident(config.table)} WHERE resource_id = %s"
    with connect(config) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (resource_id,))
            row = cur.fetchone()
    if row is None:
        raise typer.BadParameter(f"resource_id is not indexed: {resource_id}")
    return parse_vector_text(row[0])


def get_resource_public_url(config: RuntimeConfig, resource_id: str) -> str | None:
    sql = f"SELECT public_url FROM {quote_ident(config.table)} WHERE resource_id = %s"
    with connect(config) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (resource_id,))
            row = cur.fetchone()
    return row[0] if row else None


def parse_vector_text(value: str) -> list[float]:
    if not value.startswith("[") or not value.endswith("]"):
        raise RuntimeError("pgvector text output must be bracketed")
    body = value[1:-1].strip()
    if not body:
        return []
    return [float(item) for item in body.split(",")]


def emit_search_results(
    results: list[SearchResult],
    *,
    query_label: str,
    query_image_url: str | None,
    html_report: Path | None,
    as_json: bool,
) -> None:
    payload = {"query": query_label, "results": [result.to_dict() for result in results]}
    html_report_index: Path | None = None
    if html_report is not None:
        html_report_index = write_html_report(
            html_report,
            query_label=query_label,
            query_image_url=query_image_url,
            results=results,
        )
        payload["html_report"] = str(html_report)
        payload["html_report_index"] = str(html_report_index)
    if as_json:
        print_json(payload)
        return
    typer.echo(f"query: {query_label}")
    if html_report is not None:
        typer.echo(f"html_report: {html_report}")
    if html_report_index is not None:
        typer.echo(f"html_report_index: {html_report_index}")
    typer.echo("rank\tscore\tresource_id\tgroup_id\tinput\tpublic_url")
    for index, result in enumerate(results, start=1):
        typer.echo(
            f"{index}\t{result.score:.6f}\t{result.resource_id}\t{result.group_id or '-'}\t"
            f"{result.embedding_input_kind}\t{result.public_url or '-'}"
        )


def write_html_report(
    path: Path,
    *,
    query_label: str,
    query_image_url: str | None,
    results: list[SearchResult],
) -> Path:
    if path.name == "index.html":
        raise ValueError("--html-report index.html is reserved for the report directory index")
    path.parent.mkdir(parents=True, exist_ok=True)
    report_title = report_title_for_path(path, query_label=query_label)
    cards = []
    for index, result in enumerate(results, start=1):
        image = (
            f'<img src="{html.escape(result.public_url)}" alt="{html.escape(result.resource_id)}">'
            if result.public_url
            else '<div class="no-image">no image</div>'
        )
        cards.append(
            f"""
            <section class="card">
              <div class="rank">#{index} score={result.score:.6f}</div>
              {image}
              <div class="id">{html.escape(result.resource_id)}</div>
              <div class="meta">group={html.escape(result.group_id or "-")} input={html.escape(result.embedding_input_kind)}</div>
              <div class="text">{html.escape(result.text_payload or "")}</div>
            </section>
            """
        )
    query_image = (
        f'<img class="query-image" src="{html.escape(query_image_url)}" alt="query">'
        if query_image_url
        else ""
    )
    path.write_text(
        f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(report_title)}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #1f2937; }}
    .query {{ margin-bottom: 24px; }}
    .query-image {{ max-width: 240px; max-height: 180px; object-fit: contain; border: 1px solid #d1d5db; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 16px; }}
    .card {{ border: 1px solid #d1d5db; border-radius: 8px; padding: 12px; background: #fff; }}
    .card img {{ width: 100%; height: 180px; object-fit: contain; background: #f9fafb; }}
    .rank {{ font-weight: 700; margin-bottom: 8px; }}
    .id {{ margin-top: 8px; font-weight: 600; word-break: break-all; }}
    .meta {{ color: #6b7280; font-size: 12px; margin-top: 4px; }}
    .text {{ margin-top: 8px; font-size: 13px; }}
    .no-image {{ height: 180px; display: grid; place-items: center; background: #f3f4f6; color: #6b7280; }}
  </style>
</head>
<body>
  <main>
    <section class="query">
      <h1>{html.escape(report_title)}</h1>
      <div class="meta">file={html.escape(path.name)}</div>
      <p>{html.escape(query_label)}</p>
      {query_image}
    </section>
    <section class="grid">
      {"".join(cards)}
    </section>
  </main>
</body>
</html>
""",
        encoding="utf-8",
    )
    index_path = write_reports_index(path.parent)
    write_reports_root_index_for(path.parent)
    return index_path


def report_title_for_path(path: Path, *, query_label: str | None = None) -> str:
    if path.name in REPORT_TITLE_OVERRIDES:
        return REPORT_TITLE_OVERRIDES[path.name]

    stem = path.stem.replace("_", "-")
    if "query-image" in stem:
        prefix = "图片搜图"
    elif stem.endswith("-resource") or "resource" in stem:
        prefix = "资源搜相似图"
    elif stem.endswith("-text") or "text" in stem:
        prefix = "文字搜图"
    else:
        prefix = "搜索报告"

    if query_label and not query_label.startswith(("http://", "https://")):
        return f"{prefix}：{query_label}"
    return f"{prefix}：{path.stem}"


def report_index_title_for_dir(report_dir: Path) -> str:
    title = REPORT_MODE_DIRS.get(report_dir.name)
    if title:
        return f"{title}报告"
    return "Asset Vector POC 报告总览"


def write_reports_index(report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    index_path = report_dir / "index.html"
    index_title = report_index_title_for_dir(report_dir)
    report_paths = sorted(
        (path for path in report_dir.glob("*.html") if path.name != "index.html"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    cards = []
    for path in report_paths:
        stat = path.stat()
        modified_at = datetime.fromtimestamp(stat.st_mtime, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        report_title = html_title_from_report(path) or report_title_for_path(path)
        cards.append(
            f"""
            <section class="report-card">
              <header>
                <h2>{html.escape(report_title)}</h2>
                <div class="meta">file={html.escape(path.name)} · modified_at={modified_at} · size={stat.st_size} bytes</div>
                <a class="open-link" href="{html.escape(path.name)}">打开完整报告</a>
              </header>
              <iframe src="{html.escape(path.name)}" title="{html.escape(report_title)}" loading="lazy"></iframe>
            </section>
            """
        )
    report_cards = "\n".join(cards) if cards else '<div class="empty">暂无报告</div>'
    index_path.write_text(
        f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(index_title)}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #1f2937; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    h2 {{ margin: 0 0 6px; font-size: 18px; }}
    .meta {{ color: #6b7280; font-size: 13px; }}
    .summary {{ margin-bottom: 24px; }}
    .report-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(420px, 1fr)); gap: 20px; }}
    .report-card {{ border: 1px solid #d1d5db; border-radius: 8px; background: #fff; overflow: hidden; }}
    .report-card header {{ padding: 14px 16px; border-bottom: 1px solid #e5e7eb; }}
    .open-link {{ display: inline-block; margin-top: 8px; }}
    iframe {{ width: 100%; height: 520px; border: 0; background: #fff; }}
    a {{ color: #075985; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .empty {{ color: #6b7280; padding: 24px; border: 1px solid #e5e7eb; border-radius: 8px; }}
    @media (max-width: 640px) {{
      body {{ margin: 16px; }}
      .report-grid {{ grid-template-columns: 1fr; }}
      iframe {{ height: 460px; }}
    }}
  </style>
</head>
<body>
  <main>
    <section class="summary">
      <h1>{html.escape(index_title)}</h1>
      <div class="meta">reports={len(report_paths)} generated_at={datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")}</div>
    </section>
    <section class="report-grid">
      {report_cards}
    </section>
  </main>
</body>
</html>
""",
        encoding="utf-8",
    )
    return index_path


def html_title_from_report(path: Path) -> str | None:
    content = path.read_text(encoding="utf-8")
    start = content.find("<title>")
    end = content.find("</title>", start)
    if start < 0 or end < 0:
        return None
    title = html.unescape(content[start + len("<title>") : end].strip())
    return title or None


def write_reports_root_index_for(report_dir: Path) -> Path | None:
    try:
        relative = report_dir.resolve().relative_to(DEFAULT_HTML_REPORTS_DIR.resolve())
    except ValueError:
        return None
    if len(relative.parts) != 1 or relative.parts[0] not in REPORT_MODE_DIRS:
        return None
    return write_reports_root_index(DEFAULT_HTML_REPORTS_DIR)


def write_reports_root_index(root_dir: Path) -> Path:
    root_dir.mkdir(parents=True, exist_ok=True)
    index_path = root_dir / "index.html"
    cards = []
    for mode, title in REPORT_MODE_DIRS.items():
        mode_dir = root_dir / mode
        mode_index = mode_dir / "index.html"
        report_count = len([path for path in mode_dir.glob("*.html") if path.name != "index.html"]) if mode_dir.exists() else 0
        if mode_index.exists():
            href = f"{mode}/index.html"
            action = "打开报告入口"
        else:
            href = f"{mode}/"
            action = "目录暂无 index.html"
        cards.append(
            f"""
            <section class="report-card">
              <header>
                <h2>{html.escape(title)}</h2>
                <div class="meta">dir={html.escape(mode)}/ · reports={report_count}</div>
                <a class="open-link" href="{html.escape(href)}">{html.escape(action)}</a>
              </header>
            </section>
            """
        )
    index_path.write_text(
        f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Asset Vector POC HTML 报告入口</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #1f2937; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    h2 {{ margin: 0 0 6px; font-size: 18px; }}
    .meta {{ color: #6b7280; font-size: 13px; }}
    .summary {{ margin-bottom: 24px; }}
    .report-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 20px; }}
    .report-card {{ border: 1px solid #d1d5db; border-radius: 8px; background: #fff; overflow: hidden; }}
    .report-card header {{ padding: 14px 16px; }}
    .open-link {{ display: inline-block; margin-top: 8px; }}
    a {{ color: #075985; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <main>
    <section class="summary">
      <h1>Asset Vector POC HTML 报告入口</h1>
      <div class="meta">generated_at={datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")}</div>
    </section>
    <section class="report-grid">
      {"".join(cards)}
    </section>
  </main>
</body>
</html>
""",
        encoding="utf-8",
    )
    return index_path


def source_hash_for(
    *,
    model: str,
    dimension: int,
    public_url: str | None,
    text_payload: str | None,
    image_sha256: str | None,
) -> str:
    payload = {
        "dimension": dimension,
        "image_sha256": image_sha256,
        "model": model,
        "public_url": public_url,
        "text_payload": text_payload,
        "vector_kind": "asset",
    }
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{value:.12g}" for value in values) + "]"


def load_candidates(candidate_resource_id: list[str] | None, candidate_file: Path | None) -> list[str] | None:
    result: list[str] = []
    if candidate_resource_id:
        result.extend(candidate_resource_id)
    if candidate_file is not None:
        result.extend(line.strip() for line in candidate_file.read_text(encoding="utf-8").splitlines() if line.strip())
    return result or None


def load_report_resource_ids(
    config: RuntimeConfig,
    *,
    resource_id: list[str] | None,
    resource_file: Path | None,
    limit: int | None,
    all_resources: bool,
) -> list[str]:
    explicit_ids = bool(resource_id) or resource_file is not None
    selector_count = sum(1 for selected in (explicit_ids, limit is not None, all_resources) if selected)
    if selector_count != 1:
        raise typer.BadParameter("pass exactly one of --resource-id/--resource-file, --limit, or --all")
    if explicit_ids:
        return load_required_resource_ids(resource_id, resource_file)
    if limit is not None:
        if limit <= 0:
            raise typer.BadParameter("--limit must be greater than 0")
        return fetch_indexed_resource_ids(config, limit=limit)
    return fetch_indexed_resource_ids(config, limit=None)


def load_report_manifest_inputs(
    manifest: Path,
    *,
    resource_id: list[str] | None,
    resource_file: Path | None,
    limit: int | None,
    all_resources: bool,
) -> list[AssetInput]:
    inputs = read_manifest(manifest)
    if not inputs:
        raise typer.BadParameter(f"manifest is empty: {manifest}")
    explicit_ids = bool(resource_id) or resource_file is not None
    selector_count = sum(1 for selected in (explicit_ids, limit is not None, all_resources) if selected)
    if selector_count != 1:
        raise typer.BadParameter("pass exactly one of --resource-id/--resource-file, --limit, or --all")
    if explicit_ids:
        requested = load_required_resource_ids(resource_id, resource_file)
        by_id = {item.resource_id: item for item in inputs}
        missing = [item for item in requested if item not in by_id]
        if missing:
            raise typer.BadParameter(f"resource_id not found in manifest: {', '.join(missing[:10])}")
        return [by_id[item] for item in requested]
    if limit is not None:
        if limit <= 0:
            raise typer.BadParameter("--limit must be greater than 0")
        return inputs[:limit]
    return inputs


def load_text_queries(text: list[str] | None, text_file: Path | None) -> list[str]:
    result: list[str] = []
    if text:
        result.extend(text)
    if text_file is not None:
        result.extend(line.strip() for line in text_file.read_text(encoding="utf-8").splitlines() if line.strip())
    deduped = list(dict.fromkeys(item.strip() for item in result if item.strip()))
    if not deduped:
        raise typer.BadParameter("pass at least one --text or --text-file")
    return deduped


def emit_batch_report_summary(payload: dict[str, object]) -> None:
    mode = payload["mode"]
    typer.echo(
        f"mode={mode} requested={payload['requested']} generated={payload['generated']} failed={payload['failed']}"
    )
    typer.echo(f"reports_dir={payload['reports_dir']}")
    if payload.get("html_root_index") is not None:
        typer.echo(f"html_root_index={payload['html_root_index']}")
    items = payload.get("items")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            label = item.get("resource_id") or item.get("text") or "-"
            typer.echo(f"  {label} -> {item.get('html_report')}")
    failed_items = payload.get("failed_items")
    if isinstance(failed_items, list):
        for item in failed_items:
            if not isinstance(item, dict):
                continue
            label = item.get("resource_id") or item.get("text") or "-"
            typer.echo(f"  FAILED {label}: {item.get('error')}")


def load_required_resource_ids(resource_id: list[str] | None, resource_file: Path | None) -> list[str]:
    result: list[str] = []
    if resource_id:
        result.extend(resource_id)
    if resource_file is not None:
        result.extend(line.strip() for line in resource_file.read_text(encoding="utf-8").splitlines() if line.strip())
    deduped = list(dict.fromkeys(item.strip() for item in result if item.strip()))
    if not deduped:
        raise typer.BadParameter("pass at least one --resource-id or --resource-file")
    return deduped


def fetch_indexed_resource_ids(config: RuntimeConfig, *, limit: int | None) -> list[str]:
    params: tuple[Any, ...] = ()
    limit_clause = ""
    if limit is not None:
        limit_clause = "LIMIT %s"
        params = (limit,)
    sql = f"""
        SELECT resource_id
        FROM {quote_ident(config.table)}
        ORDER BY resource_id ASC
        {limit_clause}
    """
    with connect(config) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            ids = [row[0] for row in cur.fetchall()]
    if not ids:
        raise typer.BadParameter("no indexed resources found; run index-manifest first")
    return ids


def fetch_existing_ids(config: RuntimeConfig, resource_ids: list[str]) -> list[str]:
    sql = f"""
        SELECT resource_id
        FROM {quote_ident(config.table)}
        WHERE resource_id = ANY(%s)
    """
    with connect(config) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (resource_ids,))
            return [row[0] for row in cur.fetchall()]


def allowed_hosts_for_upload(env: dict[str, str]) -> tuple[str, ...]:
    if not has_oss_upload_env(env):
        return ()
    endpoint = env.get("OSS_PUBLIC_ENDPOINT", "").strip()
    if endpoint:
        parsed = urlsplit(endpoint if "://" in endpoint else f"https://{endpoint}")
        if not parsed.hostname:
            raise ValueError("OSS_PUBLIC_ENDPOINT must include host")
        return (parsed.hostname.lower(),)
    bucket = env["OSS_BUCKET"]
    endpoint = env.get("OSS_ENDPOINT") or f"oss-{env['OSS_REGION']}.aliyuncs.com"
    return (f"{bucket}.{endpoint}".lower(),)


def allowed_hosts_from_inputs(inputs: list[AssetInput], env: dict[str, str]) -> tuple[str, ...]:
    hosts = {public_host_from_url(item.public_url) for item in inputs if item.public_url}
    if any(item.local_path is not None and item.public_url is None for item in inputs):
        hosts.update(allowed_hosts_for_upload(env))
    if not hosts:
        hosts.add("example.com")
    return tuple(sorted(hosts))


def build_storage_for_manifest(
    env: dict[str, str],
    *,
    allowed_hosts: tuple[str, ...],
    upload_local: bool,
) -> AssetVectorPocStorageAdapter:
    needs_upload = upload_local and has_oss_upload_env(env)
    if needs_upload:
        return build_oss_adapter_from_env(env, allowed_hosts=allowed_hosts)
    return build_public_reader_adapter(env, allowed_hosts=allowed_hosts)


def has_oss_upload_env(env: dict[str, str]) -> bool:
    return all(
        bool(env.get(name))
        for name in (
            "OSS_BUCKET",
            "OSS_REGION",
            "OSS_ACCESS_KEY_ID",
            "OSS_ACCESS_KEY_SECRET",
            "OSS_PROJECT_ROOT",
        )
    )


def quote_ident(value: str) -> str:
    if not value.replace("_", "").isalnum() or value[0].isdigit():
        raise ValueError(f"invalid SQL identifier: {value}")
    return value


def print_json(value: object) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def report_slug(value: str) -> str:
    result = "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in value.strip())
    result = "-".join(part for part in result.split("-") if part)
    if not result:
        raise ValueError(f"cannot build report file name for resource_id: {value!r}")
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:10]
    return f"{result[:140]}-{digest}"


def timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def redact_database_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.password is None:
        return value
    auth = parsed.netloc.replace(f":{parsed.password}@", ":***@")
    return parsed._replace(netloc=auth).geturl()


if __name__ == "__main__":
    APP()

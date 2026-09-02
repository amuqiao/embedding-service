from __future__ import annotations

from collections.abc import Iterable
import uuid

import pytest
from sqlalchemy.dialects import postgresql

from app.business_packages.asset_vector.embedding_adapter import (
    AssetVectorEmbeddingConfig,
    DashScopeMultimodalEmbeddingAdapter,
    average_embeddings,
    extract_embedding,
    item_embedding_content,
    item_index_text,
    normalize_dashscope_native_base_url,
)
from app.business_packages.asset_vector.executor import (
    ASSET_VECTOR_BATCH_UPSERT_JOB_TYPE,
    ASSET_VECTOR_EMBED_ITEM_JOB_TYPE,
    ASSET_VECTOR_UPSERT_JOIN_JOB_TYPE,
    ASSET_VECTOR_UPSERT_JOIN_NODE_KEY,
    AssetVectorBatchDeleteJob,
    AssetVectorBatchUpsertJob,
    AssetVectorEmbedItemJob,
    AssetVectorUpsertJoinJob,
    register_asset_vector_workflow,
)
from app.business_packages.asset_vector.repository import delete_items, list_item_ids, search_by_vector, upsert_embedded_items
from app.business_packages.asset_vector.models import AssetVectorItem
from app.business_packages.asset_vector.router import _query_vector, search_asset_vectors
from app.business_packages.asset_vector.schemas import (
    AssetVectorBatchDeleteResult,
    AssetVectorBatchUpsertParams,
    AssetVectorBatchUpsertResult,
    AssetVectorEmbeddedItemResult,
    AssetVectorSearchResponse,
    AssetVectorSearchRequest,
)
from app.core.exceptions import AppError
from app.jobs.registry import register as register_job_executor
from app.models.job import Job
from app.services.job_runtime import build_runtime_snapshot, payload_hash, write_runtime_json
from app.workflows import clear_for_tests as clear_workflows_for_tests
from app.workflows import compile_registered_workflow


class _ScalarResult:
    def __init__(self, values: Iterable[object]) -> None:
        self._values = list(values)

    def all(self) -> list[object]:
        return list(self._values)


class _ExecuteResult:
    def __init__(self, rows: Iterable[object] = (), *, scalar_values: Iterable[object] | None = None) -> None:
        self._rows = list(rows)
        self._scalar_values = list(scalar_values or [])

    def __iter__(self):
        return iter(self._rows)

    def scalars(self) -> _ScalarResult:
        return _ScalarResult(self._scalar_values)


class _RecordingAsyncSession:
    def __init__(
        self,
        *,
        flush_error: Exception | None = None,
        scalar_values: Iterable[object] = (),
    ) -> None:
        self.flush_error = flush_error
        self.scalar_values = list(scalar_values)
        self.executed: list[object] = []
        self.flush_calls = 0

    async def execute(self, statement, params=None):
        self.executed.append((statement, params))
        return _ExecuteResult(scalar_values=self.scalar_values)

    async def flush(self) -> None:
        self.flush_calls += 1
        if self.flush_error is not None:
            raise self.flush_error


def _upsert_params() -> AssetVectorBatchUpsertParams:
    return AssetVectorBatchUpsertParams(
        items=[
            {
                "item_id": "asset_champagne",
                "item_name": "champagne bottle",
                "asset": {
                    "public_url": "https://example.com/assets/champagne.png",
                    "content_type": "image/png",
                },
                "labels": [
                    {
                        "label_id": "object_champagne",
                        "language": "en",
                        "label_name": "champagne",
                        "definition": "A champagne bottle for celebration scenes.",
                    }
                ],
            },
            {
                "item_id": "asset_apple",
                "item_name": "red apple",
                "asset": {
                    "public_url": "https://example.com/assets/apple.png",
                    "content_type": "image/png",
                },
                "labels": [
                    {
                        "label_id": "object_apple",
                        "language": "en",
                        "label_name": "apple",
                        "definition": "A red apple fruit item.",
                    }
                ],
            },
        ]
    )


def _embedding(value: float) -> list[float]:
    return [value] * 768


def _embedded_items() -> list[AssetVectorEmbeddedItemResult]:
    return [
        AssetVectorEmbeddedItemResult(
            item=item,
            embedding=_embedding(float(index + 1)),
            embedding_text=item_index_text(item),
            model_id="tongyi-embedding-vision-flash",
            dimension=768,
            input_sha256=f"{index:064x}",
        )
        for index, item in enumerate(_upsert_params().items)
    ]


def _delete_job(item_ids: list[str]) -> Job:
    job_params = {"item_ids": item_ids}
    job_params_hash = payload_hash(job_params)
    runtime_snapshot = build_runtime_snapshot(
        job_type="asset_vector_batch_delete",
        job_params_hash=job_params_hash,
        runtime_fields={"operation": "asset_vector_batch_delete", "item_count": len(item_ids)},
        output_target={
            "type": "oss_prefix",
            "oss_bucket": "bucket",
            "oss_prefix": "asset-vector/",
            "oss_region": "local",
        },
    )
    return Job(
        id=uuid.uuid4(),
        caller_id="caller-a",
        client_request_id="asset-vector-delete-test",
        job_type="asset_vector_batch_delete",
        job_params_ref=write_runtime_json(None, "job_params", job_params),
        job_params_hash=job_params_hash,
        runtime_ref=write_runtime_json(None, "runtime", runtime_snapshot),
    )


def test_asset_vector_item_embedding_content_uses_name_image_and_labels_without_stable_ids():
    params = _upsert_params()
    content = item_embedding_content(params.items[0])

    assert content["image"] == "https://example.com/assets/champagne.png"
    assert "champagne bottle" in content["text"]
    assert "A champagne bottle for celebration scenes." in content["text"]
    assert "asset_champagne" not in content["text"]
    assert "https://example.com/assets/champagne.png" not in content["text"]


def test_asset_vector_dashscope_base_url_normalizes_native_and_compatible_modes():
    assert (
        normalize_dashscope_native_base_url("https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
        == "https://dashscope-intl.aliyuncs.com/api/v1"
    )
    assert (
        normalize_dashscope_native_base_url("https://ws-iugft7wkq31tyi40.ap-southeast-1.maas.aliyuncs.com/api/v1/")
        == "https://ws-iugft7wkq31tyi40.ap-southeast-1.maas.aliyuncs.com/api/v1"
    )


def test_asset_vector_extract_embedding_rejects_dimension_mismatch():
    with pytest.raises(AppError) as exc_info:
        extract_embedding({"output": {"embeddings": [{"embedding": [0.1, 0.2]}]}}, expected_dimension=768)

    assert exc_info.value.code == "MODEL_OUTPUT_INVALID"


@pytest.mark.asyncio
async def test_asset_vector_dashscope_adapter_rejects_non_json_response(monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            raise ValueError("not json")

    class FakeAsyncClient:
        def __init__(self, *, timeout: int) -> None:
            assert timeout == 3

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url, *, headers, json):
            assert url.endswith("/services/embeddings/multimodal-embedding/multimodal-embedding")
            assert headers["Authorization"] == "Bearer test-key"
            assert json["model"] == "tongyi-embedding-vision-flash"
            return FakeResponse()

    monkeypatch.setattr(
        "app.business_packages.asset_vector.embedding_adapter.httpx.AsyncClient",
        FakeAsyncClient,
    )

    adapter = DashScopeMultimodalEmbeddingAdapter(
        AssetVectorEmbeddingConfig(
            api_key="test-key",
            base_url="https://dashscope.aliyuncs.com/api/v1",
            model_id="tongyi-embedding-vision-flash",
            dimension=768,
            timeout_seconds=3,
        )
    )
    with pytest.raises(AppError) as exc_info:
        await adapter.embed({"text": "gift"})

    assert exc_info.value.code == "MODEL_OUTPUT_INVALID"


def test_asset_vector_average_embeddings_normalizes_result():
    vector = average_embeddings([[1.0, 0.0], [1.0, 0.0]])

    assert vector == [1.0, 0.0]


def test_asset_vector_search_mode_rejects_mixed_text_request():
    with pytest.raises(ValueError, match="text search requires text only"):
        AssetVectorSearchRequest(
            search_mode="text",
            text={"query": "champagne"},
            item_ids=["asset_champagne"],
        )


def test_asset_vector_hybrid_requires_two_inputs():
    with pytest.raises(ValueError, match="hybrid search requires at least two query inputs"):
        AssetVectorSearchRequest(search_mode="hybrid", text={"query": "champagne"})


def test_asset_vector_search_accepts_empty_candidate_pool():
    request = AssetVectorSearchRequest(
        search_mode="text",
        text={"query": "champagne"},
        candidate_item_ids=[],
    )

    assert request.candidate_item_ids == []


def test_asset_vector_item_model_declares_business_table_shape():
    table = AssetVectorItem.__table__

    assert table.name == "asset_vector_items"
    assert {column.name for column in table.primary_key.columns} == {"caller_id", "item_id"}
    assert set(table.columns.keys()) == {
        "caller_id",
        "item_id",
        "item_name",
        "asset",
        "labels",
        "metadata",
        "embedding",
        "embedding_text",
        "embedding_model",
        "embedding_dimension",
        "input_sha256",
        "created_at",
        "updated_at",
    }
    assert table.indexes == set()


@pytest.mark.asyncio
async def test_asset_vector_search_by_vector_returns_empty_for_empty_candidate_pool():
    item_ids = await search_by_vector(
        object(),
        caller_id="caller-a",
        query_vector=[1.0],
        top_k=10,
        candidate_item_ids=[],
    )

    assert item_ids == []


@pytest.mark.asyncio
async def test_asset_vector_upsert_items_uses_business_package_model():
    db = _RecordingAsyncSession()

    result = await upsert_embedded_items(
        db,
        caller_id="caller-a",
        items=_embedded_items(),
    )

    assert [item_id for item_id, _indexed_at in result] == ["asset_champagne", "asset_apple"]
    assert len({indexed_at for _item_id, indexed_at in result}) == 1
    assert result[0][1].endswith("Z")
    assert db.flush_calls == 1
    assert len(db.executed) == 1

    statement, params = db.executed[0]
    assert isinstance(params, list)
    assert params[0]["embedding"].startswith("[")
    assert params[0]["embedding_model"] == "tongyi-embedding-vision-flash"
    sql = str(statement)
    assert "INSERT INTO asset_vector_items" in sql
    assert "CAST(:embedding AS vector)" in sql
    assert "ON CONFLICT (caller_id, item_id) DO UPDATE SET" in sql
    assert "metadata = EXCLUDED.metadata" in sql


@pytest.mark.asyncio
async def test_asset_vector_upsert_items_propagates_flush_failure():
    db = _RecordingAsyncSession(flush_error=RuntimeError("flush failed"))

    with pytest.raises(RuntimeError, match="flush failed"):
        await upsert_embedded_items(
            db,
            caller_id="caller-a",
            items=_embedded_items()[:1],
        )

    assert db.flush_calls == 1
    assert len(db.executed) == 1


@pytest.mark.asyncio
async def test_asset_vector_delete_job_result_marks_every_requested_item_deleted():
    result = await AssetVectorBatchDeleteJob()._execute(
        _delete_job(["asset-a", "asset-b"]),
        db=object(),
    )

    assert AssetVectorBatchDeleteResult.model_validate(result).model_dump() == {
        "schema_version": "default",
        "job_type": "asset_vector_batch_delete",
        "batch_summary": {"total": 2, "deleted": 2},
        "items": [
            {"item_id": "asset-a", "status": "deleted"},
            {"item_id": "asset-b", "status": "deleted"},
        ],
    }


@pytest.mark.asyncio
async def test_asset_vector_delete_items_flushes_direct_delete_without_pre_read():
    db = _RecordingAsyncSession()

    await delete_items(db, caller_id="caller-a", item_ids=["asset-a", "asset-b"])

    assert db.flush_calls == 1
    assert len(db.executed) == 1
    statement, params = db.executed[0]
    assert params is None
    compiled = statement.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"render_postcompile": True},
    )
    sql = str(compiled)
    assert "DELETE FROM asset_vector_items" in sql
    assert "asset_vector_items.caller_id =" in sql
    assert "asset_vector_items.item_id IN" in sql


@pytest.mark.asyncio
async def test_asset_vector_list_item_ids_uses_returned_next_cursor():
    db = _RecordingAsyncSession(scalar_values=["asset_001", "asset_002", "asset_003"])

    item_ids, next_cursor = await list_item_ids(
        db,
        caller_id="caller-a",
        limit=2,
        cursor="asset_000",
    )

    assert item_ids == ["asset_001", "asset_002"]
    assert next_cursor == "asset_002"
    assert len(db.executed) == 1
    statement, params = db.executed[0]
    assert params is None
    compiled = statement.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"render_postcompile": True},
    )
    sql = str(compiled)
    assert "asset_vector_items.item_id >" in sql
    assert "LIMIT" in sql


@pytest.mark.asyncio
async def test_asset_vector_query_item_ids_raise_when_seed_item_is_not_indexed(monkeypatch):
    async def fake_vectors_for_item_ids(db, *, caller_id: str, item_ids: list[str]) -> dict[str, list[float]]:
        assert caller_id == "caller-a"
        assert item_ids == ["missing-asset"]
        return {}

    monkeypatch.setattr(
        "app.business_packages.asset_vector.router.vectors_for_item_ids",
        fake_vectors_for_item_ids,
    )

    with pytest.raises(AppError) as exc_info:
        await _query_vector(
            AssetVectorSearchRequest(search_mode="item_ids", item_ids=["missing-asset"]),
            db=object(),
            caller_id="caller-a",
        )

    assert exc_info.value.code == "QUERY_ITEM_NOT_INDEXED"
    assert exc_info.value.details == {"item_ids": ["missing-asset"]}


@pytest.mark.asyncio
async def test_asset_vector_search_passes_caller_id_to_repository(monkeypatch):
    calls: dict[str, object] = {}

    class FakeAdapter:
        async def embed(self, content):
            calls["content"] = content
            return _embedding(1.0)

    async def fake_search_by_vector(db, *, caller_id: str, query_vector, top_k: int, candidate_item_ids):
        calls["caller_id"] = caller_id
        calls["query_vector"] = query_vector
        calls["top_k"] = top_k
        calls["candidate_item_ids"] = candidate_item_ids
        return ["asset-1"]

    monkeypatch.setattr(
        "app.business_packages.asset_vector.router.DashScopeMultimodalEmbeddingAdapter",
        FakeAdapter,
    )
    monkeypatch.setattr(
        "app.business_packages.asset_vector.router.search_by_vector",
        fake_search_by_vector,
    )

    response = await search_asset_vectors(
        AssetVectorSearchRequest(
            search_mode="text",
            text={"query": "champagne"},
            candidate_item_ids=["asset-1", "asset-2"],
            top_k=5,
        ),
        db=object(),
        caller_id="caller-a",
    )

    assert response == AssetVectorSearchResponse(item_ids=["asset-1"])
    assert calls == {
        "content": {"text": "champagne"},
        "caller_id": "caller-a",
        "query_vector": average_embeddings([_embedding(1.0)]),
        "top_k": 5,
        "candidate_item_ids": ["asset-1", "asset-2"],
    }


def test_asset_vector_workflow_compiles_one_child_per_item_and_join():
    register_job_executor(AssetVectorBatchUpsertJob())
    register_job_executor(AssetVectorEmbedItemJob())
    register_job_executor(AssetVectorUpsertJoinJob())
    clear_workflows_for_tests()
    register_asset_vector_workflow()

    params = _upsert_params()
    plan = compile_registered_workflow(ASSET_VECTOR_BATCH_UPSERT_JOB_TYPE, params.model_dump(exclude_none=True))
    nodes = {node["key"]: node for node in plan["nodes"]}

    assert plan["workflow_type"] == ASSET_VECTOR_BATCH_UPSERT_JOB_TYPE
    assert plan["node_count"] == 3
    assert nodes["item.0"]["job_type"] == ASSET_VECTOR_EMBED_ITEM_JOB_TYPE
    assert nodes["item.1"]["job_type"] == ASSET_VECTOR_EMBED_ITEM_JOB_TYPE
    assert nodes[ASSET_VECTOR_UPSERT_JOIN_NODE_KEY]["job_type"] == ASSET_VECTOR_UPSERT_JOIN_JOB_TYPE
    assert nodes[ASSET_VECTOR_UPSERT_JOIN_NODE_KEY]["depends_on"] == ["item.0", "item.1"]
    assert nodes[ASSET_VECTOR_UPSERT_JOIN_NODE_KEY]["job_params"]["item_ids"] == [
        "asset_champagne",
        "asset_apple",
    ]


def test_asset_vector_upsert_public_result_extracts_join_result_from_workflow_envelope():
    join_result = AssetVectorBatchUpsertResult(
        batch_summary={"total": 1, "succeeded": 1},
        items=[
            {
                "item_id": "asset_champagne",
                "status": "succeeded",
                "indexed": {"indexed_at": "2026-09-02T00:00:00Z"},
            }
        ],
    ).model_dump(exclude_none=True)
    canonical_result = {
        "schema_version": 1,
        "job_type": ASSET_VECTOR_BATCH_UPSERT_JOB_TYPE,
        "workflow": {
            "workflow_type": ASSET_VECTOR_BATCH_UPSERT_JOB_TYPE,
            "workflow_version": 1,
            "outcome": "success",
            "failure_policy": "fail_fast",
            "node_count": 2,
            "succeeded": 2,
            "failed": 0,
            "nodes": [
                {
                    "node_key": ASSET_VECTOR_UPSERT_JOIN_NODE_KEY,
                    "job_id": "join-job-id",
                    "job_type": ASSET_VECTOR_UPSERT_JOIN_JOB_TYPE,
                    "status": "succeeded",
                    "result": join_result,
                }
            ],
        },
    }

    assert AssetVectorBatchUpsertJob().public_result(canonical_result) == join_result

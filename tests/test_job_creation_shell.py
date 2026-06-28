import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core import config as config_module
from app.jobs.base import JobRetryPolicy
from app.models.job import Job
from app.schemas.jobs import CreateJobRequest
from app.core.exceptions import AppError, ValidationAppError
from app.services.jobs import _request_fingerprint, create_job, validate_create_contract
from app.workflows import WorkflowDefinition, chunks, task
from app.workflows import registry as workflow_registry


class _FakeDB:
    async def flush(self):
        pass


class _TestHandler:
    name = "test.echo"
    visibility = "public"
    role = "root"
    allow_callback = True
    timeout_seconds = 300
    max_attempts = 1
    retry_policy = JobRetryPolicy()

    def normalize_job_params(self, job_params):
        return job_params

    def validate_normalized_job_params(self, job_params):
        pass

    def runtime_job_fields(self, job_params):
        return {}

    def job_type_spec(self):
        return SimpleNamespace(
            job_type=self.name,
            visibility=self.visibility,
            role=self.role,
            execution_mode="custom_executor",
            retry_policy=self.retry_policy.snapshot(),
        )

    def effective_retry_policy(self):
        return self.retry_policy


def _job_settings(**overrides):
    values = {
        "ALLOW_INSECURE_CALLBACKS": False,
        "APP_ENV": "local",
        "CALLBACK_SIGNING_SECRET": "test-callback-secret",
        "CALLBACK_TIMEOUT_SECONDS": 5,
        "MAX_ACTIVE_JOBS": 5000,
        "MODEL_CALL_TIMEOUT_SECONDS": 300,
        "OSS_BUCKET": "",
        "OSS_INPUT_MAX_BYTES": 5_242_880,
        "OSS_REGION": "",
        "SERVICE_API_PREFIX": "/api/v1/ai-jobs",
    }
    values.update(overrides)
    return SimpleNamespace(
        runtime=config_module.RuntimeSettings(app_env=values["APP_ENV"]),
        service=config_module.ServiceSettings(api_prefix=values["SERVICE_API_PREFIX"]),
        storage=config_module.StorageSettings(
            oss_bucket=values["OSS_BUCKET"],
            oss_region=values["OSS_REGION"],
        ),
        callback=config_module.CallbackSettings(
            signing_secret=values["CALLBACK_SIGNING_SECRET"],
            allow_insecure_callbacks=values["ALLOW_INSECURE_CALLBACKS"],
            timeout_seconds=values["CALLBACK_TIMEOUT_SECONDS"],
        ),
        ai_provider=config_module.AIProviderSettings(
            model_call_timeout_seconds=values["MODEL_CALL_TIMEOUT_SECONDS"],
        ),
        job=config_module.JobSettings(
            max_active_jobs=values["MAX_ACTIVE_JOBS"],
            oss_input_max_bytes=values["OSS_INPUT_MAX_BYTES"],
        ),
    )


def _patch_job_settings(monkeypatch, **overrides) -> None:
    import app.services.jobs as jobs_module

    monkeypatch.setattr(jobs_module, "settings", _job_settings(**overrides))


@pytest.fixture(autouse=True)
def _public_callback_dns(monkeypatch):
    monkeypatch.setattr(
        "app.core.callback_security.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, "", ("93.184.216.34", 443))],
    )


@pytest.mark.asyncio
async def test_create_job_writes_shell_fields_without_legacy_shell_payload(monkeypatch):
    captured: dict = {}
    now = datetime.now(timezone.utc)

    async def fake_create(_db, **kwargs):
        captured.update(kwargs)
        return Job(
            id=uuid.uuid4(),
            caller_id=kwargs["caller_id"],
            client_request_id=kwargs["client_request_id"],
            job_type=kwargs["job_type"],
            status="queued",
            progress_percent=0,
            progress_text="已排队",
            callback_url=kwargs["callback_url"],
            callback_events=kwargs["callback_events"],
            metadata_=kwargs["metadata"],
            priority=kwargs["priority"],
            job_params_ref=kwargs["job_params_ref"],
            job_params_hash=kwargs["job_params_hash"],
            runtime_ref=kwargs["runtime_ref"],
            created_at=now,
            updated_at=now,
        )

    async def fake_advisory_lock(*_args):
        pass

    async def fake_get_recent(*_args, **_kwargs):
        return None

    async def fake_create_submission_key(_db, **kwargs):
        captured["submission_key"] = kwargs

    async def fake_create_initial_attempt(_db, created_job, *, timeout_seconds, purpose, retry_policy):
        captured["initial_attempt"] = (created_job.id, timeout_seconds, purpose, retry_policy)

    def fake_write_runtime_json(job, name, payload):
        owner_id = job.id if job is not None else "precreate"
        return {
            "storage": "oss_object",
            "type": "json",
            "oss_bucket": "bucket",
            "oss_key": f"ai-jobs/{owner_id}/runtime/{name}.json",
            "oss_region": "region",
            "payload_snapshot": payload,
        }

    _patch_job_settings(monkeypatch, MAX_ACTIVE_JOBS=0)
    monkeypatch.setattr("app.services.jobs.JobRepo.advisory_lock_for_client_request", fake_advisory_lock)
    monkeypatch.setattr("app.services.jobs.JobRepo.get_submission_by_client_request", fake_get_recent)
    monkeypatch.setattr("app.services.jobs.JobRepo.create", fake_create)
    monkeypatch.setattr("app.services.jobs.JobRepo.create_submission_key", fake_create_submission_key)
    monkeypatch.setattr("app.services.jobs.JobRepo.create_initial_attempt", fake_create_initial_attempt)
    monkeypatch.setattr("app.services.jobs.write_runtime_json", fake_write_runtime_json)
    monkeypatch.setattr("app.jobs.factory.get_job_executor", lambda _job_type: _TestHandler())

    payload = CreateJobRequest.model_validate(
        {
            "client_request_id": "req-1",
            "job_type": "test.echo",
            "job_params": {"value": {"hello": "world"}, "label": "Echo"},
            "callback": {"url": "https://example.com/callback"},
            "metadata": {"caller_task_id": "task-1"},
            "options": {"priority": "normal", "idempotency_mode": "reject_duplicate"},
        }
    )

    job, created = await create_job(_FakeDB(), payload, "caller-1")

    assert created is True
    assert captured["submission_key"]["request_fingerprint"].startswith("sha256:")
    assert captured["submission_key"]["job"] is job
    assert captured["submission_key"]["client_request_id"] == "req-1"
    assert "input_payload" not in captured
    assert "prompt_payload" not in captured
    assert "output_payload" not in captured
    assert "callback_payload" not in captured
    assert "options_payload" not in captured
    assert captured["metadata"] == {"caller_task_id": "task-1"}
    assert captured["priority"] == "normal"
    assert captured["initial_attempt"][0:3] == (job.id, 300, "business_execution")
    assert captured["callback_url"] == "https://example.com/callback"
    assert captured["callback_events"] == ["job.failed", "job.succeeded"]
    assert job.job_params_ref["payload_snapshot"] == {"value": {"hello": "world"}, "label": "Echo"}
    assert job.job_params_hash.startswith("sha256:")
    assert job.runtime_ref["payload_snapshot"]["schema_version"] == 1
    assert job.runtime_ref["payload_snapshot"]["job_type"] == "test.echo"
    assert job.runtime_ref["payload_snapshot"]["job_params_hash"] == job.job_params_hash
    assert job.runtime_ref["payload_snapshot"]["runtime_fields"] == {}
    assert job.runtime_ref["payload_snapshot"]["output_target"]["type"] == "oss_prefix"
    assert "workflow_plan" not in job.runtime_ref["payload_snapshot"]


@pytest.mark.asyncio
async def test_create_job_writes_registered_workflow_plan_to_runtime_ref(monkeypatch):
    captured: dict = {}
    now = datetime.now(timezone.utc)

    async def fake_create(_db, **kwargs):
        captured.update(kwargs)
        return Job(
            id=uuid.uuid4(),
            caller_id=kwargs["caller_id"],
            client_request_id=kwargs["client_request_id"],
            job_type=kwargs["job_type"],
            status="queued",
            progress_percent=0,
            progress_text="已排队",
            callback_url=kwargs["callback_url"],
            callback_events=kwargs["callback_events"],
            metadata_=kwargs["metadata"],
            priority=kwargs["priority"],
            job_params_ref=kwargs["job_params_ref"],
            job_params_hash=kwargs["job_params_hash"],
            runtime_ref=kwargs["runtime_ref"],
            created_at=now,
            updated_at=now,
        )

    async def fake_advisory_lock(*_args):
        pass

    async def fake_get_recent(*_args, **_kwargs):
        return None

    async def fake_create_submission_key(_db, **kwargs):
        captured["submission_key"] = kwargs

    async def fake_create_initial_attempt(_db, created_job, *, timeout_seconds, purpose, retry_policy):
        captured["initial_attempt"] = (created_job.id, timeout_seconds, purpose, retry_policy)

    def fake_write_runtime_json(_job, _name, payload):
        return {"storage": "db_inline", "type": "json", "name": _name, "payload_snapshot": payload}

    _patch_job_settings(monkeypatch, MAX_ACTIVE_JOBS=0)
    monkeypatch.setattr("app.services.jobs.JobRepo.advisory_lock_for_client_request", fake_advisory_lock)
    monkeypatch.setattr("app.services.jobs.JobRepo.get_submission_by_client_request", fake_get_recent)
    monkeypatch.setattr("app.services.jobs.JobRepo.create", fake_create)
    monkeypatch.setattr("app.services.jobs.JobRepo.create_submission_key", fake_create_submission_key)
    monkeypatch.setattr("app.services.jobs.JobRepo.create_initial_attempt", fake_create_initial_attempt)
    monkeypatch.setattr("app.services.jobs.write_runtime_json", fake_write_runtime_json)
    monkeypatch.setattr("app.jobs.factory.get_job_executor", lambda _job_type: _TestHandler())

    workflow_registry.clear_for_tests()

    def build_workflow(job_params):
        job_params["value"] = "mutated-in-builder"
        return task("first", "job_test_echo", {"value": job_params["value"]})

    workflow_registry.register(
        WorkflowDefinition(
            workflow_type="test.workflow",
            build=build_workflow,
            max_nodes=10,
        )
    )

    payload = CreateJobRequest.model_validate(
        {
            "client_request_id": "req-workflow-1",
            "job_type": "test.workflow",
            "job_params": {"value": "hello"},
            "metadata": {},
            "options": {"priority": "normal", "idempotency_mode": "reject_duplicate"},
        }
    )

    try:
        job, created = await create_job(_FakeDB(), payload, "caller-1")
    finally:
        workflow_registry.clear_for_tests()

    assert created is True
    assert captured["initial_attempt"][0:3] == (job.id, 300, "workflow_orchestration")
    assert job.job_params_ref["payload_snapshot"] == {"value": "hello"}
    assert job.runtime_ref["payload_snapshot"]["job_type"] == "test.workflow"
    plan = job.runtime_ref["payload_snapshot"]["workflow_plan"]
    assert plan["kind"] == "dag_lite"
    assert plan["workflow_type"] == "test.workflow"
    assert plan["nodes"][0]["job_params"] == {"value": "mutated-in-builder"}


@pytest.mark.asyncio
async def test_create_job_idempotent_existing_workflow_does_not_recompile(monkeypatch):
    existing = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id="req-workflow-1",
        job_type="test.workflow",
        status="queued",
        progress_percent=0,
        metadata_={},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    payload = CreateJobRequest.model_validate(
        {
            "client_request_id": "req-workflow-1",
            "job_type": "test.workflow",
            "job_params": {"value": "hello"},
            "metadata": {},
            "options": {"priority": "normal", "idempotency_mode": "return_existing"},
        }
    )
    request_fingerprint = _request_fingerprint(payload, "caller-1", {"value": "hello"})

    async def fake_advisory_lock(*_args):
        pass

    async def fake_get_recent(*_args, **_kwargs):
        return existing, SimpleNamespace(request_fingerprint=request_fingerprint)

    async def fail_create(*_args, **_kwargs):
        raise AssertionError("duplicate workflow request must not create a new job")

    _patch_job_settings(monkeypatch, MAX_ACTIVE_JOBS=0)
    monkeypatch.setattr("app.services.jobs.JobRepo.advisory_lock_for_client_request", fake_advisory_lock)
    monkeypatch.setattr("app.services.jobs.JobRepo.get_submission_by_client_request", fake_get_recent)
    monkeypatch.setattr("app.services.jobs.JobRepo.create", fail_create)
    monkeypatch.setattr("app.jobs.factory.get_job_executor", lambda _job_type: _TestHandler())

    workflow_registry.clear_for_tests()
    workflow_registry.register(
        WorkflowDefinition(
            workflow_type="test.workflow",
            build=lambda _params: (_ for _ in ()).throw(AssertionError("workflow must not compile")),
        )
    )

    try:
        job, created = await create_job(_FakeDB(), payload, "caller-1")
    finally:
        workflow_registry.clear_for_tests()

    assert job is existing
    assert created is False


@pytest.mark.asyncio
async def test_create_job_maps_invalid_workflow_plan_to_validation_error(monkeypatch):
    payload = CreateJobRequest.model_validate(
        {
            "client_request_id": "req-invalid-workflow-1",
            "job_type": "test.workflow",
            "job_params": {"items": [1, 2]},
            "metadata": {},
            "options": {"priority": "normal", "idempotency_mode": "reject_duplicate"},
        }
    )

    async def fake_advisory_lock(*_args):
        pass

    async def fake_get_recent(*_args, **_kwargs):
        return None

    async def fail_create(*_args, **_kwargs):
        raise AssertionError("invalid workflow plan must fail before job create")

    _patch_job_settings(monkeypatch, MAX_ACTIVE_JOBS=0)
    monkeypatch.setattr("app.services.jobs.JobRepo.advisory_lock_for_client_request", fake_advisory_lock)
    monkeypatch.setattr("app.services.jobs.JobRepo.get_submission_by_client_request", fake_get_recent)
    monkeypatch.setattr("app.services.jobs.JobRepo.create", fail_create)
    monkeypatch.setattr("app.jobs.factory.get_job_executor", lambda _job_type: _TestHandler())

    workflow_registry.clear_for_tests()
    workflow_registry.register(
        WorkflowDefinition(
            workflow_type="test.workflow",
            build=lambda params: chunks("chunk", "job_test_echo", params["items"], chunk_size=0),
        )
    )

    try:
        with pytest.raises(ValidationAppError) as exc:
            await create_job(_FakeDB(), payload, "caller-1")
    finally:
        workflow_registry.clear_for_tests()

    assert exc.value.code == "INVALID_INPUT"
    assert exc.value.details == {"job_type": "test.workflow"}


@pytest.mark.asyncio
async def test_create_job_rejects_poster_title_image_reference_outside_allowlist_before_persistence(monkeypatch):
    payload = CreateJobRequest.model_validate(
        {
            "client_request_id": "poster-invalid-ref-1",
            "job_type": "poster_title_image",
            "job_params": {
                "items": [
                    {
                        "item_id": "es",
                        "language": "es",
                        "title_text": "Cuando el amor se alejo",
                        "reference_image": {
                            "public_url": "https://not-allowed.oss-local.aliyuncs.com/reference/title.png",
                            "internal_url": "https://not-allowed.oss-local-internal.aliyuncs.com/reference/title.png",
                            "content_type": "image/png",
                            "sha256": "a" * 64,
                        },
                    }
                ]
            },
        }
    )

    async def fail_before_validation_boundary(*_args, **_kwargs):
        raise AssertionError("poster_title_image reference validation should fail before persistence")

    _patch_job_settings(monkeypatch, MAX_ACTIVE_JOBS=0)
    monkeypatch.setattr(
        "app.services.jobs.JobRepo.advisory_lock_for_client_request",
        fail_before_validation_boundary,
    )
    monkeypatch.setattr("app.services.jobs.JobRepo.create", fail_before_validation_boundary)

    with pytest.raises(AppError) as exc:
        await create_job(_FakeDB(), payload, "caller-1")

    assert exc.value.code == "POSTER_TITLE_IMAGE_REFERENCE_INVALID"
    assert exc.value.details["source_reason"] == "INVALID_INPUT"


@pytest.mark.asyncio
async def test_create_job_idempotency_uses_shell_request_fingerprint(monkeypatch):
    existing = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id="req-1",
        job_type="test.echo",
        status="queued",
        progress_percent=0,
        metadata_={},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    async def fake_advisory_lock(*_args):
        pass

    async def fake_get_recent(*_args, **_kwargs):
        return existing, SimpleNamespace(request_fingerprint=expected_fingerprint)

    async def fail_create(*_args, **_kwargs):
        raise AssertionError("idempotent create should return existing job")

    _patch_job_settings(monkeypatch, MAX_ACTIVE_JOBS=0)
    monkeypatch.setattr("app.services.jobs.JobRepo.advisory_lock_for_client_request", fake_advisory_lock)
    monkeypatch.setattr("app.services.jobs.JobRepo.get_submission_by_client_request", fake_get_recent)
    monkeypatch.setattr("app.services.jobs.JobRepo.create", fail_create)
    monkeypatch.setattr("app.jobs.factory.get_job_executor", lambda _job_type: _TestHandler())

    payload = CreateJobRequest.model_validate(
        {
            "client_request_id": "req-1",
            "job_type": "test.echo",
            "job_params": {"value": {"hello": "world"}, "label": "Echo"},
            "options": {"idempotency_mode": "return_existing"},
        }
    )
    expected_fingerprint = _request_fingerprint(payload, "caller-1", {"value": {"hello": "world"}, "label": "Echo"})

    job, created = await create_job(_FakeDB(), payload, "caller-1")

    assert job is existing
    assert created is False


@pytest.mark.asyncio
async def test_create_job_rejects_duplicate_by_default(monkeypatch):
    existing = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id="req-1",
        job_type="test.echo",
        status="queued",
        progress_percent=0,
        metadata_={},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    async def fake_advisory_lock(*_args):
        pass

    async def fake_get_recent(*_args, **_kwargs):
        return existing, SimpleNamespace(request_fingerprint=expected_fingerprint)

    _patch_job_settings(monkeypatch, MAX_ACTIVE_JOBS=0)
    monkeypatch.setattr("app.services.jobs.JobRepo.advisory_lock_for_client_request", fake_advisory_lock)
    monkeypatch.setattr("app.services.jobs.JobRepo.get_submission_by_client_request", fake_get_recent)
    monkeypatch.setattr("app.jobs.factory.get_job_executor", lambda _job_type: _TestHandler())

    payload = CreateJobRequest.model_validate(
        {
            "client_request_id": "req-1",
            "job_type": "test.echo",
            "job_params": {"value": {"hello": "world"}, "label": "Echo"},
        }
    )
    expected_fingerprint = _request_fingerprint(payload, "caller-1", {"value": {"hello": "world"}, "label": "Echo"})

    with pytest.raises(Exception, match="client_request_id already used"):
        await create_job(_FakeDB(), payload, "caller-1")


def test_request_fingerprint_canonicalizes_callback_and_ignores_metadata():
    left = CreateJobRequest.model_validate(
        {
            "client_request_id": "req-1",
            "job_type": "test.echo",
            "job_params": {"value": {"hello": "world"}},
            "callback": {"url": "https://EXAMPLE.com"},
            "metadata": {"trace": "left"},
        }
    )
    right = CreateJobRequest.model_validate(
        {
            "client_request_id": "req-1",
            "job_type": "test.echo",
            "job_params": {"value": {"hello": "world"}},
            "callback": {"url": "https://example.com/"},
            "metadata": {"trace": "right"},
            "options": {"priority": "normal", "idempotency_mode": "reject_duplicate"},
        }
    )

    left_hash = _request_fingerprint(left, "caller-1", {"value": {"hello": "world"}})
    right_hash = _request_fingerprint(right, "caller-1", {"value": {"hello": "world"}})

    assert left_hash == right_hash


def test_validate_create_contract_rejects_callback_domain_resolving_to_metadata_ip(monkeypatch):
    def fake_getaddrinfo(*_args, **_kwargs):
        return [(None, None, None, "", ("169.254.169.254", 443))]

    monkeypatch.setattr("app.core.callback_security.socket.getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr("app.jobs.factory.get_job_executor", lambda _job_type: _TestHandler())
    payload = CreateJobRequest.model_validate(
        {
            "client_request_id": "req-1",
            "job_type": "test.echo",
            "job_params": {"value": {"hello": "world"}},
            "callback": {"url": "https://metadata.example/callback"},
        }
    )

    with pytest.raises(AppError) as exc:
        validate_create_contract(payload)

    assert exc.value.code == "INVALID_INPUT"
    assert "private or reserved" in exc.value.message


@pytest.mark.parametrize("app_env", ["test", "prd"])
def test_validate_create_contract_rejects_demo_job_type_in_release_env(monkeypatch, app_env):
    handler = _TestHandler()
    handler.visibility = "demo"
    _patch_job_settings(monkeypatch, APP_ENV=app_env)
    monkeypatch.setattr("app.jobs.factory.get_job_executor", lambda _job_type: handler)
    payload = CreateJobRequest.model_validate(
        {
            "client_request_id": "req-1",
            "job_type": "test.echo",
            "job_params": {"value": {"hello": "world"}},
        }
    )

    with pytest.raises(ValidationAppError) as exc:
        validate_create_contract(payload)

    assert exc.value.code == "INVALID_JOB_TYPE"
    assert exc.value.details == {"job_type": "test.echo", "visibility": "demo", "app_env": app_env}


@pytest.mark.parametrize("app_env", ["local", "dev"])
def test_validate_create_contract_allows_demo_job_type_in_non_release_env(monkeypatch, app_env):
    handler = _TestHandler()
    handler.visibility = "demo"
    _patch_job_settings(monkeypatch, APP_ENV=app_env)
    monkeypatch.setattr("app.jobs.factory.get_job_executor", lambda _job_type: handler)
    payload = CreateJobRequest.model_validate(
        {
            "client_request_id": "req-1",
            "job_type": "test.echo",
            "job_params": {"value": {"hello": "world"}},
        }
    )

    returned_handler, job_params = validate_create_contract(payload)

    assert returned_handler is handler
    assert job_params == {"value": {"hello": "world"}}


def test_validate_create_contract_rejects_internal_job_type_in_local_env(monkeypatch):
    handler = _TestHandler()
    handler.visibility = "internal"
    _patch_job_settings(monkeypatch, APP_ENV="local")
    monkeypatch.setattr("app.jobs.factory.get_job_executor", lambda _job_type: handler)
    payload = CreateJobRequest.model_validate(
        {
            "client_request_id": "req-1",
            "job_type": "test.echo",
            "job_params": {"value": {"hello": "world"}},
        }
    )

    with pytest.raises(ValidationAppError) as exc:
        validate_create_contract(payload)

    assert exc.value.code == "INVALID_JOB_TYPE"
    assert exc.value.details == {"job_type": "test.echo", "visibility": "internal", "app_env": "local"}

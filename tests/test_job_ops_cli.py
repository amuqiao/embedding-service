import json
import uuid
from datetime import UTC, datetime

from typer.testing import CliRunner

from app.models.job import DispatchOutbox, Job, JobAttempt
from scripts.job_ops import cli


RUNNER = CliRunner()


class _FakeDB:
    async def commit(self):
        return None


async def _fake_with_db(coro):
    return await coro(_FakeDB())


def _candidate():
    attempt_id = uuid.uuid4()
    job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id="dispatch-replay",
        job_type="example_sleep",
        status="queued",
        active_attempt_id=attempt_id,
        progress_percent=0,
    )
    attempt = JobAttempt(
        id=attempt_id,
        job_id=job.id,
        purpose="business_execution",
        purpose_attempt_no=1,
        retry_chain_id=attempt_id,
        created_reason="initial",
        status="pending",
        timeout_seconds=60,
        policy_max_attempts=1,
        policy_backoff_kind="none",
        policy_retryable_error_codes=[],
        retry_policy_snapshot={},
    )
    dispatch = DispatchOutbox(
        id=uuid.uuid4(),
        event_id=f"job_attempt:{attempt_id}:dispatch",
        attempt_id=attempt_id,
        task_name="jobs.run_attempt",
        payload={"attempt_id": str(attempt_id)},
        status="dead_letter",
        publish_attempts=12,
        max_publish_attempts=12,
        dead_lettered_at=datetime.now(UTC),
        last_error={"code": "TASKIQ_PUBLISH_FAILED"},
    )
    return job, attempt, dispatch


def test_delete_family_maps_repo_value_error_to_not_eligible(monkeypatch):
    async def soft_delete(*_args, **_kwargs):
        raise ValueError("cannot soft-delete root job family: active submission key is missing")

    monkeypatch.setattr(cli, "_with_db", _fake_with_db)
    monkeypatch.setattr(cli.JobRepo, "soft_delete_root_family", soft_delete)

    result = RUNNER.invoke(
        cli.app,
        ["delete-family", str(uuid.uuid4()), "--reason", "manual", "--confirm", "--json"],
    )

    assert result.exit_code == 3
    payload = json.loads(result.output)
    assert payload["status"] == "not_eligible"
    assert "active submission key" in payload["message"]


def test_restore_family_maps_repo_value_error_to_not_eligible(monkeypatch):
    async def restore(*_args, **_kwargs):
        raise ValueError("cannot restore root job family: submission key is already used by an active job")

    monkeypatch.setattr(cli, "_with_db", _fake_with_db)
    monkeypatch.setattr(cli.JobRepo, "restore_root_family", restore)

    result = RUNNER.invoke(cli.app, ["restore-family", str(uuid.uuid4()), "--confirm", "--json"])

    assert result.exit_code == 3
    payload = json.loads(result.output)
    assert payload["status"] == "not_eligible"
    assert "already used" in payload["message"]


def test_replay_dispatch_resets_outbox_without_inline_publish(monkeypatch):
    candidate = _candidate()
    published = []

    async def get_candidate(*_args, **_kwargs):
        return candidate

    async def replay(*_args, **_kwargs):
        return candidate

    async def publish(_attempt_id):
        published.append(_attempt_id)

    monkeypatch.setattr(cli, "_with_db", _fake_with_db)
    monkeypatch.setattr(cli.JobRepo, "get_dead_lettered_dispatch_replay_candidate", get_candidate)
    monkeypatch.setattr(cli.JobRepo, "replay_dead_lettered_dispatch", replay)

    import app.tasks.jobs as task_jobs

    monkeypatch.setattr(task_jobs, "publish_job_attempt", publish)

    result = RUNNER.invoke(cli.app, ["replay-dispatch", str(candidate[0].id), "--confirm", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "replayed"
    assert payload["publish_status"] == "dispatcher_pending"
    assert published == []

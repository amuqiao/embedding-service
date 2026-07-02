from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import typer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.repositories.job_repo import JobRepo


HELP_EPILOG = """\b
作用域：
  Job 写操作运维入口。只用于明确确认后的恢复、删除和恢复动作。
  只读排障、状态查询和证据收集继续使用 ./scripts/jobs.sh。

\b
安全边界：
  所有写操作必须传 --confirm。
  replay-dispatch 只处理尚未被终态收敛的 queued + active pending attempt + dispatch dead_letter。
  已经 failed 且可能已经发出 failed callback 的 Job 不会被本命令重开。

\b
常用示例：
  ./scripts/job-ops.sh replay-dispatch <job_id>
  ./scripts/job-ops.sh replay-dispatch <job_id> --confirm
  ./scripts/job-ops.sh delete-family <root_job_id> --reason manual-cleanup --confirm
  ./scripts/job-ops.sh restore-family <root_job_id> --confirm

\b
Exit Codes:
  0  成功
  2  参数非法
  3  目标状态不符合操作前置条件
  4  写操作成功但后续投递动作失败或被延后
"""

REPLAY_HELP_EPILOG = """\b
说明：
  replay-dispatch 会把符合条件的 dispatch_outbox 从 dead_letter 重置为 retrying，
  清空旧 lease / dead_letter 字段，重置 publish_attempts，并立即尝试重新发布同一个 attempt_id。

\b
前置条件：
  Job.status = queued
  Job.active_attempt_id 指向 pending attempt
  dispatch_outbox.status = dead_letter
  dispatch_outbox.task_name = jobs.run_attempt

\b
不会做：
  不重开已经 failed 的 Job。
  不撤销已经 delivered 的 callback。
  不创建新的业务 Job 或新的 execution attempt。
"""

DELETE_HELP_EPILOG = """\b
说明：
  delete-family 调用已有 soft delete 逻辑，只软删除 settled public root Job family。
  如果 root 未终态、callback 未 settled、child 未终态或 submission key 不满足约束，会失败且不修改。
"""

RESTORE_HELP_EPILOG = """\b
说明：
  restore-family 调用已有 restore 逻辑，只恢复 soft-deleted public root Job family。
  如果 submission key 已被活跃 Job 占用，或 family 不是完整软删除状态，会失败且不修改。
"""


app = typer.Typer(
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
    help="Job 写操作运维入口。所有写操作必须显式 --confirm。",
    epilog=HELP_EPILOG,
)


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, uuid.UUID)):
        return str(value)
    return str(value)


def _print(payload: dict[str, Any], *, json_output: bool, err: bool = False) -> None:
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), err=err)
        return
    status = payload.get("status")
    typer.echo(f"status: {status}", err=err)
    for key, value in payload.items():
        if key == "status":
            continue
        typer.echo(f"{key}: {value}", err=err)


def _parse_uuid(raw: str, *, name: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise typer.BadParameter(f"{name} must be a UUID") from exc


def _operator(explicit: str | None) -> str:
    if explicit:
        return explicit
    return os.getenv("USER") or os.getenv("LOGNAME") or "unknown"


async def _with_db(coro):
    engine = create_async_engine(settings.database.url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as db:
            return await coro(db)
    finally:
        await engine.dispose()


def _candidate_payload(row) -> dict[str, Any]:
    job, attempt, dispatch = row
    return {
        "job_id": str(job.id),
        "job_status": job.status,
        "attempt_id": str(attempt.id),
        "attempt_status": attempt.status,
        "dispatch_id": str(dispatch.id),
        "dispatch_status": dispatch.status,
        "publish_attempts": dispatch.publish_attempts,
        "max_publish_attempts": dispatch.max_publish_attempts,
        "dead_lettered_at": dispatch.dead_lettered_at,
        "last_error": dispatch.last_error,
    }


@app.command("replay-dispatch", help="重放尚未终态收敛的 dead-letter dispatch。", epilog=REPLAY_HELP_EPILOG)
def replay_dispatch(
    job_id: str = typer.Argument(..., help="Job UUID。必须是 queued 且 active attempt 仍 pending。"),
    confirm: bool = typer.Option(False, "--confirm", help="确认执行写操作。未传时只做 dry-run。"),
    reason: str = typer.Option("manual_dispatch_replay", "--reason", help="写入 audit event 的操作原因。"),
    operator: str | None = typer.Option(None, "--operator", help="写入 audit event 的操作者；默认取 USER/LOGNAME。"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
) -> None:
    job_uuid = _parse_uuid(job_id, name="job_id")
    operator_value = _operator(operator)

    async def run() -> dict[str, Any]:
        async def inspect(db: AsyncSession):
            return await JobRepo.get_dead_lettered_dispatch_replay_candidate(db, job_uuid)

        candidate = await _with_db(inspect)
        if candidate is None:
            return {
                "status": "not_eligible",
                "job_id": str(job_uuid),
                "message": "no queued active pending attempt with dead-letter dispatch was found",
            }
        if not confirm:
            return {
                "status": "dry_run",
                "message": "pass --confirm to reset dispatch and publish the attempt",
                **_candidate_payload(candidate),
            }

        async def mutate(db: AsyncSession):
            replayed = await JobRepo.replay_dead_lettered_dispatch(
                db,
                job_uuid,
                reason=reason,
                operator=operator_value,
            )
            await db.commit()
            return replayed

        replayed = await _with_db(mutate)
        if replayed is None:
            return {
                "status": "not_eligible",
                "job_id": str(job_uuid),
                "message": "candidate disappeared before replay; rerun inspect",
            }
        job, attempt, dispatch = replayed
        publish_status = "requested"
        publish_error: dict[str, Any] | None = None
        from app.tasks.jobs import TaskiqPublishDeferredError, publish_job_attempt

        try:
            await publish_job_attempt(attempt.id)
        except TaskiqPublishDeferredError as exc:
            publish_status = "deferred"
            publish_error = exc.error
        except Exception as exc:  # noqa: BLE001 - CLI must report operational failure details.
            publish_status = "failed"
            publish_error = {"type": type(exc).__name__, "message": str(exc)[:500]}
        return {
            "status": "replayed" if publish_status == "requested" else "replay_committed_publish_" + publish_status,
            "job_id": str(job.id),
            "attempt_id": str(attempt.id),
            "dispatch_id": str(dispatch.id),
            "dispatch_status": dispatch.status,
            "publish_status": publish_status,
            "publish_error": publish_error,
        }

    result = asyncio.run(run())
    _print(
        result,
        json_output=json_output,
        err=result["status"] in {"not_eligible", "replay_committed_publish_deferred", "replay_committed_publish_failed"},
    )
    if result["status"] == "not_eligible":
        raise typer.Exit(3)
    if result["status"] in {"replay_committed_publish_deferred", "replay_committed_publish_failed"}:
        raise typer.Exit(4)


@app.command("delete-family", help="软删除 settled public root Job family。", epilog=DELETE_HELP_EPILOG)
def delete_family(
    root_job_id: str = typer.Argument(..., help="public root Job UUID。"),
    reason: str = typer.Option(..., "--reason", help="删除原因，会写入 deleted_reason。"),
    confirm: bool = typer.Option(False, "--confirm", help="确认执行写库软删除。"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
) -> None:
    root_uuid = _parse_uuid(root_job_id, name="root_job_id")
    if not confirm:
        _print(
            {
                "status": "dry_run",
                "root_job_id": str(root_uuid),
                "message": "pass --confirm to soft-delete the settled root family",
            },
            json_output=json_output,
        )
        return

    async def run() -> dict[str, Any]:
        async def mutate(db: AsyncSession):
            rowcount = await JobRepo.soft_delete_root_family(db, root_uuid, reason=reason)
            await db.commit()
            return rowcount

        try:
            rowcount = await _with_db(mutate)
        except ValueError as exc:
            return {
                "status": "not_eligible",
                "root_job_id": str(root_uuid),
                "message": str(exc),
            }
        if rowcount < 1:
            return {
                "status": "not_eligible",
                "root_job_id": str(root_uuid),
                "message": "root family is not eligible for soft delete",
            }
        return {"status": "deleted", "root_job_id": str(root_uuid), "affected_jobs": rowcount}

    result = asyncio.run(run())
    _print(result, json_output=json_output, err=result["status"] == "not_eligible")
    if result["status"] == "not_eligible":
        raise typer.Exit(3)


@app.command("restore-family", help="恢复 soft-deleted public root Job family。", epilog=RESTORE_HELP_EPILOG)
def restore_family(
    root_job_id: str = typer.Argument(..., help="public root Job UUID。"),
    confirm: bool = typer.Option(False, "--confirm", help="确认执行写库恢复。"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
) -> None:
    root_uuid = _parse_uuid(root_job_id, name="root_job_id")
    if not confirm:
        _print(
            {
                "status": "dry_run",
                "root_job_id": str(root_uuid),
                "message": "pass --confirm to restore the soft-deleted root family",
            },
            json_output=json_output,
        )
        return

    async def run() -> dict[str, Any]:
        async def mutate(db: AsyncSession):
            rowcount = await JobRepo.restore_root_family(db, root_uuid)
            await db.commit()
            return rowcount

        try:
            rowcount = await _with_db(mutate)
        except ValueError as exc:
            return {
                "status": "not_eligible",
                "root_job_id": str(root_uuid),
                "message": str(exc),
            }
        if rowcount < 1:
            return {
                "status": "not_eligible",
                "root_job_id": str(root_uuid),
                "message": "root family is not eligible for restore",
            }
        return {"status": "restored", "root_job_id": str(root_uuid), "affected_jobs": rowcount}

    result = asyncio.run(run())
    _print(result, json_output=json_output, err=result["status"] == "not_eligible")
    if result["status"] == "not_eligible":
        raise typer.Exit(3)


if __name__ == "__main__":
    app()

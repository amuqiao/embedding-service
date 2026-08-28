from __future__ import annotations

from contextlib import nullcontext
import time
import uuid
from typing import Any
from urllib.parse import urlparse

from smoke.harness import formatters
from smoke.harness import callback_capture
from smoke.harness import cli_contract
from smoke.harness import env_runtime
from smoke.harness import http_runtime
from smoke.harness import service_runtime
from smoke.harness.errors import FlowError
from smoke.jobs import callback as job_callback
from smoke.jobs import cli_contract as job_cli_contract
from smoke.jobs import runtime as job_runtime


JOB_TYPE = "example_lifecycle_probe"
SCENARIO_NAME = "example-lifecycle-probe"
EXPECTED_STATUS_CHOICES = {"auto", "succeeded", "failed"}
CALLBACK_EVENT_CHOICES = {
    "succeeded": ["job.succeeded"],
    "failed": ["job.failed"],
    "both": ["job.succeeded", "job.failed"],
}


def expected_status(*, fail: bool, expect_status: str) -> str:
    if expect_status not in EXPECTED_STATUS_CHOICES:
        raise FlowError(f"unsupported expect_status: {expect_status}", exit_code=2)
    if expect_status == "auto":
        return "failed" if fail else "succeeded"
    return expect_status


def callback_events(callback_event: str) -> list[str]:
    try:
        return CALLBACK_EVENT_CHOICES[callback_event]
    except KeyError as exc:
        raise FlowError(f"unsupported callback_event: {callback_event}", exit_code=2) from exc


def build_payload(
    *,
    probe_id: str,
    message: str,
    sleep_seconds: float,
    fail: bool,
    fail_after_seconds: float,
    result_payload: str | None,
    result_size_bytes: int,
    callback_url: str | None,
    callback_event: str,
    client_request_id: str | None,
) -> dict[str, Any]:
    if result_payload is not None and result_size_bytes:
        raise FlowError("result-payload and result-size-bytes are mutually exclusive", exit_code=2)
    if not fail and fail_after_seconds:
        raise FlowError("fail-after-seconds requires --fail", exit_code=2)
    if sleep_seconds + fail_after_seconds > 600:
        raise FlowError("sleep-seconds + fail-after-seconds must be <= 600", exit_code=2)
    params: dict[str, Any] = {
        "probe_id": probe_id,
        "message": message,
        "sleep_seconds": sleep_seconds,
        "fail": fail,
        "fail_after_seconds": fail_after_seconds,
    }
    if result_payload is not None:
        params["result_payload"] = result_payload
    if result_size_bytes:
        params["result_size_bytes"] = result_size_bytes
    payload: dict[str, Any] = {
        "client_request_id": client_request_id or f"smoke-example-lifecycle-probe-{uuid.uuid4()}",
        "job_type": JOB_TYPE,
        "job_params": params,
        "metadata": {"source": f"scripts/smoke.sh {SCENARIO_NAME}", "probe_id": probe_id},
        "options": {"priority": "normal", "idempotency_mode": "reject_duplicate"},
    }
    if callback_url is not None:
        payload["callback"] = {"url": callback_url, "events": callback_events(callback_event)}
    return payload


def _callback_status(job: dict[str, Any]) -> str | None:
    callback = job.get("callback")
    if not isinstance(callback, dict):
        return None
    status = callback.get("status")
    return str(status) if status is not None else None


def _callback_attempt(job: dict[str, Any]) -> int | None:
    callback = job.get("callback")
    if not isinstance(callback, dict):
        return None
    attempt = callback.get("attempt")
    return int(attempt) if isinstance(attempt, int) else None


def poll_callback_envelope(
    *,
    jobs_url: str,
    job_id: str,
    headers: dict[str, str],
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_envelope: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last_envelope = http_runtime.request_json(f"{jobs_url}/{job_id}", method="GET", headers=headers)
        job = http_runtime.data_object(last_envelope, "job")
        status = _callback_status(job)
        if status == "delivered":
            return last_envelope
        if status == "failed":
            raise FlowError(f"callback for job {job_id} failed; callback={job.get('callback')}", exit_code=1)
        time.sleep(poll_interval_seconds)
    raise FlowError(
        f"callback for job {job_id} was not delivered within {timeout_seconds}s; last={last_envelope}",
        exit_code=5,
    )


def _assert_terminal_job(
    job: dict[str, Any],
    *,
    expected: str,
    probe_id: str,
    message: str,
    forced_failure: bool,
) -> None:
    actual = str(job.get("job_status"))
    if actual != expected:
        raise FlowError(f"job {job.get('job_id')} status mismatch: expected={expected} actual={actual}", exit_code=1)
    if expected == "failed":
        error = job.get("job_error")
        if not isinstance(error, dict):
            raise FlowError(f"failed job {job.get('job_id')} missing job_error", exit_code=1)
        reason = error.get("reason") or error.get("code")
        if forced_failure and reason != "JOB_EXECUTION_FAILED":
            raise FlowError(
                f"failed job {job.get('job_id')} reason mismatch: expected=JOB_EXECUTION_FAILED actual={reason}",
                exit_code=1,
            )
        details = error.get("details")
        if forced_failure and (not isinstance(details, dict) or details.get("fault") != "forced_failure"):
            raise FlowError(
                f"failed job {job.get('job_id')} fault mismatch: expected=forced_failure actual={details}",
                exit_code=1,
            )
        return
    result = job.get("job_result")
    if not isinstance(result, dict):
        raise FlowError(f"succeeded job {job.get('job_id')} missing result object", exit_code=1)
    for key, expected_value in {"probe_id": probe_id, "message": message}.items():
        if result.get(key) != expected_value:
            raise FlowError(
                f"result {key} mismatch: expected={expected_value!r} actual={result.get(key)!r}",
                exit_code=1,
            )
    if not result.get("worker_observed_at"):
        raise FlowError("result missing worker_observed_at", exit_code=1)


def _is_loopback_api(api_url: str) -> bool:
    host = urlparse(api_url).hostname
    return host in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def _local_callback_server(context: service_runtime.RuntimeContext) -> callback_capture.CallbackCaptureServer:
    signing_secret = env_runtime.env_value("CALLBACK_SIGNING_SECRET", context.app_env)
    if not signing_secret:
        raise FlowError("CALLBACK_SIGNING_SECRET is required for --local-callback signature verification", exit_code=2)
    return callback_capture.CallbackCaptureServer(
        path="/callbacks/example-lifecycle-probe",
        signing_secret=signing_secret,
        ack_payload={"accepted": True, "msg": "example lifecycle probe callback accepted"},
    )


def _remaining_timeout_seconds(deadline: float, *, timeout_seconds: int) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise FlowError(f"example lifecycle probe timed out after {timeout_seconds}s", exit_code=5)
    return remaining


def _remaining_callback_timeout_seconds(
    deadline: float,
    *,
    timeout_seconds: int,
    callback_timeout_seconds: int | None,
) -> float:
    return cli_contract.callback_timeout_budget(
        remaining_seconds=_remaining_timeout_seconds(deadline, timeout_seconds=timeout_seconds),
        callback_timeout_seconds=callback_timeout_seconds,
    )


def _summary(
    *,
    context_summary: dict[str, Any],
    terminal_job: dict[str, Any],
    expected: str,
    probe_id: str,
    callback_job: dict[str, Any] | None,
    callback_waited: bool,
) -> dict[str, Any]:
    observed_job = callback_job or terminal_job
    return {
        "note": "summary is generated by scripts/smoke.sh; raw HTTP envelopes are under responses",
        "scenario": SCENARIO_NAME,
        "job_id": observed_job.get("job_id"),
        "job_status": observed_job.get("job_status"),
        "expected_status": expected,
        "probe_id": probe_id,
        "callback_status": _callback_status(observed_job),
        "callback_attempt": _callback_attempt(observed_job),
        "callback_waited": callback_waited,
        "mechanism_evidence": {
            "api": "create-job HTTP response returned job_id",
            "dispatcher": "job reached terminal status through dispatch_outbox -> Taskiq",
            "taskiq_worker": "job executor produced terminal result/error",
            "callbacker": "callback.status=delivered when callback is configured and waited",
            "reconciler": "normal success path does not prove reconciler intervention; this scenario keeps the role in the acceptance contract",
        },
        "context": context_summary,
    }


def run(
    *,
    job_options: job_cli_contract.JobSmokeOptions,
    callback_options: cli_contract.CallbackSmokeOptions,
    api_url: str | None,
    env_file: str | None,
    allow_remote_api: bool,
    service_api_key: str | None,
    caller_id: str,
    timeout_seconds: int,
    poll_interval_seconds: float,
    probe_id: str,
    message: str,
    sleep_seconds: float,
    fail: bool,
    fail_after_seconds: float,
    result_payload: str | None,
    result_size_bytes: int,
    json_output: bool,
) -> None:
    if not job_options.confirm_run:
        raise FlowError("example lifecycle probe smoke requires --confirm-run", exit_code=2)
    if callback_options.callback_url is not None and callback_options.local_callback:
        raise FlowError("--callback-url and --local-callback are mutually exclusive", exit_code=2)
    if callback_options.local_callback and not callback_options.wait_callback:
        raise FlowError("--local-callback requires --wait-callback", exit_code=2)
    expected = expected_status(fail=fail, expect_status=job_options.expect_status)
    context = job_runtime.resolve_job_context(
        env_file=env_file,
        api_url=api_url,
        allow_remote_api=allow_remote_api,
        caller_id=caller_id,
        service_api_key=service_api_key,
    )
    if callback_options.local_callback and not _is_loopback_api(str(context.summary["api_url"])):
        raise FlowError("--local-callback requires a loopback API URL", exit_code=2)

    jobs_url = str(context.summary["jobs_url"])
    headers = service_runtime.build_headers(context.app_env, caller_id=caller_id, service_api_key=service_api_key)
    receiver_context = _local_callback_server(context) if callback_options.local_callback else nullcontext(None)
    capture_event = None
    deadline = time.monotonic() + timeout_seconds
    try:
        with receiver_context as receiver:
            effective_callback_url = callback_options.callback_url or (receiver.url if receiver is not None else None)
            if (
                effective_callback_url is not None
                and callback_options.callback_event != "both"
                and callback_options.callback_event != expected
            ):
                raise FlowError("--callback-event must include the expected terminal job status", exit_code=2)
            create_payload = build_payload(
                probe_id=probe_id,
                message=message,
                sleep_seconds=sleep_seconds,
                fail=fail,
                fail_after_seconds=fail_after_seconds,
                result_payload=result_payload,
                result_size_bytes=result_size_bytes,
                callback_url=effective_callback_url,
                callback_event=callback_options.callback_event,
                client_request_id=job_options.client_request_id,
            )
            create_envelope = http_runtime.request_json(jobs_url, method="POST", headers=headers, payload=create_payload)
            created = http_runtime.data_object(create_envelope, "job")
            job_id = str(created["job_id"])
            get_job_envelope = job_runtime.poll_job_envelope(
                jobs_url=jobs_url,
                job_id=job_id,
                headers=headers,
                timeout_seconds=_remaining_timeout_seconds(deadline, timeout_seconds=timeout_seconds),
                poll_interval_seconds=poll_interval_seconds,
            )
            terminal_job = http_runtime.data_object(get_job_envelope, "job")
            _assert_terminal_job(
                terminal_job,
                expected=expected,
                probe_id=probe_id,
                message=message,
                forced_failure=fail,
            )

            callback_envelope = None
            callback_job = None
            callback_waited = bool(effective_callback_url and callback_options.wait_callback)
            if callback_waited:
                callback_envelope = poll_callback_envelope(
                    jobs_url=jobs_url,
                    job_id=job_id,
                    headers=headers,
                    timeout_seconds=_remaining_callback_timeout_seconds(
                        deadline,
                        timeout_seconds=timeout_seconds,
                        callback_timeout_seconds=callback_options.callback_timeout_seconds,
                    ),
                    poll_interval_seconds=poll_interval_seconds,
                )
                callback_job = http_runtime.data_object(callback_envelope, "job")
                if receiver is not None:
                    capture_event = receiver.wait_for_event(
                        job_callback.job_callback_expectation(
                            job_id=job_id,
                            event=f"job.{expected}",
                            job_status=expected,
                        ),
                        timeout_seconds=_remaining_callback_timeout_seconds(
                            deadline,
                            timeout_seconds=timeout_seconds,
                            callback_timeout_seconds=callback_options.callback_timeout_seconds,
                        ),
                    )

            local_callback_events = receiver.snapshot() if receiver is not None else []
            if callback_options.local_callback and callback_options.wait_callback and not local_callback_events:
                raise FlowError("local callback receiver did not capture any callback event", exit_code=1)
    except callback_capture.CallbackCaptureError as exc:
        raise FlowError(str(exc), exit_code=exc.exit_code) from exc

    summary = _summary(
        context_summary=context.summary,
        terminal_job=terminal_job,
        expected=expected,
        probe_id=probe_id,
        callback_job=callback_job,
        callback_waited=callback_waited,
    )
    if json_output:
        responses: dict[str, Any] = {"create_job": create_envelope, "get_job": get_job_envelope}
        if callback_envelope is not None:
            responses["callback_job"] = callback_envelope
        formatters.print_json(
            {
                "ok": True,
                "scenario": SCENARIO_NAME,
                "conclusion": (
                    f"job={summary['job_status']} expected={expected} "
                    f"callback={summary['callback_status'] or '-'}"
                ),
                "summary": summary,
                "responses": responses,
                "local_callback": {
                    "enabled": callback_options.local_callback,
                    "url": effective_callback_url if callback_options.local_callback else None,
                    "event_count": len(local_callback_events),
                    "matched_event": capture_event,
                    "events": local_callback_events,
                },
            }
        )
        return

    formatters.section("Example Lifecycle Probe")
    formatters.event("OK", "job", f"id={summary['job_id']} status={summary['job_status']} expected={expected}")
    if effective_callback_url is not None:
        formatters.event(
            "OK" if summary["callback_status"] == "delivered" else "INFO",
            "callback",
            f"status={summary['callback_status']} attempt={summary['callback_attempt']}",
        )
    formatters.event("INFO", "reconciler", "normal path keeps the role in contract but does not require intervention")
    formatters.print_table(
        [summary],
        [
            ("job_id", "job_id"),
            ("job_status", "job"),
            ("expected_status", "expected"),
            ("probe_id", "probe_id"),
            ("callback_status", "callback"),
            ("callback_attempt", "attempt"),
            ("callback_waited", "waited"),
        ],
    )

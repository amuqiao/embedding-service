import hmac
import json
from hashlib import sha256
from urllib.request import Request, urlopen

import pytest

from smoke.harness import callback_capture


def test_callback_capture_signature_verification_accepts_service_contract():
    raw_body = '{"event":"job.succeeded","job":{"job_id":"probe-job","job_status":"succeeded"}}'
    timestamp = "2026-08-28T00:00:00Z"
    signature = "sha256=" + hmac.new(
        b"test-secret",
        timestamp.encode("utf-8") + b"." + raw_body.encode("utf-8"),
        sha256,
    ).hexdigest()

    result = callback_capture.verify_callback_signature(
        headers={
            "X-Callback-Timestamp": timestamp,
            "X-Callback-Signature": signature,
        },
        raw_body=raw_body,
        signing_secret="test-secret",
    )

    assert result["checked"] is True
    assert result["valid"] is True
    assert result["error"] is None


def test_callback_capture_signature_verification_reports_missing_timestamp():
    result = callback_capture.verify_callback_signature(
        headers={"X-Callback-Signature": "sha256=abc"},
        raw_body=b"{}",
        signing_secret="test-secret",
    )

    assert result["checked"] is True
    assert result["valid"] is False
    assert result["error"] == "missing X-Callback-Timestamp"


def test_callback_capture_rejects_relative_path():
    with pytest.raises(callback_capture.CallbackCaptureError) as exc_info:
        callback_capture.CallbackCaptureServer(path="callbacks/test")

    assert exc_info.value.exit_code == 2
    assert "path must start with /" in str(exc_info.value)


def test_callback_capture_wait_timeout_uses_standard_exit_code_5():
    receiver = callback_capture.CallbackCaptureServer()

    with pytest.raises(callback_capture.CallbackCaptureError) as exc_info:
        receiver.wait_for_event(
            callback_capture.CallbackExpectation(job_id="probe-job", event="job.succeeded", job_status="succeeded"),
            timeout_seconds=0,
        )

    assert exc_info.value.exit_code == 5


def _signature(*, secret: str, timestamp: str, raw_body: bytes) -> str:
    return "sha256=" + hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("utf-8") + b"." + raw_body,
        sha256,
    ).hexdigest()


def _enter_receiver_or_skip(receiver: callback_capture.CallbackCaptureServer) -> callback_capture.CallbackCaptureServer:
    try:
        return receiver.__enter__()
    except callback_capture.CallbackCaptureError as exc:
        if exc.exit_code == 4:
            pytest.skip(str(exc))
        raise


def _post_callback(
    *,
    url: str,
    body: dict,
    secret: str,
    signature: str | None = None,
) -> tuple[int, dict]:
    raw_body = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    timestamp = "2026-08-28T00:00:00+00:00"
    request = Request(
        url,
        data=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Callback-Timestamp": timestamp,
            "X-Callback-Signature": signature or _signature(secret=secret, timestamp=timestamp, raw_body=raw_body),
        },
        method="POST",
    )
    with urlopen(request, timeout=2) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def test_callback_capture_server_captures_http_callback_and_returns_ack():
    receiver = callback_capture.CallbackCaptureServer(
        path="/callbacks/test",
        signing_secret="test-secret",
        ack_payload={"accepted": True, "msg": "ack"},
    )
    entered = _enter_receiver_or_skip(receiver)
    try:
        body = {"event": "job.succeeded", "job": {"job_id": "probe-job", "job_status": "succeeded"}}

        status, ack = _post_callback(url=str(entered.url), body=body, secret="test-secret")
        event = entered.wait_for_event(
            callback_capture.CallbackExpectation(
                job_id="probe-job",
                event="job.succeeded",
                job_status="succeeded",
            ),
            timeout_seconds=1,
        )

        assert status == 200
        assert ack == {"accepted": True, "msg": "ack"}
        assert event["path"] == "/callbacks/test"
        assert event["body"] == body
        assert event["signature"]["checked"] is True
        assert event["signature"]["valid"] is True
        assert entered.snapshot() == [event]
    finally:
        receiver.__exit__(None, None, None)


def test_callback_capture_server_rejects_bad_signature_when_waiting_for_match():
    receiver = callback_capture.CallbackCaptureServer(path="/callbacks/test", signing_secret="test-secret")
    entered = _enter_receiver_or_skip(receiver)
    try:
        body = {"event": "job.failed", "job": {"job_id": "probe-job", "job_status": "failed"}}

        status, ack = _post_callback(
            url=str(entered.url),
            body=body,
            secret="test-secret",
            signature="sha256=bad",
        )

        assert status == 200
        assert ack["accepted"] is True
        with pytest.raises(callback_capture.CallbackCaptureError) as exc_info:
            entered.wait_for_event(
                callback_capture.CallbackExpectation(
                    job_id="probe-job",
                    event="job.failed",
                    job_status="failed",
                ),
                timeout_seconds=1,
            )

        assert exc_info.value.exit_code == 1
        assert "signature invalid" in str(exc_info.value)
    finally:
        receiver.__exit__(None, None, None)

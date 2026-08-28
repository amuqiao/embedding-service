from __future__ import annotations

from dataclasses import dataclass
import hmac
import json
import threading
import time
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit


class CallbackCaptureError(RuntimeError):
    exit_code: int

    def __init__(self, message: str, *, exit_code: int = 4) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class CallbackExpectation:
    job_id: str | None = None
    event: str | None = None
    job_status: str | None = None


def _header_value(headers: dict[str, str], name: str) -> str | None:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


def verify_callback_signature(
    *,
    headers: dict[str, str],
    raw_body: bytes | str,
    signing_secret: str | None,
) -> dict[str, Any]:
    timestamp = _header_value(headers, "X-Callback-Timestamp")
    actual = _header_value(headers, "X-Callback-Signature")
    raw_body_bytes = raw_body.encode("utf-8") if isinstance(raw_body, str) else raw_body
    if not signing_secret:
        return {
            "checked": False,
            "valid": None,
            "timestamp": timestamp,
            "actual": actual,
            "expected": None,
            "error": "signing_secret_not_configured",
        }
    if not timestamp:
        return {
            "checked": True,
            "valid": False,
            "timestamp": None,
            "actual": actual,
            "expected": None,
            "error": "missing X-Callback-Timestamp",
        }
    expected = "sha256=" + hmac.new(
        signing_secret.encode("utf-8"),
        timestamp.encode("utf-8") + b"." + raw_body_bytes,
        sha256,
    ).hexdigest()
    return {
        "checked": True,
        "valid": hmac.compare_digest(actual or "", expected),
        "timestamp": timestamp,
        "actual": actual,
        "expected": expected,
        "error": None if hmac.compare_digest(actual or "", expected) else "signature_mismatch",
    }


def _event_matches(event: dict[str, Any], expectation: CallbackExpectation) -> bool:
    body = event.get("body")
    if not isinstance(body, dict):
        return False
    if expectation.event is not None and body.get("event") != expectation.event:
        return False
    job = body.get("job")
    if not isinstance(job, dict):
        return False
    if expectation.job_id is not None and str(job.get("job_id")) != expectation.job_id:
        return False
    if expectation.job_status is not None and job.get("job_status") != expectation.job_status:
        return False
    return True


class CallbackCaptureServer:
    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        path: str = "/callbacks/smoke-capture",
        signing_secret: str | None = None,
        ack_payload: dict[str, Any] | None = None,
    ) -> None:
        if not path.startswith("/"):
            raise CallbackCaptureError("callback capture path must start with /", exit_code=2)
        self.host = host
        self.path = path
        self.signing_secret = signing_secret
        self.ack_payload = ack_payload or {"accepted": True, "msg": "smoke callback accepted"}
        self.url: str | None = None
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._condition = threading.Condition()
        self._events: list[dict[str, Any]] = []

    def __enter__(self) -> "CallbackCaptureServer":
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                request_path = urlsplit(self.path).path
                if request_path != receiver.path:
                    self.send_response(404)
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length", "0") or "0")
                raw_body_bytes = self.rfile.read(length)
                raw_body = raw_body_bytes.decode("utf-8", errors="replace")
                body: Any = None
                parse_error: str | None = None
                if raw_body:
                    try:
                        body = json.loads(raw_body)
                    except json.JSONDecodeError as exc:
                        parse_error = str(exc)
                headers = dict(self.headers.items())
                captured = {
                    "method": "POST",
                    "path": request_path,
                    "headers": headers,
                    "body": body,
                    "raw_body": raw_body,
                    "json_parse_error": parse_error,
                    "signature": verify_callback_signature(
                        headers=headers,
                        raw_body=raw_body_bytes,
                        signing_secret=receiver.signing_secret,
                    ),
                    "received_at_monotonic": time.monotonic(),
                }
                with receiver._condition:
                    receiver._events.append(captured)
                    receiver._condition.notify_all()
                response = json.dumps(receiver.ack_payload, separators=(",", ":")).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        try:
            self._server = ThreadingHTTPServer((self.host, 0), Handler)
        except OSError as exc:
            raise CallbackCaptureError(
                f"callback capture server failed to bind {self.host}:0: {exc}",
                exit_code=4,
            ) from exc
        port = int(self._server.server_address[1])
        self.url = f"http://{self.host}:{port}{self.path}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def snapshot(self) -> list[dict[str, Any]]:
        with self._condition:
            return [self._public_event(event) for event in self._events]

    def wait_for_event(
        self,
        expectation: CallbackExpectation,
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            while True:
                for event in self._events:
                    if _event_matches(event, expectation):
                        public_event = self._public_event(event)
                        signature = public_event.get("signature")
                        if isinstance(signature, dict) and signature.get("checked") and not signature.get("valid"):
                            raise CallbackCaptureError(
                                f"callback signature invalid: {signature.get('error')}",
                                exit_code=1,
                            )
                        return public_event
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CallbackCaptureError(
                        f"callback event not received within {timeout_seconds}s; expectation={expectation}",
                        exit_code=5,
                    )
                self._condition.wait(timeout=min(remaining, 0.5))

    @staticmethod
    def _public_event(event: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in event.items() if key != "received_at_monotonic"}

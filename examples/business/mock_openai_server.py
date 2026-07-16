"""本地 Mock OpenAI 兼容服务器示例，用于业务项目恢复 mock-smoke 时复用。

不调用真实模型，返回稳定的 OpenAI 兼容 chat completion 响应。

用法：
  python examples/business/mock_openai_server.py [port]   # 默认 18200
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 18200

_RESPONSE_BODY = "Mock model response for local workflow validation."


def _make_completion(content: str) -> bytes:
    return json.dumps({
        "id": "mock-chatcmpl-001",
        "object": "chat.completion",
        "model": "gpt-5.5",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 20, "completion_tokens": 80, "total_tokens": 100},
    }, ensure_ascii=False).encode("utf-8")


class MockHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[mock-openai] {fmt % args}", flush=True)

    def _send_json(self, body: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        body = json.dumps({"data": [{"id": "gpt-5.5", "object": "model"}]}).encode()
        self._send_json(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self._send_json(_make_completion(_RESPONSE_BODY))


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", PORT), MockHandler)
    print(f"[mock-openai] listening on http://127.0.0.1:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

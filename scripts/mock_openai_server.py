"""本地 Mock OpenAI 兼容服务器，用于 mock-smoke 验证任务完整流程。

不调用真实模型，按 job_type 返回预设的合规响应：
  step1_localize  → 包含工作注释 + 本地化正文标记
  step2_review    → 包含 【校验结论】通过
  step3_translate → 纯英文译文

用法：
  python scripts/mock_openai_server.py [port]   # 默认 18200
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 18200

_STEP1_BODY = """\
===工作注释开始===
Mock 工作注释：主角在聚会上重新理解了自己的生活，情感细腻，叙事节奏舒缓。
===工作注释结束===

===本地化正文开始===
这是一个关于家庭、身份与选择的故事。聚会的灯光洒在每个人的脸上，主角第一次感受到某种久违的平静。
===本地化正文结束==="""

_STEP2_BODY = """\
【校验结论】通过"""

_STEP3_BODY = """\
This is a story about family, identity, and choice. \
The lights of the gathering fell on every face, \
and for the first time the protagonist felt a long-forgotten sense of calm."""


def _detect_job_type(messages: list[dict]) -> str:
    """从 messages 内容推断 job_type，用于返回对应 mock 响应。

    用 output_contract 里唯一的输出标记区分：
      step1 → ===工作注释开始===（在 output_contract 里）
      step2 → 【校验结论】通过/不通过（在 output_contract 里）
      step3 → 兜底
    """
    all_text = " ".join(m.get("content", "") for m in messages)
    if "【校验结论】" in all_text:
        return "step2"
    if "===工作注释开始===" in all_text:
        return "step1"
    return "step3"


def _make_completion(content: str) -> bytes:
    return json.dumps({
        "id": "mock-chatcmpl-001",
        "object": "chat.completion",
        "model": "gpt-4.1",
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
        body = json.dumps({"data": [{"id": "gpt-4.1", "object": "model"}]}).encode()
        self._send_json(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {}

        messages = payload.get("messages", [])
        job_type = _detect_job_type(messages)

        content_map = {"step1": _STEP1_BODY, "step2": _STEP2_BODY, "step3": _STEP3_BODY}
        content = content_map[job_type]
        self._send_json(_make_completion(content))


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", PORT), MockHandler)
    print(f"[mock-openai] listening on http://127.0.0.1:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

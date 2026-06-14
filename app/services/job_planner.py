from dataclasses import dataclass
from typing import Any, Literal


ExecutionMode = Literal["single", "chunked"]


@dataclass(frozen=True)
class PlannedWorkItem:
    name: str
    kind: str
    chunk_index: int
    input_payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class JobPlan:
    execution_mode: ExecutionMode
    chunk_count: int
    work_items: list[PlannedWorkItem]
    chunk_registry: list[dict[str, Any]]

    def model_dump(self) -> dict[str, Any]:
        return {
            "execution_mode": self.execution_mode,
            "chunk_count": self.chunk_count,
            "chunk_registry": self.chunk_registry,
            "work_items": [
                {
                    "name": item.name,
                    "kind": item.kind,
                    "chunk_index": item.chunk_index,
                    "input_payload": item.input_payload,
                }
                for item in self.work_items
            ],
        }


def job_plan_from_payload(payload: dict[str, Any]) -> JobPlan:
    return JobPlan(
        execution_mode=payload["execution_mode"],
        chunk_count=payload["chunk_count"],
        chunk_registry=list(payload["chunk_registry"]),
        work_items=[
            PlannedWorkItem(
                name=item["name"],
                kind=item["kind"],
                chunk_index=item["chunk_index"],
                input_payload=item.get("input_payload"),
            )
            for item in payload["work_items"]
        ],
    )


def _count_chars(text: str) -> int:
    return sum(1 for char in text if not char.isspace())


def _split_by_char_limit(text: str, limit: int) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    current_chars = 0
    for char in text:
        current.append(char)
        if not char.isspace():
            current_chars += 1
        if current_chars >= limit:
            chunk = "".join(current).strip()
            if chunk:
                parts.append(chunk)
            current = []
            current_chars = 0
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _split_oversized_paragraph(paragraph: str, limit: int) -> list[str]:
    if _count_chars(paragraph) <= limit:
        return [paragraph]

    sentence_endings = set("。！？；.!?;")
    sentences: list[str] = []
    current: list[str] = []
    for char in paragraph:
        current.append(char)
        if char in sentence_endings:
            sentence = "".join(current).strip()
            if sentence:
                sentences.append(sentence)
            current = []
    tail = "".join(current).strip()
    if tail:
        sentences.append(tail)
    if not sentences:
        sentences = [paragraph]

    parts: list[str] = []
    current_parts: list[str] = []
    current_chars = 0
    for sentence in sentences:
        sentence_chars = _count_chars(sentence)
        if sentence_chars > limit:
            if current_parts:
                parts.append("".join(current_parts).strip())
                current_parts = []
                current_chars = 0
            parts.extend(_split_by_char_limit(sentence, limit))
            continue
        if current_parts and current_chars + sentence_chars > limit:
            parts.append("".join(current_parts).strip())
            current_parts = []
            current_chars = 0
        current_parts.append(sentence)
        current_chars += sentence_chars
    if current_parts:
        parts.append("".join(current_parts).strip())
    return [part for part in parts if part]


def split_text(text: str, max_chars: int | None = None) -> list[str]:
    return [item["text"] for item in split_text_with_registry(text, max_chars=max_chars)]


def split_text_with_registry(text: str, max_chars: int | None = None) -> list[dict[str, Any]]:
    limit = max_chars or 3000
    paragraphs = [
        part
        for item in text.split("\n\n")
        if item.strip()
        for part in _split_oversized_paragraph(item.strip(), limit)
    ]
    if not paragraphs:
        return [{"chunk_index": 1, "text": text, "char_count": _count_chars(text)}]

    chunks: list[str] = []
    current: list[str] = []
    current_chars = 0

    for paragraph in paragraphs:
        paragraph_chars = _count_chars(paragraph)
        if current and current_chars + paragraph_chars > limit:
            chunks.append("\n\n".join(current))
            current = []
            current_chars = 0
        current.append(paragraph)
        current_chars += paragraph_chars

    if current:
        chunks.append("\n\n".join(current))

    return [
        {
            "chunk_index": index,
            "text": chunk,
            "char_count": _count_chars(chunk),
        }
        for index, chunk in enumerate(chunks, start=1)
    ]


def build_job_plan(job_type: str, input_text: str) -> JobPlan:
    from app.core.workflow_registry import get as get_handler
    handler = get_handler(job_type)

    char_count = _count_chars(input_text)
    if not handler.chunking_enabled or char_count <= handler.max_single_chars:
        return JobPlan(
            execution_mode="single",
            chunk_count=1,
            chunk_registry=[{"chunk_index": 1, "text": input_text, "char_count": char_count}],
            work_items=[
                PlannedWorkItem(
                    name=f"{job_type}.whole",
                    kind="whole",
                    chunk_index=0,
                    input_payload={"text": input_text},
                )
            ],
        )

    chunk_registry = split_text_with_registry(input_text, max_chars=handler.chunk_size)
    work_items: list[PlannedWorkItem] = []

    if handler.canvas_pattern == "memory_fanout":
        work_items.append(
            PlannedWorkItem(
                name=f"{job_type}.memory",
                kind="memory",
                chunk_index=0,
                input_payload={"chunks": chunk_registry},
            )
        )

    for chunk in chunk_registry:
        work_items.append(
            PlannedWorkItem(
                name=f"{job_type}.chunk",
                kind="chunk",
                chunk_index=chunk["chunk_index"],
                input_payload={"text": chunk["text"], "char_count": chunk["char_count"]},
            )
        )

    work_items.append(
        PlannedWorkItem(
            name=f"{job_type}.merge",
            kind="merge",
            chunk_index=len(chunk_registry) + 1,
            input_payload={"chunk_count": len(chunk_registry)},
        )
    )

    if handler.canvas_pattern == "scan_chord":
        work_items.append(
            PlannedWorkItem(
                name=f"{job_type}.scan",
                kind="scan",
                chunk_index=len(chunk_registry) + 2,
                input_payload={"chunk_count": len(chunk_registry)},
            )
        )

    return JobPlan(
        execution_mode="chunked",
        chunk_count=len(chunk_registry),
        chunk_registry=chunk_registry,
        work_items=work_items,
    )

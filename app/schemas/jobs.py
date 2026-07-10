from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any, Literal, TypeAlias
from uuid import UUID

from pydantic import ConfigDict, Field, StrictFloat, StrictInt, field_validator, model_validator

from app.core.language_catalog import supported_language_codes
from app.schemas.common import StrictBaseModel
from app.schemas.errors import CallbackErrorDetail, JobErrorDetail

HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
BARE_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
REAL_LLM_ECHO_INLINE_MAX_BYTES = 4096
POSTER_TITLE_IMAGE_MAX_TITLE_LINES = 2
POSTER_TITLE_IMAGE_MAX_HARD_LINE_BREAKS = POSTER_TITLE_IMAGE_MAX_TITLE_LINES - 1
POSTER_TITLE_IMAGE_DISALLOWED_LINE_BREAKS = frozenset(
    {
        "\r",
        "\v",
        "\f",
        "\x1c",
        "\x1d",
        "\x1e",
        "\x85",
        "\u2028",
        "\u2029",
    }
)
POSTER_TITLE_IMAGE_LINE_CONTROL_OVERRIDE_PATTERN = re.compile(
    r"\\n|\b(?:line[- ]?breaks?|newlines?|new\s+lines?|hard\s+breaks?|lf)\b|"
    r"\b(?:render|show|display|draw|format|set|make|force|keep|preserve|put|place)\b.{0,40}"
    r"\b(?:single|one|two|three|\d+|separate|multiple)\s+lines?\b|"
    r"\b(?:title|text|words?)\b.{0,40}\b(?:single|one|two|three|\d+|separate|multiple)\s+lines?\b|"
    r"\b(?:title|text|words?)\b.{0,40}\b(?:rows?|stacked|multiline)\b|"
    r"\b(?:arrange|put|place|keep|make|set|format|render|display|show)\b.{0,20}"
    r"\b(?:title|text|words?)\b.{0,40}\b(?:rows?|stacked|multiline)\b|"
    r"\b(?:each|every)\s+word\b.{0,40}\b(?:own|separate)\s+rows?\b|"
    r"\bmultiline\b|"
    r"\b(?:break|wrap|split)\b.{0,40}\b(?:title|text|words?)\b|"
    r"\b(?:title|text|words?)\b.{0,40}\b(?:break|wrap|split)\b|"
    r"\b(?:split|merge|preserve|add|remove|reorder|reposition|move)\s+(?:any\s+)?lines?\b|"
    r"换行|分行|断行|单行|多行|合并行|拆行",
    re.IGNORECASE,
)

JobStatus = Literal["queued", "running", "succeeded", "failed"]
ProgressStage = Literal[
    "accepted",
    "fetching_input",
    "planning",
    "calling_model",
    "merging",
    "writing_result",
    "completed",
    "failed",
]
CallbackDeliveryStatus = Literal["not_configured", "pending", "delivering", "delivered", "retrying", "failed"]
NumberValue: TypeAlias = StrictInt | StrictFloat


class CallbackConfig(StrictBaseModel):
    url: str = Field(min_length=1)
    events: list[Literal["job.succeeded", "job.failed"]] | None = None

    @model_validator(mode="after")
    def default_events(self) -> "CallbackConfig":
        if self.events == []:
            raise ValueError("callback.events must not be empty")
        if self.events is None:
            self.events = ["job.failed", "job.succeeded"]
        else:
            self.events = sorted(set(self.events))
        return self


class JobOptions(StrictBaseModel):
    priority: Literal["low", "normal"] = "normal"
    idempotency_mode: Literal["reject_duplicate", "return_existing"] = "reject_duplicate"


class CreateJobRequest(StrictBaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "client_request_id": "swagger-arithmetic-demo",
                    "job_type": "arithmetic",
                    "job_params": {
                        "a": 9,
                        "b": 3,
                    },
                    "metadata": {
                        "source": "swagger-ui",
                    },
                    "options": {
                        "priority": "normal",
                        "idempotency_mode": "return_existing",
                    },
                }
            ]
        }
    )

    client_request_id: str = Field(min_length=1, max_length=255)
    job_type: str = Field(min_length=1)
    job_params: dict[str, Any] = Field(default_factory=dict)
    callback: CallbackConfig | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    options: JobOptions | None = None


class JobProgress(StrictBaseModel):
    percent: int = Field(ge=0, le=100)
    stage: str | None = None
    message: str | None = None


class CallbackState(StrictBaseModel):
    status: CallbackDeliveryStatus
    attempt: int = Field(ge=0)
    last_error: CallbackErrorDetail | None = None
    next_retry_at: datetime | None = None


class JobCost(StrictBaseModel):
    currency: str = Field(min_length=1, max_length=8)
    amount: str = Field(min_length=1)
    final: bool


class JobUsage(StrictBaseModel):
    ai_call_count: int = Field(ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    final: bool


class JobEnvelope(StrictBaseModel):
    job_id: UUID
    client_request_id: str | None = None
    job_type: str
    job_status: JobStatus
    job_progress: JobProgress
    job_result: dict[str, Any] | None = None
    job_error: JobErrorDetail | None = None
    cost: JobCost | None = None
    usage: JobUsage | None = None
    callback: CallbackState
    status_url: str
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None

    @model_validator(mode="after")
    def validate_terminal_payload(self) -> "JobEnvelope":
        if self.job_status == "queued":
            if self.job_result is not None:
                raise ValueError("job_result must be null while job is not terminal")
            if self.job_error is not None:
                raise ValueError("job_error must be null while job is not terminal")
            if self.cost is not None:
                raise ValueError("cost must be null while job is not terminal")
            if self.usage is not None:
                raise ValueError("usage must be null while job is not terminal")
        elif self.job_status == "running":
            if self.job_error is not None:
                raise ValueError("job_error must be null while job is not terminal")
            if self.cost is not None:
                raise ValueError("cost must be null while job is not terminal")
            if self.usage is not None:
                raise ValueError("usage must be null while job is not terminal")
        elif self.job_status == "succeeded":
            if self.job_error is not None:
                raise ValueError("job_error must be null when job succeeded")
        elif self.job_status == "failed":
            if self.job_error is None:
                raise ValueError("job_error is required when job failed")
        return self


class JobResponseData(StrictBaseModel):
    job: JobEnvelope


class Artifact(StrictBaseModel):
    key: str
    type: str
    label: str
    apply_mode: Literal["replace", "append"] | None = None
    storage: Literal["oss_object"] | None = None
    oss_bucket: str | None = None
    oss_key: str | None = None
    oss_region: str | None = None
    content_hash: str | None = None
    content_size_bytes: int | None = None
    content: Any | None = None

    @field_validator("content_hash")
    @classmethod
    def validate_hash(cls, value: str | None) -> str | None:
        if value is not None and not HASH_RE.fullmatch(value):
            raise ValueError("content_hash must match sha256:<64 lowercase hex>")
        return value


class JobResult(StrictBaseModel):
    artifacts: list[Artifact | dict[str, Any]] = Field(default_factory=list)
    signals: dict[str, Any] = Field(default_factory=dict)

    @field_validator("artifacts", mode="before")
    @classmethod
    def validate_artifacts(cls, value: Any) -> list[Artifact | dict[str, Any]]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("artifacts must be a list")
        artifacts: list[Artifact | dict[str, Any]] = []
        for item in value:
            if isinstance(item, Artifact):
                artifacts.append(item)
                continue
            if not isinstance(item, dict):
                raise ValueError("artifact must be an object")
            if "key" in item:
                artifacts.append(Artifact.model_validate(item))
            else:
                artifacts.append(item)
        return artifacts


class RuntimeSystemFields(StrictBaseModel):
    trigger_request_id: str | None = Field(default=None, min_length=1, max_length=128)


class RuntimeFieldsBase(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # Workflow orchestration injects internal request metadata into persisted runtime fields.
    system: RuntimeSystemFields | None = Field(default=None, alias="_system")


class ExamplePairParams(StrictBaseModel):
    a: NumberValue
    b: NumberValue
    sleep_seconds: float = Field(default=0, ge=0, le=600)
    fail: bool = False
    fail_after_seconds: float = Field(default=0, ge=0, le=600)

    @field_validator("a", "b")
    @classmethod
    def validate_number(cls, value: NumberValue) -> NumberValue:
        if isinstance(value, bool):
            raise ValueError("value must be a number")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("value must be finite")
        return value


class ExamplePairRuntimeFields(StrictBaseModel):
    operation: Literal["pair"]


class ExamplePairResult(StrictBaseModel):
    a: NumberValue
    b: NumberValue
    result: NumberValue

    @field_validator("a", "b", "result")
    @classmethod
    def validate_number(cls, value: NumberValue) -> NumberValue:
        if isinstance(value, bool):
            raise ValueError("value must be a number")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("value must be finite")
        return value


class ExampleSleepParams(StrictBaseModel):
    message: str = Field(min_length=1, max_length=512)
    repeat: int = Field(default=1, ge=1, le=5)
    sleep_seconds: float = Field(default=0, ge=0, le=600)
    fail: bool = False
    fail_after_seconds: float = Field(default=0, ge=0, le=600)
    result_size_bytes: int = Field(default=0, ge=0, le=65_536)


class ExampleSleepRuntimeFields(StrictBaseModel):
    operation: Literal["sleep"]


class ExampleSleepResult(StrictBaseModel):
    message: str
    repeated: list[str]
    count: int = Field(ge=1, le=5)
    payload: str = Field(default="", max_length=65_536)


class ExampleCollectParams(StrictBaseModel):
    items: list[str] = Field(min_length=1, max_length=10)
    sleep_seconds: float = Field(default=0, ge=0, le=600)
    fail: bool = False
    fail_after_seconds: float = Field(default=0, ge=0, le=600)


class ExampleCollectRuntimeFields(StrictBaseModel):
    operation: Literal["collect"]


class ExampleCollectResult(StrictBaseModel):
    items: list[str]
    count: int = Field(ge=1, le=10)


class ExampleWorkflowParams(StrictBaseModel):
    mode: Literal["single", "chain", "group", "chord", "map", "starmap", "chunks"]
    label: str = Field(default="workflow-smoke", min_length=1, max_length=64)
    sleep_seconds: float = Field(default=0, ge=0, le=600)
    fail_node_key: str | None = Field(default=None, min_length=1, max_length=64)
    fail_after_seconds: float = Field(default=0, ge=0, le=600)
    result_size_bytes: int = Field(default=0, ge=0, le=65_536)


class ExampleWorkflowRuntimeFields(StrictBaseModel):
    operation: Literal["workflow_root"]


class ExampleWorkflowResult(StrictBaseModel):
    schema_version: int
    job_type: str
    workflow: dict[str, Any]


class JobRealLlmEchoParams(StrictBaseModel):
    model_id: str = Field(min_length=1, max_length=128)
    instruction: str = Field(default="用一句话确认真实 LLM 计费链路可用。", min_length=1, max_length=1000)
    source: dict[str, Any]

    @field_validator("source")
    @classmethod
    def validate_inline_source(cls, value: dict[str, Any]) -> dict[str, Any]:
        inline = value.get("inline")
        if not isinstance(inline, dict):
            raise ValueError("source.inline is required")
        text = inline.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("source.inline.text is required")
        if len(text.encode("utf-8")) > REAL_LLM_ECHO_INLINE_MAX_BYTES:
            raise ValueError(f"source.inline.text must be at most {REAL_LLM_ECHO_INLINE_MAX_BYTES} bytes")
        return value


class JobRealLlmEchoRuntimeFields(StrictBaseModel):
    model_id: str
    prompt_payload: dict[str, Any]


class JobRealLlmEchoResult(StrictBaseModel):
    artifacts: list[Artifact | dict[str, Any]] = Field(default_factory=list)
    signals: dict[str, Any] = Field(default_factory=dict)


class JobRealLlmDoubleEchoParams(JobRealLlmEchoParams):
    first_instruction: str = Field(default="第一次调用：用一句话确认真实 LLM 计费链路可用。", min_length=1, max_length=1000)
    second_instruction: str = Field(default="第二次调用：用另一句话确认同一 Job 的多次 LLM 计费可汇总。", min_length=1, max_length=1000)


class JobRealLlmDoubleEchoRuntimeFields(StrictBaseModel):
    model_id: str
    first_prompt_payload: dict[str, Any]
    second_prompt_payload: dict[str, Any]


class JobRealLlmDoubleEchoResult(StrictBaseModel):
    artifacts: list[Artifact | dict[str, Any]] = Field(default_factory=list)
    signals: dict[str, Any]


class ArithmeticParams(StrictBaseModel):
    a: NumberValue
    b: NumberValue

    @field_validator("a", "b")
    @classmethod
    def validate_nonzero_number(cls, value: NumberValue) -> NumberValue:
        if isinstance(value, bool):
            raise ValueError("value must be a number")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("value must be finite")
        if value == 0:
            raise ValueError("value must be non-zero")
        return value


class ArithmeticRuntimeFields(StrictBaseModel):
    operation: Literal["add_subtract_multiply_divide"]


class ArithmeticResult(StrictBaseModel):
    a: NumberValue
    b: NumberValue
    addition: NumberValue
    subtraction: NumberValue
    multiplication: NumberValue
    division: StrictFloat

    @field_validator("a", "b", "addition", "subtraction", "multiplication")
    @classmethod
    def validate_number(cls, value: NumberValue) -> NumberValue:
        if isinstance(value, bool):
            raise ValueError("value must be a number")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("value must be finite")
        return value

    @field_validator("division")
    @classmethod
    def validate_division(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("division must be finite")
        return value


class OssUrlRef(StrictBaseModel):
    public_url: str = Field(min_length=1)
    internal_url: str = Field(min_length=1)
    content_type: Literal["image/png", "image/jpeg", "image/webp"]
    sha256: str

    @field_validator("sha256")
    @classmethod
    def validate_bare_sha256(cls, value: str) -> str:
        if not BARE_HASH_RE.fullmatch(value):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return value


class PosterTitleImageReferenceImage(StrictBaseModel):
    public_url: str = Field(min_length=1)
    internal_url: str = Field(min_length=1)
    content_type: Literal["image/png", "image/jpeg", "image/webp"]
    sha256: str

    @field_validator("sha256")
    @classmethod
    def validate_bare_sha256(cls, value: str) -> str:
        if not BARE_HASH_RE.fullmatch(value):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return value


class PosterTitleImageModelOptions(StrictBaseModel):
    size: Literal["1024x1024", "1536x1024", "1024x1536", "auto"] = "auto"
    quality: Literal["low", "medium", "high", "auto"] = "high"
    draw_count: int = Field(default=1, ge=1, le=4)
    # Public delivery contract: callers request a transparent title layer.
    # The current gpt-image-2 provider call still uses background=auto and
    # local green-screen post-processing to produce that transparent output.
    background: Literal["transparent"] = "transparent"
    output_format: Literal["png"] = "png"

    @model_validator(mode="after")
    def validate_transparent_output(self) -> "PosterTitleImageModelOptions":
        if self.background == "transparent" and self.output_format != "png":
            raise ValueError("background=transparent requires output_format=png")
        return self


class PosterTitleImagePromptOverrides(StrictBaseModel):
    style_probe: str | None = Field(default=None, min_length=1, max_length=4000)
    additional_prompt: str | None = Field(
        default=None,
        min_length=1,
        max_length=4000,
        description=(
            "Additional visual or style preference only. It must not define, add, remove, merge, split, reorder, "
            "or reposition title_text line breaks."
        ),
    )
    layout_rules: str | None = Field(
        default=None,
        min_length=1,
        max_length=4000,
        description=(
            "Visual layout preference only. It must not define, add, remove, merge, split, reorder, or reposition "
            "title_text line breaks."
        ),
    )

    @field_validator("additional_prompt", "layout_rules")
    @classmethod
    def validate_no_line_break_control(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if POSTER_TITLE_IMAGE_LINE_CONTROL_OVERRIDE_PATTERN.search(value):
            raise ValueError(
                "prompt_overrides.additional_prompt and prompt_overrides.layout_rules must not control title_text line breaks"
            )
        return value


class PosterTitleImageItemParams(StrictBaseModel):
    item_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    language: str = Field(min_length=1, max_length=16)
    title_text: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "Only source of caller-specified hard line breaks. No LF means no caller-specified hard line break; "
            "The service may wrap when needed for fit and balance. "
            "Each LF position determines a hard line break position. "
            f"LF separates caller-specified lines, up to {POSTER_TITLE_IMAGE_MAX_TITLE_LINES} lines."
        ),
    )
    model_id: str | None = Field(default=None, min_length=1, max_length=128)
    model_options: PosterTitleImageModelOptions = Field(default_factory=PosterTitleImageModelOptions)
    reference_image: PosterTitleImageReferenceImage
    prompt_overrides: PosterTitleImagePromptOverrides | None = None

    @field_validator("language")
    @classmethod
    def validate_language_subset(cls, value: str) -> str:
        if value not in supported_language_codes():
            raise ValueError("language is not supported by poster_title_image")
        return value

    @field_validator("title_text")
    @classmethod
    def validate_title_text_line_breaks(cls, value: str) -> str:
        if any(separator in value for separator in POSTER_TITLE_IMAGE_DISALLOWED_LINE_BREAKS):
            raise ValueError("title_text line breaks must use LF \\n only")
        if "<br" in value.lower():
            raise ValueError("title_text does not support HTML line break tags; use LF \\n")
        if value.count("\n") > POSTER_TITLE_IMAGE_MAX_HARD_LINE_BREAKS:
            raise ValueError(f"title_text must use at most {POSTER_TITLE_IMAGE_MAX_TITLE_LINES} lines")
        lines = value.split("\n")
        if any(not line.strip() for line in lines):
            raise ValueError("title_text lines must not be empty")
        return value


class PosterTitleImageParams(StrictBaseModel):
    items: list[PosterTitleImageItemParams] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_items(self) -> "PosterTitleImageParams":
        item_ids = [item.item_id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("items[].item_id must be unique")
        model_ids = {item.model_id for item in self.items if item.model_id is not None}
        if len(model_ids) > 1:
            raise ValueError("items[].model_id must be the same within one poster_title_image job")
        return self


class AudioStemSeparationInputObject(StrictBaseModel):
    public_url: str = Field(min_length=1)
    internal_url: str = Field(min_length=1)
    content_type: Literal["audio/wav"]
    sha256: str

    @field_validator("sha256")
    @classmethod
    def validate_bare_sha256(cls, value: str) -> str:
        if not BARE_HASH_RE.fullmatch(value):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return value


class AudioStemSeparationParams(StrictBaseModel):
    input_audio: AudioStemSeparationInputObject
    max_duration_seconds: float | None = Field(default=None, gt=0, le=3600)


class AudioStemSeparationRuntimeFields(RuntimeFieldsBase):
    operation: Literal["audio_stem_separation"] = "audio_stem_separation"
    onnx_model_version: str = Field(min_length=1, max_length=128)
    execution_provider: str = Field(min_length=1, max_length=128)
    segment_seconds: float = Field(gt=0)
    overlap_ratio: float = Field(gt=0, lt=1)


class AudioStemSeparationOutputObject(StrictBaseModel):
    public_url: str
    internal_url: str
    content_type: Literal["audio/wav"]
    sha256: str

    @field_validator("sha256")
    @classmethod
    def validate_output_sha256(cls, value: str) -> str:
        if not BARE_HASH_RE.fullmatch(value):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return value


class AudioStemSeparationStemOutputs(StrictBaseModel):
    drums: AudioStemSeparationOutputObject
    bass: AudioStemSeparationOutputObject
    other: AudioStemSeparationOutputObject
    vocals: AudioStemSeparationOutputObject


class AudioStemSeparationDurationMs(StrictBaseModel):
    io: int = Field(ge=0)
    inference: int = Field(ge=0)
    total: int = Field(ge=0)


class AudioStemSeparationResult(StrictBaseModel):
    schema_version: Literal["default"] = "default"
    job_type: Literal["audio_stem_separation"] = "audio_stem_separation"
    stems: AudioStemSeparationStemOutputs
    source_duration_seconds: float = Field(gt=0)
    segment_count: int = Field(ge=1)
    sample_rate: Literal[44100] = 44100
    channels: Literal[2] = 2
    onnx_model_version: str = Field(min_length=1, max_length=128)
    execution_provider: str = Field(min_length=1, max_length=128)
    duration_ms: AudioStemSeparationDurationMs


class PosterTitleImageRuntimeFields(RuntimeFieldsBase):
    operation: Literal["poster_title_image"] = "poster_title_image"
    style_probe_model_id: str = Field(min_length=1, max_length=128)
    generation_model_id: str = Field(min_length=1, max_length=128)
    image_adapter: str = Field(min_length=1, max_length=128)


class PosterTitleImageStyleProbeParams(StrictBaseModel):
    style_key: str = Field(min_length=1)
    reference_image: PosterTitleImageReferenceImage
    style_prompt: str = Field(min_length=1, max_length=8000)
    style_probe_model_id: str = Field(min_length=1, max_length=128)
    image_adapter: str = Field(min_length=1, max_length=128)


class PosterTitleImageStyleProbeRuntimeFields(RuntimeFieldsBase):
    operation: Literal["poster_title_image_style_probe"] = "poster_title_image_style_probe"
    style_probe_model_id: str = Field(min_length=1, max_length=128)
    image_adapter: str = Field(min_length=1, max_length=128)


class PosterTitleImageDurationMs(StrictBaseModel):
    ai_model: int = Field(ge=0)
    total: int = Field(ge=0)


class PosterTitleImageStyleProbeResult(StrictBaseModel):
    style_key: str = Field(min_length=1)
    style_desc: str = Field(min_length=1)
    duration_ms: PosterTitleImageDurationMs


class PosterTitleImageGenerateItemParams(StrictBaseModel):
    item: PosterTitleImageItemParams
    probe_node_key: str = Field(min_length=1, max_length=128)
    style_probe_model_id: str = Field(min_length=1, max_length=128)
    image_adapter: str = Field(min_length=1, max_length=128)


class PosterTitleImageGenerateItemRuntimeFields(RuntimeFieldsBase):
    operation: Literal["poster_title_image_generate_item"] = "poster_title_image_generate_item"
    generation_model_id: str = Field(min_length=1, max_length=128)
    style_probe_model_id: str = Field(min_length=1, max_length=128)
    image_adapter: str = Field(min_length=1, max_length=128)


PosterTitleImageItemStatus = Literal["pending", "running", "succeeded", "failed"]


class PosterTitleImageObject(StrictBaseModel):
    public_url: str
    internal_url: str
    content_type: Literal["image/png", "image/jpeg", "image/webp"]
    sha256: str

    @field_validator("sha256")
    @classmethod
    def validate_output_sha256(cls, value: str) -> str:
        if not BARE_HASH_RE.fullmatch(value):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return value


class PosterTitleImageImage(StrictBaseModel):
    object: PosterTitleImageObject
    width: int = Field(ge=1)
    height: int = Field(ge=1)


class PosterTitleImageError(StrictBaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class PosterTitleImageResultItem(StrictBaseModel):
    item_id: str
    language: str
    status: PosterTitleImageItemStatus
    images: list[PosterTitleImageImage] = Field(default_factory=list)
    error: PosterTitleImageError | None = None


class PosterTitleImageBatchSummary(StrictBaseModel):
    total: int = Field(ge=1)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    running: int = Field(ge=0)
    pending: int = Field(ge=0)


class PosterTitleImageGenerateItemResult(StrictBaseModel):
    item: PosterTitleImageResultItem
    duration_ms: PosterTitleImageDurationMs


class PosterTitleImageJoinParams(StrictBaseModel):
    items: list[PosterTitleImageItemParams] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_items_contract(self) -> "PosterTitleImageJoinParams":
        PosterTitleImageParams.model_validate({"items": [item.model_dump() for item in self.items]})
        return self


class PosterTitleImageJoinRuntimeFields(RuntimeFieldsBase):
    operation: Literal["poster_title_image_join"] = "poster_title_image_join"


class PosterTitleImageResult(StrictBaseModel):
    schema_version: Literal["default"] = "default"
    job_type: Literal["poster_title_image"] = "poster_title_image"
    batch_summary: PosterTitleImageBatchSummary
    items: list[PosterTitleImageResultItem] = Field(min_length=1)
    duration_ms: PosterTitleImageDurationMs

    @model_validator(mode="after")
    def validate_summary(self) -> "PosterTitleImageResult":
        counts = {
            "succeeded": sum(1 for item in self.items if item.status == "succeeded"),
            "failed": sum(1 for item in self.items if item.status == "failed"),
            "running": sum(1 for item in self.items if item.status == "running"),
            "pending": sum(1 for item in self.items if item.status == "pending"),
        }
        if self.batch_summary.total != len(self.items):
            raise ValueError("batch_summary.total must equal items count")
        for key, value in counts.items():
            if getattr(self.batch_summary, key) != value:
                raise ValueError(f"batch_summary.{key} must match items")
        for item in self.items:
            if item.status == "succeeded" and (not item.images or item.error is not None):
                raise ValueError("succeeded item requires images and null error")
            if item.status == "failed" and (item.images or item.error is None):
                raise ValueError("failed item requires empty images and non-null error")
            if item.status in {"pending", "running"} and (item.images or item.error is not None):
                raise ValueError("pending/running item requires empty images and null error")
        return self


CreateJobResponse = JobEnvelope
JobStatusResponse = JobEnvelope


def __getattr__(name: str) -> Any:
    if name in {"CallbackEnvelope", "CallbackResponseEnvelope"}:
        from app.schemas import callbacks as callback_schemas

        return getattr(callback_schemas, name)
    raise AttributeError(name)

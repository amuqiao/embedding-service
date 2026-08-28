from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import typer


@dataclass(frozen=True)
class JobSmokeOptions:
    confirm_run: bool
    client_request_id: str | None
    expect_status: str


ConfirmRunOption = Annotated[
    bool,
    typer.Option("--confirm-run", help="确认本命令会创建真实 Job 并写入 Job/Outbox/Callback 数据。"),
]
ConfirmCostOption = Annotated[
    bool,
    typer.Option("--confirm-cost", help="确认本命令会调用真实模型或 provider，并可能产生费用。"),
]
ConfirmUploadOption = Annotated[
    bool,
    typer.Option("--confirm-upload", help="确认本命令可能上传本地文件到对象存储。"),
]
ClientRequestIdOption = Annotated[
    str | None,
    typer.Option("--client-request-id", help="显式 client_request_id；默认由场景自动生成。"),
]
ExpectStatusOption = Annotated[
    str,
    typer.Option("--expect-status", help="期望终态：auto、succeeded 或 failed。"),
]


def job_smoke_options(
    *,
    confirm_run: bool,
    client_request_id: str | None,
    expect_status: str = "auto",
) -> JobSmokeOptions:
    return JobSmokeOptions(
        confirm_run=confirm_run,
        client_request_id=client_request_id,
        expect_status=expect_status,
    )

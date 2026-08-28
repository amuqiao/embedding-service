from __future__ import annotations


class FlowError(RuntimeError):
    exit_code: int

    def __init__(self, message: str, *, exit_code: int = 4) -> None:
        super().__init__(message)
        self.exit_code = exit_code

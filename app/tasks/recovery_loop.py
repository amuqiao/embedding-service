from __future__ import annotations

import logging
import time

from app.core.config import settings
from app.core.logging import configure_logging
from app.tasks.recovery import run_recovery
from app.tasks.runtime import ensure_worker_runtime_initialized

logger = logging.getLogger(__name__)


def main() -> None:
    configure_logging()
    ensure_worker_runtime_initialized()
    while True:
        try:
            result = run_recovery()
            logger.info("recovery_loop_completed result=%s", result)
        except Exception:
            logger.exception("recovery_loop_failed")
        time.sleep(settings.job.recovery_interval_seconds)


if __name__ == "__main__":
    main()

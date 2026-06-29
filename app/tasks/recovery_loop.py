from __future__ import annotations

import logging
import time

from app.core.config import settings
from app.core.logging import configure_logging
from app.tasks.recovery import run_recovery

logger = logging.getLogger(__name__)


def main() -> None:
    configure_logging()
    while True:
        try:
            result = run_recovery()
            logger.info("recovery_loop_completed result=%s", result)
        except Exception:
            logger.exception("recovery_loop_failed")
        time.sleep(settings.job.recovery_interval_seconds)


if __name__ == "__main__":
    main()

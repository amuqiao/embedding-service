import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

from app.core.registry_checks import validate_all_registries
from app.main import app
from app.jobs.types.register import register_all_job_types


def main() -> None:
    register_all_job_types()
    validate_all_registries(app)
    print("registry consistency ok")


if __name__ == "__main__":
    main()

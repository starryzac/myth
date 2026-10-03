"""Import the deterministic demo into the configured, already migrated database."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from app.db.session import create_database_engine  # noqa: E402
from app.services.demo_seed import seed_demo  # noqa: E402


def main() -> None:
    engine = create_database_engine()
    try:
        print(seed_demo(engine).model_dump_json(indent=2))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()

"""Run the deterministic FAQ and workflow gates on a dedicated local database."""

import argparse
import os
import subprocess
import sys
from urllib.parse import urlsplit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("fixture", "ollama"), default="fixture")
    args = parser.parse_args()
    url = os.getenv("CLINICFLOW_TEST_DATABASE_URL", "")
    # Legacy integration fixtures reset tables. This command only accepts a
    # dedicated, explicitly configured local database for that reason.
    if not is_local_test_database(url):
        parser.error("CLINICFLOW_TEST_DATABASE_URL must point to a local clinicflow_test or clinicflow_ci database")
    env = {**os.environ, "DATABASE_URL": url}
    for arguments in (
        ("-m", "alembic", "upgrade", "head"),
        ("-m", "pytest", "-q"),
        ("-m", "clinicflow.evals", "--extended", "--provider", args.provider),
    ):
        result = subprocess.run([sys.executable, *arguments], env=env, check=False)
        if result.returncode:
            return result.returncode
    return 0


def is_local_test_database(url: str) -> bool:
    target = urlsplit(url)
    return target.hostname in {"localhost", "127.0.0.1", "::1"} and target.path.lstrip("/").startswith(
        ("clinicflow_test", "clinicflow_ci", "clinicflow_smoke")
    )


if __name__ == "__main__":
    raise SystemExit(main())

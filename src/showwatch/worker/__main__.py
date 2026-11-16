"""Entry point for the worker container: `python -m showwatch.worker`."""

import asyncio

from showwatch.core.lifecycle import run_until_stopped
from showwatch.core.settings import get_settings


def main() -> None:
    asyncio.run(run_until_stopped("worker", get_settings()))


if __name__ == "__main__":
    main()

"""Entry point for the bot container: `python -m showwatch.bot`."""

import asyncio

from showwatch.core.lifecycle import run_until_stopped
from showwatch.core.settings import get_settings


def main() -> None:
    settings = get_settings()
    settings.require_bot()
    asyncio.run(run_until_stopped("bot", settings))


if __name__ == "__main__":
    main()

"""Entry point for the bot container: `python -m showwatch.bot`."""

import sys

import structlog

from showwatch.bot.app import ALLOWED_UPDATES, build_application
from showwatch.core.db import create_engine, create_sessionmaker
from showwatch.core.logging import configure_logging
from showwatch.core.settings import get_settings


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    log = structlog.get_logger("bot")
    try:
        engine = create_engine(settings.database_url)
        application = build_application(settings, create_sessionmaker(engine), engine=engine)
        application.run_polling(allowed_updates=ALLOWED_UPDATES)
    except Exception:
        # Logged through structlog so the redaction runs: a raw traceback can contain the token.
        log.exception("bot_crashed")
        sys.exit(1)


if __name__ == "__main__":
    main()

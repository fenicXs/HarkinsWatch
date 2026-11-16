"""Shared start-up and shutdown for the bot and worker processes."""

import asyncio
import signal

import structlog

from showwatch import __version__
from showwatch.core.db import check_connection, create_engine
from showwatch.core.logging import configure_logging
from showwatch.core.settings import Settings


async def run_until_stopped(name: str, settings: Settings) -> None:
    """Check the database, log `ready`, then wait for SIGINT or SIGTERM."""
    configure_logging(settings.log_level)
    log = structlog.get_logger(name)
    engine = create_engine(settings.database_url)
    try:
        await check_connection(engine)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:  # Windows event loops have no signal handlers
                pass
        log.info("ready", service=name, version=__version__)
        await stop.wait()
        log.info("stopping", service=name)
    finally:
        await engine.dispose()

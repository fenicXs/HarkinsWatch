"""Entry point for the worker container: `python -m showwatch.worker`."""

import asyncio
import os
import signal
import socket
import sys
from functools import partial
from pathlib import Path

import structlog
from telegram import Bot

from showwatch import __version__
from showwatch.chains.harkins.scraper import HarkinsChain
from showwatch.chains.politeness import Politeness
from showwatch.chains.registry import ChainRegistry
from showwatch.core.db import check_connection, create_engine, create_sessionmaker
from showwatch.core.logging import configure_logging
from showwatch.core.settings import Settings, get_settings
from showwatch.llm.client import LLMClient, vision_endpoints
from showwatch.llm.tasks.seat_vision import find_points
from showwatch.notify import TelegramNotifier
from showwatch.worker.handlers import WorkerEnv
from showwatch.worker.runner import run_forever
from showwatch.worker.scheduler import run_scheduler


async def run(settings: Settings) -> None:
    log = structlog.get_logger("worker")
    token = settings.require_token()
    engine = create_engine(settings.database_url)
    factory = create_sessionmaker(engine)
    try:
        await check_connection(engine)
        politeness = Politeness(factory, settings.chain_request_budget_per_min)
        registry = ChainRegistry()
        llm = LLMClient(vision_endpoints(settings))
        vision = partial(find_points, llm) if llm.configured else None
        registry.register(
            HarkinsChain(politeness, profile_dir=Path(settings.harkins_profile_dir), vision=vision)
        )

        async with Bot(token.get_secret_value()) as bot:
            env = WorkerEnv(
                session_factory=factory,
                notifier=TelegramNotifier(bot),
                registry=registry,
                politeness=politeness,
                settings=settings,
            )
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, stop.set)
                except NotImplementedError:  # Windows event loops have no signal handlers
                    pass
            worker_id = f"{socket.gethostname()}:{os.getpid()}"
            log.info("ready", service="worker", version=__version__, worker_id=worker_id)
            await asyncio.gather(run_forever(env, worker_id, stop), run_scheduler(env, stop))
            log.info("stopping", service="worker")
    finally:
        await engine.dispose()


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        asyncio.run(run(settings))
    except Exception:
        # Logged through structlog so the redaction runs: a raw traceback can contain the token.
        structlog.get_logger("worker").exception("worker_crashed")
        sys.exit(1)


if __name__ == "__main__":
    main()

"""Job handlers. Each takes the shared `WorkerEnv` and the claimed `Job`."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from showwatch.chains.politeness import Politeness
from showwatch.chains.registry import ChainRegistry
from showwatch.core.settings import Settings
from showwatch.llm.client import LLMClient
from showwatch.notify import Notifier


@dataclass(frozen=True)
class WorkerEnv:
    session_factory: async_sessionmaker[AsyncSession]
    notifier: Notifier
    registry: ChainRegistry
    politeness: Politeness
    settings: Settings
    llm: LLMClient | None = None  # text model, for labels the plain rules cannot place

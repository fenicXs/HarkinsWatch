"""Read-only lookups for the seeded reference tables: cities, chains, theatres."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from showwatch.db import tables
from showwatch.domain.models import ChainInfo, City, Theatre


async def list_cities(session: AsyncSession) -> list[City]:
    rows = await session.scalars(select(tables.City).order_by(tables.City.id))
    return [City.model_validate(r) for r in rows]


async def get_city(session: AsyncSession, city_id: int) -> City | None:
    row = await session.get(tables.City, city_id)
    return City.model_validate(row) if row else None


async def get_city_by_code(session: AsyncSession, code: str) -> City | None:
    row = await session.scalar(select(tables.City).where(tables.City.code == code))
    return City.model_validate(row) if row else None


async def get_chain_by_code(session: AsyncSession, code: str) -> ChainInfo | None:
    row = await session.scalar(select(tables.Chain).where(tables.Chain.code == code))
    return ChainInfo.model_validate(row) if row else None


async def list_enabled_chains(session: AsyncSession) -> list[ChainInfo]:
    rows = await session.scalars(
        select(tables.Chain).where(tables.Chain.enabled.is_(True)).order_by(tables.Chain.id)
    )
    return [ChainInfo.model_validate(r) for r in rows]


async def list_theatres(session: AsyncSession, chain_id: int, city_id: int) -> list[Theatre]:
    rows = await session.scalars(
        select(tables.Theatre)
        .where(tables.Theatre.chain_id == chain_id, tables.Theatre.city_id == city_id)
        .order_by(tables.Theatre.id)
    )
    return [Theatre.model_validate(r) for r in rows]

"""An enrollment token's max_uses must hold under concurrent redemption.

``enroll_agent`` checked ``token.is_valid(now)`` on a SELECT, then wrote
``used_count + 1`` computed in Python. pysqlite opens no transaction for the
SELECT, so N installers redeeming one single-use token at the same moment (a DC
pasting the one-liner into several jumpbox shells, or a config-management fan-out)
all passed the check and each enrolled an agent — while the stored counter read
``1/1 used``, hiding the overrun from the token list. The bound that decides who
may put a beacon on the customer network did not bound anything.

Uses a FILE-backed SQLite with the production pool; the shared in-memory
StaticPool serialises everything onto one connection and cannot show the race.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class _Req:
    headers: dict = {}
    client = None


@pytest.fixture
def file_db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'enroll.db'}")
    SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _init():
        from database import Base
        import models  # noqa: F401

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    yield SessionLocal
    asyncio.run(engine.dispose())


async def _mint(SessionLocal, *, max_uses: int, token: str) -> None:
    from models import EnrollmentToken

    now = datetime.utcnow()
    async with SessionLocal() as db:
        db.add(EnrollmentToken(token=token, created_at=now,
                               expires_at=now + timedelta(hours=1),
                               max_uses=max_uses, used_count=0, revoked=False))
        await db.commit()


async def _redeem_concurrently(SessionLocal, token: str, n: int):
    from api.agents import EnrollRequest, enroll_agent

    async def _one(i):
        async with SessionLocal() as db:
            try:
                return await enroll_agent(
                    EnrollRequest(token=token, hostname=f"jump-{i}", os="linux"), _Req(), db)
            except HTTPException as exc:
                return exc

    return await asyncio.gather(*(_one(i) for i in range(n)))


async def _counts(SessionLocal, token: str):
    from models import Agent, EnrollmentToken

    async with SessionLocal() as db:
        agents = (await db.execute(select(func.count()).select_from(Agent))).scalar_one()
        used = (await db.execute(
            select(EnrollmentToken.used_count).where(EnrollmentToken.token == token)
        )).scalar_one()
        return agents, used


def test_single_use_token_enrolls_exactly_one_agent_under_concurrency(file_db):
    async def _go():
        await _mint(file_db, max_uses=1, token="cxs_single")
        outs = await _redeem_concurrently(file_db, "cxs_single", 5)
        return outs, await _counts(file_db, "cxs_single")

    outs, (agents, used) = asyncio.run(_go())
    enrolled = [o for o in outs if isinstance(o, dict)]
    denied = [o for o in outs if isinstance(o, HTTPException)]
    assert len(enrolled) == 1, f"{len(enrolled)} agents enrolled on a single-use token"
    assert agents == 1
    assert used == 1
    assert all(d.status_code == 403 and d.detail["code"] == "ENROLL_DENIED" for d in denied)


def test_multi_use_token_never_exceeds_max_uses(file_db):
    async def _go():
        await _mint(file_db, max_uses=3, token="cxs_three")
        outs = await _redeem_concurrently(file_db, "cxs_three", 8)
        return outs, await _counts(file_db, "cxs_three")

    outs, (agents, used) = asyncio.run(_go())
    enrolled = [o for o in outs if isinstance(o, dict)]
    assert len(enrolled) == 3
    assert agents == 3 and used == 3
    assert sorted(o["remaining_uses"] for o in enrolled) == [0, 1, 2]

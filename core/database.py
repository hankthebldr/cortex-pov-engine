"""
CortexSim database setup — async SQLAlchemy with SQLite.
Database file lives at {CORTEXSIM_BASE_DIR}/data/cortexsim.db.
"""

import os
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config import settings

# Resolve absolute path to the DB file
_db_path = os.path.join(settings.CORTEXSIM_BASE_DIR, "data", "cortexsim.db")
_db_dir = os.path.dirname(_db_path)

# Ensure the data directory exists at import time so the URL is always valid.
os.makedirs(_db_dir, exist_ok=True)

DATABASE_URL = f"sqlite+aiosqlite:///{_db_path}"

engine = create_async_engine(
    DATABASE_URL,
    echo=(settings.CORTEXSIM_ENV == "development"),
    connect_args={"check_same_thread": False},
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def init_db() -> None:
    """Create all tables.  Called from FastAPI startup handler."""
    async with engine.begin() as conn:
        # Import models so their metadata is registered before create_all
        import models  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    """FastAPI dependency — yields an async session."""
    async with AsyncSessionLocal() as session:
        yield session

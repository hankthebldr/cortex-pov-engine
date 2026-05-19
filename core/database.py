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
        # Idempotent column additions for tables that pre-date a schema bump.
        # SQLAlchemy's create_all only creates missing TABLES, not missing
        # COLUMNS — so a CortexSim dev box with an existing cortexsim.db
        # would otherwise SELECT-fail on the new columns.
        await conn.run_sync(_migrate_results_columns)


def _migrate_results_columns(connection) -> None:
    """Add Phase 1 columns to the ``results`` table if absent.

    All columns are nullable so the ADD COLUMN is non-blocking and the
    existing rows simply hold NULL until a new run seeds them.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(connection)
    if "results" not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns("results")}

    additions = [
        ("ttp_ref", "VARCHAR"),
        ("detection_id", "VARCHAR"),
        ("detection_kind", "VARCHAR"),
        ("detection_logic", "TEXT"),
        ("detection_severity", "VARCHAR"),
        ("mitre_technique", "VARCHAR"),
    ]
    for col_name, col_type in additions:
        if col_name in existing:
            continue
        connection.execute(text(f"ALTER TABLE results ADD COLUMN {col_name} {col_type}"))


async def get_db():
    """FastAPI dependency — yields an async session."""
    async with AsyncSessionLocal() as session:
        yield session

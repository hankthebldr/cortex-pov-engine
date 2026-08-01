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
        await conn.run_sync(_migrate_scenarios_columns)
        await conn.run_sync(_migrate_assertion_columns)


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
        # Phase 2 — verification
        ("verification_xql", "TEXT"),
        ("kpi_contribution", "JSON"),
        ("kpi_verdict", "VARCHAR"),
        ("verified_at", "DATETIME"),
    ]
    for col_name, col_type in additions:
        if col_name in existing:
            continue
        connection.execute(text(f"ALTER TABLE results ADD COLUMN {col_name} {col_type}"))


def _migrate_scenarios_columns(connection) -> None:
    """Add later-phase columns to the ``scenarios`` table if absent.

    Same rationale as ``_migrate_results_columns``: ``create_all`` never adds
    columns to an existing table, so a box carrying an older cortexsim.db would
    SELECT-fail on these. All are nullable (or JSON defaulting to ``[]`` at the
    ORM layer), so the ADD COLUMN is non-blocking.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(connection)
    if "scenarios" not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns("scenarios")}

    additions = [
        ("cgo_anchor", "JSON"),          # causality contract
        ("pov_scenario_id", "VARCHAR"),  # UC/TC payload join
        ("tc_refs", "JSON"),             # full TC evidence set
        # Phase 2 — measurement contract
        ("validation_methodology", "VARCHAR"),
        ("methodology_family", "VARCHAR"),
        ("primary_kpi", "VARCHAR"),
        ("threshold", "JSON"),
        ("success_criteria", "TEXT"),
        ("moat_tier", "VARCHAR"),
        ("correlation_window_seconds", "INTEGER"),
        ("stitching_key", "VARCHAR"),
        ("required_planes_in_incident", "JSON"),
        # Phase 3 — license gating
        ("required_base_platform", "JSON"),
        ("required_addons", "JSON"),
    ]
    for col_name, col_type in additions:
        if col_name in existing:
            continue
        connection.execute(text(f"ALTER TABLE scenarios ADD COLUMN {col_name} {col_type}"))

    if "runs" in inspector.get_table_names():
        run_existing = {col["name"] for col in inspector.get_columns("runs")}
        for col_name, col_type in [("tc_verdict", "VARCHAR"), ("tc_verdict_detail", "JSON")]:
            if col_name in run_existing:
                continue
            connection.execute(text(f"ALTER TABLE runs ADD COLUMN {col_name} {col_type}"))


def _migrate_assertion_columns(connection) -> None:
    """Add later columns to the assertion tables if absent.

    The three tables themselves are created by ``create_all`` on any box that
    has never seen them, so this pass is a no-op on a fresh DB. It exists for
    the same reason the other two do: ``create_all`` never adds a COLUMN to an
    existing table, so once these tables ship, every subsequent column has to
    land here or an upgraded box SELECT-fails. Keeping the (currently complete)
    column list here makes that a one-line change instead of a re-derivation.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(connection)
    tables = set(inspector.get_table_names())

    additions: dict[str, list[tuple[str, str]]] = {
        "assertions": [
            ("index_meta", "JSON"),
            ("tc_scoreable", "BOOLEAN"),
            ("scope_limitations", "TEXT"),
            ("threshold", "JSON"),
            ("required_base_platform", "JSON"),
            ("required_addons", "JSON"),
            ("spec", "JSON"),
            ("source_file", "VARCHAR"),
        ],
        "assertion_runs": [
            ("tenant", "VARCHAR"),
            ("trigger_run_id", "VARCHAR"),
            ("context", "JSON"),
            ("tc_verdict", "VARCHAR"),
            ("tc_verdict_detail", "JSON"),
            ("reason", "VARCHAR"),
        ],
        "assertion_checks": [
            ("verification_xql", "TEXT"),
            ("measured_value", "FLOAT"),
            ("measured_unit", "VARCHAR"),
            ("taxonomy_code", "VARCHAR"),
            ("remediation", "TEXT"),
            ("detail", "TEXT"),
            ("negative_control", "TEXT"),
            ("kpi_contribution", "JSON"),
            ("kpi_verdict", "VARCHAR"),
            ("verified_at", "DATETIME"),
            ("sample_rows", "JSON"),
            ("detection_id", "VARCHAR"),
        ],
    }

    for table, cols in additions.items():
        if table not in tables:
            continue
        existing = {col["name"] for col in inspector.get_columns(table)}
        for col_name, col_type in cols:
            if col_name in existing:
                continue
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"))


async def get_db():
    """FastAPI dependency — yields an async session."""
    async with AsyncSessionLocal() as session:
        yield session

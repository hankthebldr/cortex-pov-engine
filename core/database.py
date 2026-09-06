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
# A read-only parent makes this raise at IMPORT time, i.e. before the lifespan
# handler that knows how to explain it — swallow it here so the failure surfaces
# at init_db() as a named DB_NOT_WRITABLE instead of an import traceback.
try:
    os.makedirs(_db_dir, exist_ok=True)
except OSError:
    pass

DATABASE_URL = f"sqlite+aiosqlite:///{_db_path}"

engine = create_async_engine(
    DATABASE_URL,
    echo=settings.CORTEXSIM_SQL_ECHO,
    connect_args={"check_same_thread": False},
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Boot diagnostics
#
# `config.validate_master_key` is the model: a named condition, a plain-English
# cause, and a copy-pasteable fix. Two equally likely deployment faults had
# nothing — a corrupt cortexsim.db and a read-only data/ mount both produced a
# raw SQLAlchemy traceback and exit 3, indistinguishable from a code defect.
#
# Exit code 4 (distinct from the master-key guard's 3) so an operator scripting
# the boot can branch on "the storage is wrong" vs "the secret is wrong".
# ---------------------------------------------------------------------------

DB_BOOT_EXIT_CODE = 4


class DatabaseBootError(RuntimeError):
    """A storage-layer fault that CortexSim can name and remediate.

    ``code`` is stable and machine-readable; ``str(exc)`` is the operator-facing
    message and is printed INSTEAD of a traceback.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def db_path() -> str:
    """Absolute path of the run-history SQLite file."""
    return _db_path


def classify_db_error(exc: BaseException) -> DatabaseBootError:
    """Translate a SQLAlchemy/sqlite3 boot failure into a named, fixable one.

    Matched on the sqlite3 message text rather than the exception class because
    SQLAlchemy wraps every one of these in ``OperationalError`` /
    ``DatabaseError`` — the class tells you nothing, the text tells you
    everything. Anything unrecognised falls through to ``DB_INIT_FAILED`` with
    the original text, so a new failure mode is still legible even before it has
    a dedicated branch.
    """
    text = str(exc).lower()
    path = _db_path

    if "file is not a database" in text or "file is encrypted" in text:
        return DatabaseBootError("DB_CORRUPT", (
            f"CORRUPT DATABASE — {path} exists but is not a SQLite database.\n"
            f"\n"
            f"Usually a truncated copy, a Git LFS pointer committed in place of "
            f"the file, or an unrelated file mounted at that path.\n"
            f"\n"
            f"CortexSim stores ONLY run history here. Scenarios, TTP cards, "
            f"adapter packs and the UC/TC index all load from disk at boot, so "
            f"moving this file aside costs you past run records and nothing "
            f"else.\n"
            f"\n"
            f"To fix:\n"
            f"  1. mv {path} {path}.bad\n"
            f"  2. restart CortexSim (the schema is recreated automatically)\n"
        ))

    if "unable to open database file" in text or "readonly database" in text \
            or "attempt to write a readonly database" in text:
        try:
            uid = os.getuid()
        except AttributeError:  # pragma: no cover - non-POSIX
            uid = -1
        return DatabaseBootError("DB_NOT_WRITABLE", (
            f"DATA DIRECTORY NOT WRITABLE — cannot open {path} for writing.\n"
            f"\n"
            f"The data directory is read-only, or it is owned by a different "
            f"uid than the one CortexSim runs as (uid {uid}).\n"
            f"\n"
            f"To fix:\n"
            f"  - In Docker: check for ':ro' on the volume mount for "
            f"{_db_dir} in docker-compose.yml, and that the named volume is "
            f"not backed by a read-only filesystem.\n"
            f"  - On a host: chown -R {uid} {_db_dir}\n"
            f"  - Confirm with: touch {_db_dir}/.write-test\n"
        ))

    if "disk is full" in text or "database or disk is full" in text \
            or "no space left" in text:
        return DatabaseBootError("DB_DISK_FULL", (
            f"DISK FULL — SQLite cannot extend {path}.\n"
            f"\n"
            f"To fix:\n"
            f"  1. df -h {_db_dir}\n"
            f"  2. Free space, or point CORTEXSIM_BASE_DIR at a larger volume.\n"
            f"  3. Run history is the only thing stored here; an old "
            f"cortexsim.db can be archived and removed safely.\n"
        ))

    return DatabaseBootError("DB_INIT_FAILED", (
        f"DATABASE INITIALISATION FAILED at {path}.\n"
        f"\n"
        f"{type(exc).__name__}: {exc}\n"
        f"\n"
        f"This condition has no dedicated remediation yet. Capture the full "
        f"server log and report it; as a workaround, move {path} aside (you "
        f"lose past run records only) and restart.\n"
    ))


async def init_db() -> None:
    """Create all tables. Called from the FastAPI lifespan handler.

    Storage faults are re-raised as :class:`DatabaseBootError` so the caller can
    print a remediation instead of a traceback.
    """
    try:
        await _init_db_inner()
    except DatabaseBootError:
        raise
    except Exception as exc:  # noqa: BLE001 — translated, then re-raised
        raise classify_db_error(exc) from exc


async def _init_db_inner() -> None:
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
        await conn.run_sync(_migrate_runtime_dependency_columns)


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
        ("stitch_context", "JSON"),      # Phase 2 — composer stitch context (authored intent)
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
        for col_name, col_type in [
            ("tc_verdict", "VARCHAR"),
            ("tc_verdict_detail", "JSON"),
            ("stitch_binding", "JSON"),  # Phase 2 — per-run resolved stitch binding
        ]:
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


def _migrate_runtime_dependency_columns(connection) -> None:
    """Add the runtime-dependency preflight/install columns to ``agents`` and
    ``runs`` if absent (docs/design/agent-runtime-dependencies.md). Same
    rationale as the other migration helpers here: ``create_all`` never adds a
    COLUMN to an existing table.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(connection)
    tables = set(inspector.get_table_names())

    if "agents" in tables:
        existing = {col["name"] for col in inspector.get_columns("agents")}
        if "interpreters" not in existing:
            connection.execute(text("ALTER TABLE agents ADD COLUMN interpreters JSON"))

    if "runs" in tables:
        existing = {col["name"] for col in inspector.get_columns("runs")}
        for col_name, col_type in [
            ("runtime_install_authorized", "BOOLEAN"),
            ("runtime_dependency_gaps", "JSON"),
        ]:
            if col_name in existing:
                continue
            connection.execute(text(f"ALTER TABLE runs ADD COLUMN {col_name} {col_type}"))


async def get_db():
    """FastAPI dependency — yields an async session."""
    async with AsyncSessionLocal() as session:
        yield session

"""Collector-target store: the DB and vault side of ``eal_simulator.targets``.

Every consumer that is about to resolve a destination calls ``load_specs`` (no
keys) and, only on the path that sends, ``load_tokens``. Keys are read from the
vault at that moment and never written anywhere else.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from eal_simulator.targets import TargetSpec
from models import CollectorTargetConfig
from security import CredentialStore

#: Summary fields of a preflight result kept on the row as ``last_check``.
_CHECK_FIELDS = ("verdict", "code", "checked_at", "write_canary", "status_code",
                 "records_written", "remediation")


def secret_name_for(name: str) -> str:
    return f"collector-target:{name}"


def to_spec(row: CollectorTargetConfig) -> TargetSpec:
    return TargetSpec(
        name=row.name, kind=row.kind, url=row.url, dataset=row.dataset,
        auth_header=row.auth_header or "Authorization", auth_scheme=row.auth_scheme or "",
        plugins=tuple(row.plugins or ()), is_default=bool(row.is_default),
        has_token=row.secret_name is not None, last_check=row.last_check,
    )


async def list_rows(db: AsyncSession) -> list[CollectorTargetConfig]:
    result = await db.execute(select(CollectorTargetConfig).order_by(CollectorTargetConfig.name))
    return list(result.scalars().all())


async def get_row(db: AsyncSession, name: str) -> Optional[CollectorTargetConfig]:
    result = await db.execute(
        select(CollectorTargetConfig).where(CollectorTargetConfig.name == name))
    return result.scalar_one_or_none()


async def load_specs(db: AsyncSession) -> list[TargetSpec]:
    return [to_spec(r) for r in await list_rows(db)]


async def load_tokens(db: AsyncSession, names: Iterable[str]) -> dict[str, str]:
    """Decrypt the keys for ``names``. Call only on a path that sends."""
    wanted = set(names)
    store = CredentialStore(db)
    out: dict[str, str] = {}
    for row in await list_rows(db):
        if row.name in wanted and row.secret_name:
            out[row.name] = await store.get_plaintext(row.secret_name)
    return out


async def record_check(db: AsyncSession, name: str, result: dict[str, Any]) -> None:
    row = await get_row(db, name)
    if row is None:
        return
    row.last_check = {k: result.get(k) for k in _CHECK_FIELDS}
    await db.commit()


async def upsert(
    db: AsyncSession, name: str, *, kind: str, url: str, dataset: Optional[str],
    auth_header: str, auth_scheme: str, plugins: list[str], is_default: bool,
    token: Optional[str], clear_token: bool,
) -> tuple[CollectorTargetConfig, Optional[str]]:
    """Create or replace a target. Returns ``(row, previous_default)``.

    The caller has validated the input. A change to anything that decides
    where or how records go discards the stored check: it described a
    different door.
    """
    store = CredentialStore(db)
    row = await get_row(db, name)
    now = datetime.utcnow()
    previous_default: Optional[str] = None
    if is_default:
        for other in await list_rows(db):
            if other.name != name and other.is_default:
                other.is_default = False
                previous_default = other.name

    if row is None:
        row = CollectorTargetConfig(name=name, created_at=now)
        changed = True
    else:
        changed = (row.url, row.kind, row.auth_header, row.auth_scheme) != (
            url, kind, auth_header, auth_scheme)
    # Every NOT NULL field is set before the row joins the session: the vault
    # write below flushes, and a half-built row would fail that flush.
    row.kind, row.url, row.dataset = kind, url, dataset
    row.auth_header, row.auth_scheme = auth_header, auth_scheme
    row.plugins, row.is_default, row.updated_at = list(plugins), bool(is_default), now
    db.add(row)

    if token is not None:
        await store.put(secret_name_for(name), token, type_hint="collector_token",
                        description=f"Collector key for target {name}")
        row.secret_name = secret_name_for(name)
        changed = True
    elif clear_token and row.secret_name:
        await store.delete(row.secret_name)
        row.secret_name = None
        changed = True

    if changed:
        row.last_check = None
    await db.commit()
    await db.refresh(row)
    return row, previous_default


async def delete(db: AsyncSession, name: str) -> bool:
    row = await get_row(db, name)
    if row is None:
        return False
    if row.secret_name:
        await CredentialStore(db).delete(row.secret_name)
    await db.delete(row)
    await db.commit()
    return True

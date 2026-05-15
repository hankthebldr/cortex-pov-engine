"""High-level credentials API.

This module is the *only* place in the codebase that handles plaintext
credential values. Every consumer (XSIAM client, AWS provisioning, future
integrations) reads via ``CredentialStore.get_plaintext()`` and never touches
the ORM ``Secret`` row directly.

Invariants enforced here:
    * Plaintext is never logged. We log secret *names*, never values.
    * Plaintext is never serialised to dicts/JSON unless explicitly via
      ``get_plaintext()``.
    * Every read bumps ``last_accessed_at`` so an operator can spot stale
      or unused secrets.

Pluggable policy:
    ``redaction_policy(plaintext, type_hint)`` decides what (if any) preview
    tail is stored alongside the ciphertext. See its docstring — this is a
    real security tradeoff and the default is conservative.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models import IntegrationCredential, Secret

from .crypto import CryptoError, derive_fernet_key


logger = logging.getLogger("cortexsim.security")


# ---------------------------------------------------------------------------
# Redaction policy — OPERATOR-OWNED DECISION
# ---------------------------------------------------------------------------
#
# TODO(Henry): Pick the right preview-tail policy for your DC workflow.
#
# The "preview tail" is a snippet of the cleartext value stored alongside the
# encrypted blob. It is intentionally cleartext — the point is to let an
# operator glance at the credentials list and recognise which key is which
# WITHOUT decrypting. Trade-off:
#
#   * NO preview ever — strictest. Operators rely entirely on the `name` and
#     `description` columns. Best for shared / multi-operator environments.
#   * Last 4 chars for values >= 20 chars (CURRENT DEFAULT) — matches how
#     AWS shows keys ("...A1B2"), how 1Password shows passwords, what most
#     SaaS APIs surface in their UI. Leaks 4 chars of entropy, but a 4-char
#     suffix of a 40-char API key is not exploitable on its own.
#   * Last 6 chars + first 2 chars — most operator-friendly, most leakage.
#     Useful when keys share a long common prefix (looking at you, AWS ARNs).
#
# Your CLAUDE.md OPSEC line is "never paste real credentials into chat". The
# preview tail is on disk, not in chat — different threat model. But if the
# DC laptop is the threat model (lost / left unlocked), the preview tail is
# disk-readable, so be honest about that.
#
# This function is called on every PUT. It receives the plaintext and the
# operator-supplied type_hint (e.g. "xsiam_api_key", "aws_secret_access_key").
# Return the string to store as Secret.preview_tail, or None to skip.
def redaction_policy(plaintext: str, type_hint: str) -> Optional[str]:
    """Return preview-tail to store with the encrypted secret, or None.

    DEFAULT: last 4 chars for values >= 20 chars, prefixed with an ellipsis.
    Override this function to enforce your operator's policy.
    """
    if len(plaintext) < 20:
        return None
    return f"...{plaintext[-4:]}"


# ---------------------------------------------------------------------------
# CredentialStore — async high-level API
# ---------------------------------------------------------------------------


class CredentialStore:
    """Async helper around the Secret + IntegrationCredential tables.

    Usage:
        store = CredentialStore(session)
        await store.put("xsiam-prod-key", "abc123...", type_hint="xsiam_api_key")
        value = await store.get_plaintext("xsiam-prod-key")
    """

    def __init__(self, session: AsyncSession, *, master_key: Optional[str] = None):
        self._session = session
        self._fernet = Fernet(derive_fernet_key(master_key or settings.CORTEXSIM_SECRET))

    # ── Secret CRUD ────────────────────────────────────────────────────────

    async def put(
        self,
        name: str,
        plaintext: str,
        *,
        type_hint: str = "generic",
        description: Optional[str] = None,
        rotation_days: Optional[int] = None,
    ) -> Secret:
        """Insert or update a secret. Returns the (refreshed) Secret row.

        ``rotation_days`` sets ``rotation_reminder_at`` to now + N days. The UI
        surfaces this as a soft reminder; nothing is auto-rotated.
        """
        if not name or not plaintext:
            raise ValueError("name and plaintext are required")

        token = self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")
        tail = redaction_policy(plaintext, type_hint)

        existing = await self._get_secret_row(name)
        now = datetime.utcnow()
        reminder = now + timedelta(days=rotation_days) if rotation_days else None

        if existing is None:
            secret = Secret(
                name=name,
                type_hint=type_hint,
                ciphertext=token,
                preview_tail=tail,
                description=description,
                created_at=now,
                updated_at=now,
                rotation_reminder_at=reminder,
            )
            self._session.add(secret)
            logger.info("secret created name=%s type_hint=%s", name, type_hint)
        else:
            existing.type_hint = type_hint
            existing.ciphertext = token
            existing.preview_tail = tail
            if description is not None:
                existing.description = description
            existing.updated_at = now
            if rotation_days is not None:
                existing.rotation_reminder_at = reminder
            secret = existing
            logger.info("secret updated name=%s type_hint=%s", name, type_hint)

        await self._session.flush()
        return secret

    async def get_plaintext(self, name: str) -> str:
        """Decrypt and return the plaintext value. Updates last_accessed_at.

        Raises KeyError if the secret does not exist, CryptoError on bad token.
        """
        row = await self._get_secret_row(name)
        if row is None:
            raise KeyError(f"secret '{name}' not found")

        try:
            plaintext = self._fernet.decrypt(row.ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise CryptoError(
                f"failed to decrypt secret '{name}' — wrong master key or corrupted ciphertext"
            ) from exc

        row.last_accessed_at = datetime.utcnow()
        await self._session.flush()
        logger.debug("secret read name=%s", name)
        return plaintext

    async def delete(self, name: str) -> bool:
        """Delete a secret by name. Returns True if a row was removed."""
        row = await self._get_secret_row(name)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        logger.info("secret deleted name=%s", name)
        return True

    async def list(self) -> list[Secret]:
        """Return every Secret row (metadata only — caller must not access ciphertext)."""
        stmt = select(Secret).order_by(Secret.name)
        res = await self._session.execute(stmt)
        return list(res.scalars().all())

    async def _get_secret_row(self, name: str) -> Optional[Secret]:
        stmt = select(Secret).where(Secret.name == name)
        res = await self._session.execute(stmt)
        return res.scalar_one_or_none()

    # ── IntegrationCredential CRUD ─────────────────────────────────────────

    async def put_integration(
        self,
        *,
        name: str,
        kind: str,
        plaintext_secret: str,
        config: dict[str, Any],
        secret_type_hint: Optional[str] = None,
        description: Optional[str] = None,
    ) -> IntegrationCredential:
        """Create or replace an integration credential.

        Stores the secret value under name ``__integration__/{name}`` so the
        underlying Secret isn't exposed by the plain ``list()`` call (operator
        manages integrations, not the secret rows behind them).
        """
        if not name or not kind or not plaintext_secret:
            raise ValueError("name, kind, and plaintext_secret are required")

        backing_name = f"__integration__/{name}"
        secret = await self.put(
            backing_name,
            plaintext_secret,
            type_hint=secret_type_hint or f"{kind}_credential",
            description=f"backing secret for integration '{name}' ({kind})",
        )

        existing = await self._get_integration_row(name)
        now = datetime.utcnow()
        if existing is None:
            row = IntegrationCredential(
                name=name,
                kind=kind,
                config=config,
                secret_id=secret.id,
                created_at=now,
                updated_at=now,
            )
            self._session.add(row)
            logger.info("integration created name=%s kind=%s", name, kind)
        else:
            existing.kind = kind
            existing.config = config
            existing.secret_id = secret.id
            existing.updated_at = now
            existing.last_verified_at = None
            existing.last_verified_ok = None
            existing.last_verified_error = None
            row = existing
            logger.info("integration updated name=%s kind=%s", name, kind)

        await self._session.flush()
        return row

    async def get_integration(self, name: str) -> Optional[IntegrationCredential]:
        return await self._get_integration_row(name)

    async def get_integration_secret(self, name: str) -> str:
        """Convenience: integration name → decrypted secret plaintext."""
        return await self.get_plaintext(f"__integration__/{name}")

    async def list_integrations(self, kind: Optional[str] = None) -> list[IntegrationCredential]:
        stmt = select(IntegrationCredential).order_by(IntegrationCredential.name)
        if kind:
            stmt = stmt.where(IntegrationCredential.kind == kind)
        res = await self._session.execute(stmt)
        return list(res.scalars().all())

    async def delete_integration(self, name: str) -> bool:
        row = await self._get_integration_row(name)
        if row is None:
            return False
        backing_name = f"__integration__/{name}"
        await self._session.delete(row)
        await self.delete(backing_name)
        logger.info("integration deleted name=%s", name)
        return True

    async def mark_integration_verified(
        self,
        name: str,
        *,
        ok: bool,
        error: Optional[str] = None,
    ) -> Optional[IntegrationCredential]:
        row = await self._get_integration_row(name)
        if row is None:
            return None
        row.last_verified_at = datetime.utcnow()
        row.last_verified_ok = ok
        row.last_verified_error = error if not ok else None
        await self._session.flush()
        return row

    async def _get_integration_row(self, name: str) -> Optional[IntegrationCredential]:
        stmt = select(IntegrationCredential).where(IntegrationCredential.name == name)
        res = await self._session.execute(stmt)
        return res.scalar_one_or_none()

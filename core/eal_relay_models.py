"""The EAL relay's one table: which run marked which step for which beacon.

Kept out of ``models.py`` so the relay stays one self-contained slice;
``models.py`` imports this module so ``create_all`` creates the table wherever
the models are loaded. See ``engine/eal_relay.py`` for the reader and writer.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class EalRelayStep(Base):
    """One pull-task step SimCore marked ``eal_relay`` at launch.

    The row is the relay's whole authority: ``POST /api/eal/relay`` serves a
    request only when a row exists for its ``(run_id, step_id)`` and names the
    asking agent. ``authorized_by`` and ``c2_authorized`` are the launch's
    consent, recorded here because the campaign text arrives later, from the
    target, and its own consent fields are never read.
    """

    __tablename__ = "eal_relay_steps"
    __table_args__ = (UniqueConstraint("run_id", "step_id", name="uq_eal_relay_run_step"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    step_id: Mapped[str] = mapped_column(String, nullable=False)
    agent_id: Mapped[str] = mapped_column(String, nullable=False)
    # The operator the launch named under consent.simulation_authorized; NULL
    # means the launch gave no live consent and every relay is a dry run.
    authorized_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    c2_authorized: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Ledger. ``relays`` counts every request served for the step; ``delivered``
    # is records a collector accepted; ``ops_issued`` is network operations
    # handed to the beacon.
    relays: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ops_issued: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    last_relay_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

"""The catch-all 500 must not echo the exception text to the caller.

``main.global_exception_handler`` returned ``"detail": str(exc)``. For a
SQLAlchemy error that string carries the full statement AND its bound
parameters. Reproduced with a double-submitted ``PUT
/api/credentials/integrations`` (a double-click on Save): the losing requests hit
``UNIQUE constraint failed: secrets.name`` and the unauthenticated response body
contained the tenant API key's Fernet ciphertext and its ``preview_tail`` (the
last four characters of the key) — the exact material ``GET
/api/credentials/secrets`` was hardened to stop disclosing.

The body keeps the ``{error, code, detail}`` contract and gains a ``ref`` that
appears in the server log next to the full traceback, so the operator can still
find the cause.
"""
from __future__ import annotations

import asyncio
import logging

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_KEY = "sk-live-TENANT-KEY-0123456789wxyz"


def _app_with_handler():
    from main import global_exception_handler

    app = FastAPI()
    app.add_exception_handler(Exception, global_exception_handler)
    return app


def test_unhandled_exception_text_is_not_returned(caplog):
    app = _app_with_handler()

    @app.get("/boom")
    async def _boom():
        raise RuntimeError("[SQL: INSERT INTO secrets ...] [parameters: ('gAAAAAB-ciphertext', '...wxyz')]")

    async def _go():
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await c.get("/boom")

    with caplog.at_level(logging.ERROR, logger="cortexsim.main"):
        r = asyncio.run(_go())
    assert r.status_code == 500
    body = r.json()
    assert body["code"] == "INTERNAL_ERROR"
    assert set(body) >= {"error", "code", "detail", "ref"}
    for leaked in ("gAAAAAB", "wxyz", "[SQL", "[parameters", "INSERT INTO"):
        assert leaked not in r.text, f"{leaked!r} leaked: {r.text}"
    # The operator can still find it: the ref is in the server log with the cause.
    assert body["ref"] in caplog.text
    assert "gAAAAAB-ciphertext" in caplog.text


def test_double_submitted_integration_put_leaks_no_key_material(tmp_path):
    from api.credentials import router
    from database import Base, get_db
    import models  # noqa: F401

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cred.db'}")
    SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _go():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        app = _app_with_handler()
        app.include_router(router, prefix="/api")

        async def _db():
            async with SessionLocal() as s:
                yield s

        app.dependency_overrides[get_db] = _db
        body = {"name": "acme", "kind": "generic", "plaintext_secret": _KEY, "config": {}}
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            rs = await asyncio.gather(*(
                c.put("/api/credentials/integrations", json=body) for _ in range(4)))
        await engine.dispose()
        return rs

    for r in asyncio.run(_go()):
        assert "gAAAA" not in r.text, r.text      # Fernet token prefix
        assert "...wxyz" not in r.text, r.text    # preview_tail of the key
        assert "[parameters" not in r.text, r.text

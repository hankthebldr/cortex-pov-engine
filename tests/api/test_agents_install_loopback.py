"""A baked loopback callback address must be DIAGNOSED, never silently generic.

The failure this guards is silent and expensive: a DC has the console open on
the control plane at http://localhost:8888 (what scripts/dev-up.sh gives you),
copies the one-liner, pastes it on a jumpbox, and `localhost` there resolves to
the JUMPBOX. The beacon installs cleanly, verifies its sha256, starts under
systemd, and calls back to itself. The generic remediation on the resulting
unreachable-server failure is "check routing / proxy / firewall", which sends
the operator to the customer's network team for a problem in the URL.

The endpoint deliberately does NOT refuse to bake loopback: installing on the
SimCore host itself is a real flow (local dev, and tests/installer's e2e suite,
which serves uvicorn on 127.0.0.1 and installs against it). From the server
side those two cases are indistinguishable, so the diagnosis is pushed to the
two places that CAN tell them apart — the console before the copy, and the
generated script's preflight on the target, which only fires when SERVER is
genuinely unreachable.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.agents import router as agents_router, _is_loopback, _loopback_hint


@pytest.fixture()
def client_at(memory_db):
    """Build a TestClient whose base_url IS the address under test — the
    baked SERVER is derived from the request, so the origin is the input.
    """
    get_db_override, _ = memory_db

    def _build(base_url: str) -> TestClient:
        from database import get_db

        app = FastAPI()
        app.include_router(agents_router, prefix="/api")
        app.dependency_overrides[get_db] = get_db_override
        return TestClient(app, base_url=base_url)

    return _build


# --------------------------------------------------------------------- unit
@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8888",
        "http://127.0.0.1:8888",
        "https://localhost",
        "http://[::1]:8888",
        "http://0.0.0.0:8888",
        "http://LOCALHOST:8888",  # case must not be a bypass
    ],
)
def test_loopback_addresses_are_detected(url):
    assert _is_loopback(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.10.5:8888",
        "http://simcore.corp.local:8888",
        "https://cortexsim.example.com",
        # Substring traps: these are NOT loopback and must not be flagged.
        "http://localhost.attacker.example.com",
        "http://not-127.0.0.1.example.com",
    ],
)
def test_routable_addresses_are_not_flagged(url):
    assert _is_loopback(url) is False


def test_hint_is_empty_for_a_routable_server():
    """No hint means the generic remediation stands — which is correct there."""
    assert _loopback_hint("http://10.0.10.5:8888") == ""


# ---------------------------------------------------------------- endpoint
def test_loopback_is_served_not_refused(client_at):
    """Same-host installs stay possible — this is the e2e suite's own flow."""
    r = client_at("http://127.0.0.1:8888").get("/api/agents/install?os=linux")
    assert r.status_code == 200
    assert "http://127.0.0.1:8888" in r.text


def test_loopback_script_carries_the_remediation(client_at):
    """The hint rides in the script, on the unreachable-server path only."""
    r = client_at("http://localhost:8888").get("/api/agents/install?os=linux")
    assert r.status_code == 200
    # Key on the hint's own wording, not the word "loopback" — the template
    # carries an explanatory comment that also uses it.
    assert "re-fetch it from the control plane's routable address" in r.text


def test_routable_script_carries_no_hint(client_at):
    """A correct address must not be nagged about a problem it does not have."""
    r = client_at("http://10.0.10.5:8888").get("/api/agents/install?os=linux")
    assert r.status_code == 200
    assert "re-fetch it from the control plane's routable address" not in r.text
    assert 'CS_LOOPBACK_HINT=""' in r.text


def test_explicit_override_wins_over_the_request_origin(client_at):
    """The DC knows the jumpbox's reachable address — honour it, and with it
    the hint disappears, because the baked SERVER is no longer loopback."""
    r = client_at("http://localhost:8888").get(
        "/api/agents/install?os=linux&server=http://10.0.10.5:8888"
    )
    assert r.status_code == 200
    assert "http://10.0.10.5:8888" in r.text
    assert "re-fetch it from the control plane's routable address" not in r.text

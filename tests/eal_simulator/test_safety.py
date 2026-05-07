"""Safety policy enforcement tests."""
from __future__ import annotations

import pytest

from eal_simulator.safety import SafetyError, SafetyPolicy


def test_dry_run_skips_all_checks():
    policy = SafetyPolicy(
        simulation_authorized=False,
        authorized_by="",
        target_allowlist=[],
        dry_run=True,
    )
    policy.assert_campaign_authorized()
    policy.authorise("anything.example.com")  # accepted in dry-run


def test_live_requires_simulation_authorized():
    policy = SafetyPolicy(
        simulation_authorized=False,
        authorized_by="op",
        target_allowlist=["a.example"],
        dry_run=False,
    )
    with pytest.raises(SafetyError, match="simulation_authorized"):
        policy.assert_campaign_authorized()


def test_live_requires_authorized_by():
    policy = SafetyPolicy(
        simulation_authorized=True,
        authorized_by="",
        target_allowlist=["a.example"],
        dry_run=False,
    )
    with pytest.raises(SafetyError, match="authorized_by"):
        policy.assert_campaign_authorized()


def test_live_requires_non_empty_allowlist():
    policy = SafetyPolicy(
        simulation_authorized=True,
        authorized_by="op",
        target_allowlist=[],
        dry_run=False,
    )
    with pytest.raises(SafetyError, match="target_allowlist"):
        policy.assert_campaign_authorized()


def test_hostname_suffix_match_accepts_subdomain():
    policy = SafetyPolicy(
        simulation_authorized=True,
        authorized_by="op",
        target_allowlist=["testmynids.org"],
        dry_run=False,
    )
    policy.authorise("foo.testmynids.org")
    policy.authorise("testmynids.org")


def test_hostname_match_rejects_unrelated():
    policy = SafetyPolicy(
        simulation_authorized=True,
        authorized_by="op",
        target_allowlist=["testmynids.org"],
        dry_run=False,
    )
    with pytest.raises(SafetyError):
        policy.authorise("evil.example.com")


def test_cidr_match_accepts_member():
    policy = SafetyPolicy(
        simulation_authorized=True,
        authorized_by="op",
        target_allowlist=["10.50.0.0/24"],
        dry_run=False,
    )
    policy.authorise("10.50.0.42")


def test_cidr_match_rejects_outsider():
    policy = SafetyPolicy(
        simulation_authorized=True,
        authorized_by="op",
        target_allowlist=["10.50.0.0/24"],
        dry_run=False,
    )
    with pytest.raises(SafetyError):
        policy.authorise("10.60.0.42")


def test_host_with_port_strips_port():
    policy = SafetyPolicy(
        simulation_authorized=True,
        authorized_by="op",
        target_allowlist=["testmynids.org"],
        dry_run=False,
    )
    policy.authorise("foo.testmynids.org:8080")


def test_invalid_hostname_rejected():
    policy = SafetyPolicy(
        simulation_authorized=True,
        authorized_by="op",
        target_allowlist=["a.example"],
        dry_run=False,
    )
    with pytest.raises(SafetyError):
        policy.authorise("space disallowed")


def test_ipv6_literal_uses_128_prefix_not_32():
    """Regression: a single IPv6 literal must not authorise a whole /32 net.

    Codex review on PR #9 flagged that ``ip_network(f"{token}/32")`` over-
    broadens IPv6 entries. We assert that an IPv6 literal authorises only
    that exact address.
    """
    policy = SafetyPolicy(
        simulation_authorized=True,
        authorized_by="op",
        target_allowlist=["2001:db8::1"],
        dry_run=False,
    )
    # Exact host accepted.
    policy.authorise("2001:db8::1")
    # Different host inside the (would-be /32) network must be rejected.
    with pytest.raises(SafetyError):
        policy.authorise("2001:db8::2")
    with pytest.raises(SafetyError):
        policy.authorise("2001:db8:dead:beef::1")


def test_ipv4_literal_still_uses_32_prefix():
    """IPv4 sibling of the IPv6 fix — single host literal stays /32."""
    policy = SafetyPolicy(
        simulation_authorized=True,
        authorized_by="op",
        target_allowlist=["10.0.0.5"],
        dry_run=False,
    )
    policy.authorise("10.0.0.5")
    with pytest.raises(SafetyError):
        policy.authorise("10.0.0.6")

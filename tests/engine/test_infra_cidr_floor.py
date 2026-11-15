"""SEC-10 — ``dc_ssh_cidr`` also opens the unauthenticated SimCore port.

In ``infra/modules/aws/base/main.tf`` the jumpbox security group uses
``var.dc_ssh_cidr`` for BOTH ingress rules: 22/tcp (SSH) and 8888/tcp (SimCore
UI + API). The field is named for SSH, so a DC in a hurry types ``0.0.0.0/0``
"just to get SSH working" — and publishes an API with no authentication to the
internet. ``ipaddress.ip_network`` accepted it; nothing else looked.

Floor: an IPv4 prefix shorter than /16 (more than 65,536 addresses) is refused,
and the message says why — the CIDR gates the SimCore port too. IPv6 gets the
equivalent site-sized floor, /48: an IPv6 /16 would be 2**112 addresses and no
guard at all. (The AWS template feeds ``cidr_blocks``, which is IPv4-only, so
an IPv6 value fails at apply regardless; the floor keeps the request honest.)

RED on base cc26ddf: ``0.0.0.0/0`` and ``10.0.0.0/8`` validated.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError


def _params(cidr: str):
    from engine.infra_models import InfraGenerateParams

    return InfraGenerateParams(project_name="acme-pov", dc_ssh_cidr=cidr)


@pytest.mark.parametrize("cidr", ["0.0.0.0/0", "10.0.0.0/8", "172.16.0.0/12",
                                  "192.0.0.0/15", "::/0", "2001:db8::/32"])
def test_overbroad_cidr_is_refused_and_says_why(cidr):
    with pytest.raises(ValidationError) as exc:
        _params(cidr)
    msg = str(exc.value)
    assert "SimCore" in msg and "8888" in msg, msg
    assert "unauthenticated" in msg.lower(), msg


@pytest.mark.parametrize("cidr", ["203.0.113.7/32", "203.0.113.0/24",
                                  "10.20.0.0/16", "203.0.113.7",
                                  "2001:db8:1::/48", "2001:db8:1:2::/64"])
def test_reasonable_cidr_is_accepted(cidr):
    assert _params(cidr).dc_ssh_cidr == cidr


def test_garbage_is_still_refused():
    with pytest.raises(ValidationError):
        _params("not-a-cidr")

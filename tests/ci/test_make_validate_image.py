"""``make validate`` cannot pass against a stale image (CI-17).

Four of its gates ``docker run $(IMAGE)``: ``check-refs`` and ``ground-truth``
run the tree's code inside the image, and ``check-agent-shelf`` /
``check-ui-shelf`` assert what the image itself SHIPS. ``validate`` did not
depend on ``build``, so they ran against whatever tag was already on the
machine. After a Dockerfile change without a rebuild, the beacon and console
parity gates passed against yesterday's image. ``make ci`` was safe only by
accident: ``test-backend`` happens to depend on ``build``.

``validate`` now builds first. The targeted gates stay assertions about a NAMED
image and do not rebuild on their own, because the backend CI job builds
``cortexsim:ci`` once and points ``check-agent-shelf`` at exactly that image.
"""
from __future__ import annotations

from tests.ci._wiring import make_n


def test_make_validate_builds_the_image_before_any_gate_reads_it():
    out = make_n("validate")
    build = out.find("docker build -f core/Dockerfile")
    assert build >= 0, "`make validate` never builds the image its gates inspect"
    first_run = out.find("docker run")
    assert first_run > build, "an image gate runs before the image is built"
    assert out.find("scripts/check-ui-shelf.sh") > build
    assert out.count("docker build -f core/Dockerfile") == 1, "the image is built once per `make validate`"


def test_make_ci_builds_the_image_once():
    out = make_n("ci")
    assert out.count("docker build -f core/Dockerfile") == 1
    assert out.find("docker build -f core/Dockerfile") < out.find("docker run")


def test_ci_still_checks_the_image_it_built_without_rebuilding():
    """The targeted gate is an assertion about the image it is GIVEN. If it
    rebuilt, the backend job would be checking a second build, not the image its
    pytest run just used."""
    out = make_n("check-agent-shelf", "IMAGE=cortexsim:ci")
    assert "docker build" not in out
    assert "cortexsim:ci" in out

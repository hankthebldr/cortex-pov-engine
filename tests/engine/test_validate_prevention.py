import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent.parent / "detection_scanner" / "scripts" / "validate.py"


@pytest.fixture(scope="module")
def validate_mod():
    spec = importlib.util.spec_from_file_location("validate_script", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["validate_script"] = mod
    spec.loader.exec_module(mod)
    return mod


GOOD = {"name": "BTP reverse shell", "module": "btp", "platforms": ["linux"]}


@pytest.mark.parametrize("bad", [
    dict(GOOD, module="nope"),
    dict(GOOD, name="TODO fill in"),
    dict(GOOD, name="  "),
    dict(GOOD, platforms=[]),
])
def test_prevention_module_rejects_are_errors(validate_mod, bad):
    r = validate_mod.Report(quiet=True)
    validate_mod.check_prevention_modules(r, "x.json", {"prevention_modules": [bad]})
    assert len(r.errors) == 1 and not r.warnings


def test_valid_prevention_module_passes(validate_mod):
    r = validate_mod.Report(quiet=True)
    validate_mod.check_prevention_modules(r, "x.json", {"prevention_modules": [GOOD]})
    assert not r.errors and not r.warnings

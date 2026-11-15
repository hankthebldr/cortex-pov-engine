"""A scenario's scratch paths cannot be taken over by another local user.

Two shapes of scratch path were reachable by any local user:

1. A fixed DIRECTORY under /tmp, /var/tmp or /dev/shm. Made with ``mkdir -p``,
   it could be created first by another user, who then owned it: ``mkdir -p``
   succeeds on an existing directory and a later ``chmod`` does not change the
   owner. Steps then wrote into it and ran files from it. An adversarial review
   reproduced this as a root code execution (2026-11-13).
2. A fixed-name FILE written straight into /tmp or /var/tmp by a root step and
   then run or read back (an EAL campaign YAML handed to the CLI, a crontab
   staged and cat'd, a hash list handed to impacket). A file or symlink planted
   at that name lets another user redirect the write or change what is run.

The rule for a directory (SANDBOXES):
  - the step that creates it removes whatever is at the path and makes it with
    ``mkdir -m MODE`` and no ``-p``, so an existing entry stops the step;
  - every other step that uses it first checks, in ONE lookup, that it is a
    directory (not a symlink) with that mode and the expected owner:
    ``[ "$(stat -c %F:%u:%a "$D" 2>/dev/null)" = "directory:$(id -u):700" ]``.
    Where a service account runs inside a root-made directory the mode is 755
    and the check stores the lookup and also accepts root
    (``s=$(stat ...); [ "$s" = "directory:0:755" ] || [ "$s" = "directory:$(id -u):755" ]``).
    It never accepts a directory another, less privileged account owns: that
    account could plant links inside it and redirect the step's writes, so a
    step running as a different account keeps its own files in ``$C`` instead.
    A step that also runs on macOS adds the BSD lookup inside the same
    substitution (``|| stat -f %HT:%u:%Lp "$D" 2>/dev/null``) and accepts
    ``Directory:``. Each alternative is a complete check on one lookup.

The rule for a file used only by the step that writes it (PRIVATE_FILES): the
step opens with ``C=$(mktemp -d /tmp/cortexsim.XXXXXX) || exit 1; trap 'rm -rf
"$C"' EXIT`` and keeps the file at ``$C/<name>``; the old fixed name is gone from
the scenario. A file handed to a later step lives in that scenario's sandbox.

One lookup matters. An earlier form used three tests (``-d``, ``! -L``, ``-O``).
Where root follows another user's symlink (``fs.protected_symlinks`` off), a
user exchanging a directory and a symlink between those tests got a root step
past it in 73 of 300 passes under dash and 50 of 300 under bash; the one-lookup
form let none through. test_a_guard_cannot_be_raced_by_an_exchange keeps that
measurement for every guard form in the corpus.

The text tests read the scenarios and run anywhere. The behaviour tests run each
step's own guard or private-directory line as the step's identity against
planted paths; they need root and fixed system paths, so they run only inside a
container (the CI image).
"""
from __future__ import annotations

import ctypes
import functools
import os
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# scenario id -> the fixed scratch directories its steps create
SANDBOXES = {
    "SIM-AIACC-006": ["/tmp/cortexsim-aiacc006"],
    "SIM-AIRS-001": ["/tmp/cortexsim-airs-001"],
    "SIM-AIRS-003": ["/tmp/cortexsim-airs-003"],
    "SIM-AIRS-004": ["/tmp/cortexsim-airs-004"],
    "SIM-AISPM-001": ["/tmp/cortexsim-aispm-001"],
    "SIM-AISPM-002": ["/tmp/cortexsim-aispm-002"],
    "SIM-AISPM-004": ["/tmp/cortexsim-aispm-004"],
    "SIM-AISPM-005": ["/tmp/cortexsim-aispm-005"],
    "SIM-AISPM-006": ["/tmp/cortexsim-aispm-006"],
    "SIM-APB-001": ["/tmp/cortexsim-apb001"],
    "SIM-CDR-002": ["/tmp/.xmr"],
    "SIM-CDR-004": ["/tmp/cortexsim-cdr-004"],
    "SIM-CDR-005": ["/tmp/wf_sim"],
    "SIM-CDR-006": ["/tmp/.cortexsim-node"],
    "SIM-CDR-010": ["/tmp/cortexsim-cdr-010"],
    "SIM-CDR-012": ["/var/tmp/.hidden_wildfire_samples"],
    "SIM-CDR-018": ["/tmp/cortexsim-cdr-018"],
    "SIM-CDR-027": ["/tmp/cortex_ddr_dump"],
    "SIM-CDR-028": ["/tmp/cortex_ddr_dynamo"],
    "SIM-CLOUD-010": ["/tmp/cortexsim-cloud010"],
    "SIM-DLP-001": ["/tmp/cortex_dlp_usb", "/tmp/cortex_mock_usb"],
    "SIM-DLP-002": ["/tmp/cortex_dlp_staging"],
    "SIM-DLP-003": ["/tmp/cortex_dlp_saas"],
    "SIM-DLP-004": ["/tmp/cortex_dlp_cross"],
    "SIM-DLP-005": ["/tmp/cortex_dlp_phi"],
    "SIM-EDR-003": ["/tmp/.cortexsim_ssh_test"],
    "SIM-EDR-007": ["/var/lib/cortexsim/edr-007"],
    "SIM-EDR-008": ["/tmp/cortexsim_victim", "/tmp/cortexsim-edr-008"],
    "SIM-EDR-009": ["/tmp/cortexsim_edr009_stage", "/tmp/cortexsim_edr009_dest"],
    "SIM-EDR-011": ["/tmp/cortexsim_preenc"],
    "SIM-EDR-012": ["/tmp/cortexsim-collect"],
    "SIM-EDR-015": ["/tmp/cortexsim_edr015"],
    "SIM-EDR-023": ["/var/tmp/.cortexsim-ice"],
    "SIM-EDR-024": ["/var/tmp/.cortexsim-sshd"],
    "SIM-EDR-025": ["/var/tmp/.cortexsim-akira"],
    "SIM-ITDR-011": ["/tmp/cortexsim-itdr-011"],
    "SIM-ITDR-012": ["/tmp/cortexsim-itdr-012"],
    "SIM-ITDR-013": ["/tmp/cortexsim-itdr-013"],
    "SIM-ITDR-014": ["/tmp/cortexsim-itdr-014"],
    "SIM-ITDR-017": ["/tmp/cortexsim-itdr-017"],
    "SIM-KOI-007": ["/tmp/cortexsim-mcp-sampling"],
    "SIM-KOI-009": ["/tmp/CORTEXSIM-KOI009-VSIX", "/tmp/CORTEXSIM-KOI009-DECOY"],
    "SIM-KOI-010": ["/var/tmp/.koi010-victim"],
    "SIM-KOI-011": ["/var/tmp/.koi011-victim"],
    "SIM-KOI-012": ["/var/tmp/.cortexsim-axios"],
    "SIM-MP-003": ["/tmp/cortexsim_stage"],
    "SIM-MP-010": ["/tmp/cortexsim-mp-010"],
    "SIM-MP-013": ["/tmp/cortexsim-mp013"],
    "SIM-MP-015": ["/tmp/cortexsim-mp015"],
    "SIM-MP-017": ["/tmp/cortexsim-mp-017"],
    "SIM-MP-018": ["/tmp/cortexsim_mp018_wipe_scratch"],
    "SIM-MP-019": ["/tmp/cortexsim-mp019"],
    "SIM-MP-020": ["/tmp/.cortexsim-mp020"],
    "SIM-MP-021": ["/tmp/cortexsim-mp021"],
    "SIM-MP-022": ["/tmp/cortexsim-mp022"],
    "SIM-MP-023": ["/tmp/cortexsim-mp023"],
    "SIM-MP-024": ["/tmp/cortexsim-mp-024", "/dev/shm/.gs-cortexsim-mp024"],
    "SIM-MP-025": ["/tmp/cortexsim-mp025"],
    "SIM-MP-026": ["/var/tmp/.cortexsim-mp026"],
    "SIM-MP-027": ["/tmp/cortexsim-mp-027"],
    "SIM-MP-028": ["/tmp/cortexsim-mp-028"],
    "SIM-MP-029": ["/tmp/cortexsim-mp-029"],
    "SIM-MP-031": ["/tmp/cortexsim-mp031"],
    "SIM-MP-032": ["/tmp/cortexsim-mp-032"],
    "SIM-MP-033": ["/var/tmp/.cortexsim-mp033"],
    "SIM-MP-034": ["/tmp/cortexsim-mp-034"],
    "SIM-MP-035": ["/tmp/cortexsim-mp-035"],
    "SIM-MP-036": ["/tmp/cortexsim-mp-036"],
    "SIM-MP-037": ["/tmp/cortexsim-mp-037", "/dev/shm/cortexsim-mp-037"],
    "SIM-TIM-003": ["/tmp/cortexsim-tim-003"],
    "SIM-TIM-008": ["/tmp/cortexsim-tim008"],
    "SIM-TIM-009": ["/tmp/cortexsim-tim009"],
}

# scenario id -> files a root step once wrote straight into /tmp under these names,
# to run or read them in the same step (or, in a step that now has $C anyway, only
# to keep its output); each now lives in $C (see PRIVATE_DIR)
PRIVATE_FILES = {
    "SIM-AIACC-001": "aiacc-001-burst.yml aiacc-001.yml",
    "SIM-AIACC-002": "aiacc-002-burst.yml aiacc-002.yml",
    "SIM-AIACC-003": "aiacc-003-cool.yml aiacc-003.yml",
    "SIM-AIACC-004": "aiacc-004.yml",
    "SIM-AIACC-005": "aiacc-005-anthropic.yml aiacc-005-gemini.yml aiacc-005-openai.yml",
    "SIM-AIRS-002": "airs-002-events.jsonl airs-002-poison.txt",
    "SIM-AIRS-004": "airs-004-tools.json",
    "SIM-AIRS-005": "airs-005-events.jsonl airs-005-final.json",
    "SIM-AISPM-002": "aispm-002-findings.json",
    "SIM-AISPM-003": "aispm-003-expected-cves.txt aispm-003-findings.json aispm-003-graph.json aispm-003-layer-arn.txt",
    "SIM-AISPM-004": "aispm-004-canary.txt aispm-004-findings.json aispm-004-pickle-check.txt",
    "SIM-AISPM-005": "aispm-005-canary-preview.txt aispm-005-dspm.json aispm-005-gov.json aispm-005-lineage.json",
    "SIM-AISPM-006": "aispm-006-summary.json aispm-006-trends.json",
    "SIM-BROWSER-001": "sim-browser-001-eal.yml",
    "SIM-BROWSER-002": "sim-browser-002-eal.yml",
    "SIM-BROWSER-003": "sim-browser-003-eal.yml",
    "SIM-BROWSER-004": "sim-browser-004-eal.yml",
    "SIM-BROWSER-005": "sim-browser-005-eal.yml",
    "SIM-CDR-004": "cortexsim-cronjob.yaml",
    "SIM-CDR-007": "cloudsplaining iam-authz.json",
    "SIM-CDR-019": "cdr-019-s3-exfil-confirm.yml cdr-019-s3-exfil.yml",
    "SIM-CDR-020": "cdr-020-secrets.yml cdr-020-ssm.yml",
    "SIM-CLOUD-001": "cloud-001-burst.yml cloud-001.yml",
    "SIM-CLOUD-002": "cloud-002-rep.yml cloud-002.yml",
    "SIM-CLOUD-003": "cloud-003-ms.yml cloud-003.yml",
    "SIM-CLOUD-004": "cloud-004-google.yml cloud-004-ms.yml cloud-004-okta.yml",
    "SIM-CLOUD-005": "cloud-005-google.yml cloud-005-ms.yml cloud-005-okta.yml",
    "SIM-CLOUD-008": "cloud-008-enum.yml cloud-008-exfil.yml",
    "SIM-CLOUD-009": "sim-cloud-009-onedrive.yml sim-cloud-009-sharepoint.yml",
    "SIM-CSPM-001": "cspm-discovered.json cspm-planted.json",
    "SIM-EDR-001": "mimi_output.txt mimipenguin.sh",
    "SIM-EDR-003": "cortexsim-backdoor.service cortexsim_cron_backup cortexsim_cron_test cortexsim_profile_test",
    "SIM-EMAIL-001": "email-001-m365.yml email-001.yml",
    "SIM-EMAIL-002": "email-002-m365.yml email-002.yml",
    "SIM-EMAIL-003": "email-003-m365.yml email-003.yml",
    "SIM-EMAIL-004": "email-004-m365.yml email-004.yml",
    "SIM-EMAIL-005": "email-005-click.yml email-005-replay.yml",
    "SIM-ITDR-001": "itdr-001-ms.yml itdr-001.yml",
    "SIM-ITDR-002": "itdr-002-heavy.yml itdr-002.yml",
    "SIM-ITDR-003": "itdr-003-ms.yml itdr-003.yml",
    "SIM-ITDR-004": "itdr-004-rep.yml itdr-004.yml",
    "SIM-ITDR-005": "itdr-005-spray.yml itdr-005.yml",
    "SIM-ITDR-006": "cortexsim_itdr006_asrep.txt cortexsim_itdr006_users.txt",
    "SIM-ITDR-008": "itdr-008-ms.yml itdr-008-okta.yml",
    "SIM-ITDR-019": "itdr-019-rule.yml itdr-019.yml",
    "SIM-KOI-001": "koi-001.yml",
    "SIM-KOI-002": "koi-002.yml",
    "SIM-KOI-003": "koi-003.yml",
    "SIM-KOI-004": "koi-004.yml",
    "SIM-KOI-005": "koi-005.yml",
    "SIM-KOI-006": "koi-006.yml",
    "SIM-MP-002": "cortexsim_mp002_cracked.txt",
    "SIM-MP-023": "mp-023-ngfw-eal.yml mp-023-ngfw-threat.yml",
    "SIM-MP-024": "mp-024-gsocket-threat.yml mp-024-mail-upload.yml mp-024-sysgorat-c2.yml",
    "SIM-MP-025": "cortexsim-mp025-github.yml cortexsim-mp025-tunnel.yml",
    "SIM-MP-026": "mp-026-beacon.yml mp-026-threat.yml",
    "SIM-MP-027": "mp-027-ngfw-eal.yml",
    "SIM-MP-029": "mp-029-c2.yml mp-029-delivery.yml",
    "SIM-MP-030": "mp-030-beacon.yml mp-030-threat.yml",
    "SIM-MP-031": "mp-031-exfil.yml mp-031-threat.yml",
    "SIM-MP-032": "mp-032-ngfw-eal.yml mp-032-ngfw-threat.yml",
    "SIM-MP-033": "mp-033-ssh.yml mp-033-threat.yml",
    "SIM-MP-034": ("mp-034-download.yml mp-034-onedrive.yml mp-034-risky.yml "
                   "mp-034-rule.yml mp-034-sharepoint.yml mp-034-travel.yml"),
    "SIM-MP-035": "mp-035-llm.yml mp-035-ngfw-c2.yml mp-035-ngfw-exfil.yml mp-035-ngfw-proxy.yml",
    "SIM-MP-036": ("cortexsim-mp-036-manifest.txt mp-036-gp-ips.yml mp-036-gp-root.yml "
                   "mp-036-lateral.yml mp-036-vcenter.yml mp-036-veeam.yml"),
    "SIM-MP-037": "mp-037-php2shell-inbound.yml",
    "SIM-NDR-001": "c2-beacon-dry.yml c2-beacon-live.yml",
    "SIM-NDR-002": "dns-tunnel-live.yml dns-tunnel-txt.yml",
    "SIM-NDR-003": "stratum-live.yml",
    "SIM-NDR-004": "smb-sweep-dry.yml smb-sweep-live.yml",
    "SIM-NDR-005": "bulk-exfil.yml",
    "SIM-NDR-006": "ftp-egress-live.yml",
    "SIM-NDR-007": "ssh-egress-live.yml",
    "SIM-NDR-012": "ndr-012-beacon.yml ndr-012-upload.yml",
    "SIM-TIM-001": "tim-ioc-beacon-dry.yml tim-ioc-beacon-live.yml",
    "SIM-TIM-002": "tim-resdev-stage-dry.yml tim-resdev-stage-live.yml",
}
PRIVATE_DIR = """C=$(mktemp -d /tmp/cortexsim.XXXXXX) || exit 1; trap 'rm -rf "$C"' EXIT"""
RUNS_AS_BEACON = {"root", "direct", "container-runtime"}
# Directories the operator stages before the run (TTP-2026-0006's payload makes
# this one): no step creates them, every step checks them.
OPERATOR_STAGED = {"/var/lib/cortexsim/edr-007"}
# Windows: scenario id -> (the folder as the step spells it, its marker SID, the
# creating step, the checking steps). See scripts/ci/windows-native-e2e.ps1
# -Phase sandbox, which runs these guards on Windows PowerShell 5.1.
WINDOWS_SANDBOXES = {
    "SIM-MP-023": ("'C:\\ProgramData\\cortexsim-mp023'", "S-1-9-4243-23", "step-01", ["step-03", "step-04"]),
    "SIM-MP-026": ("'C:\\ProgramData\\cortexsim-mp026'", "S-1-9-4243-26", "step-01",
                   ["step-02", "step-03", "step-06", "step-07", "step-08", "step-09"]),
    "SIM-MP-029": ("Join-Path $env:TEMP 'cortexsim-mp-029'", "S-1-9-4243-29", "step-01", ["step-02", "step-03"]),
}
OTHER_UID = 4242  # stands in for another local user


@functools.lru_cache(maxsize=None)
def _corpus() -> dict[str, dict]:
    found = {}
    for path in (REPO_ROOT / "scenarios").rglob("*.yml"):
        doc = yaml.safe_load(path.read_text())
        if isinstance(doc, dict) and doc.get("scenario_id"):
            found[doc["scenario_id"]] = doc
    return found


def _scenarios() -> dict[str, dict]:
    found = {sid: doc for sid, doc in _corpus().items() if sid in SANDBOXES}
    assert set(found) == set(SANDBOXES)
    return found


def _texts(step: dict):
    """(field, shell text) for the step's command and each POSIX platform variant."""
    yield "command", step.get("command") or ""
    for os_name, text in (step.get("platform_variants") or {}).items():
        if os_name != "windows":
            yield f"platform_variants.{os_name}", text or ""


def _names(text: str, path: str) -> bool:
    """True when ``text`` names ``path`` itself (not a longer name that starts with it)."""
    return re.search(re.escape(path) + r"(?![A-Za-z0-9._@+-])", text) is not None


def _refs(command: str, sandbox: str) -> list[str]:
    """Every way ``command`` spells the sandbox root: the path, and variables set to it."""
    names = re.findall(rf"(?m)(?:^|;)\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=([\"']?){re.escape(sandbox)}\2\s*(?:;|$)",
                       command)
    return [sandbox, f'"{sandbox}"'] + [form for n, _ in names for form in (f'"${n}"', f"${n}")]


def _lookup(r: str) -> str:
    """The one lookup, with the optional macOS fallback inside the same substitution."""
    return rf"\$\(stat -c %F:%u:%a {r} 2>/dev/null(?: \|\| stat -f %HT:%u:%Lp {r} 2>/dev/null)?\)"


def _guard(command: str, sandbox: str) -> tuple[str, re.Match, re.Match] | None:
    """(kind, first guard, the guard that defines the kind) for ``sandbox``, or None.

    A step that creates the directory is a 'create' step even when an
    ownership-tested read comes first (SIM-KOI-012 reads a pidfile that way).
    """
    for ref in _refs(command, sandbox):
        r = re.escape(ref)
        create = re.search(rf"rm -rf {r}(?: &&|\n)\s*mkdir -m [0-7]{{3}} {r}(?: \|\| \{{[^\n]*?exit 1; \}})?", command)
        mine = r'"directory:\$\(id -u\):[0-7]{3}"'
        alt = r'\[ "\$s" = "[dD]irectory:(?:0|\$\(id -u\)):[0-7]{3}" \]'
        check = re.search(
            rf'(?:\[ "{_lookup(r)}" = {mine} \]'          # the owner's check
            rf"|s={_lookup(r)}; {alt}(?: \|\| {alt})*)"    # stored: root's or mine; BSD spelling
            rf"(?: \|\| \{{[^\n]*?exit 1; \}})?", command)
        found = [m for m in (create, check) if m]
        if found:
            return ("create" if create else "check"), min(found, key=lambda m: m.start()), create or check
    return None


def _first_use(command: str, sandbox: str) -> int | None:
    """Offset of the first reference to something inside the sandbox.

    Naming a path inside it in an assignment (``STAGE="$OUT/pkg"``) touches
    nothing on disk, so that is not a use.
    """
    hits = []
    for ref in _refs(command, sandbox):
        for m in re.finditer(re.escape(ref.rstrip('"') + "/"), command):
            line_head = command[command.rfind("\n", 0, m.start()) + 1:m.start()]
            if not re.search(r'(?:^|[\s;])[A-Za-z_][A-Za-z0-9_]*="?$', line_head):
                hits.append(m.start())
                break
    return min(hits) if hits else None


def _cases():
    for sid, doc in sorted(_scenarios().items()):
        default = (doc.get("execution_identity") or {}).get("default")
        for sandbox in SANDBOXES[sid]:
            for step in doc["steps"]:
                for field, command in _texts(step):
                    if _names(command, sandbox):
                        tag = "" if field == "command" else ":" + field.split(".", 1)[1]
                        yield pytest.param(sid, step["id"], step.get("identity") or default, sandbox, command,
                                           id=f"{sid}:{step['id']}{tag}:{sandbox}")


CASES = list(_cases())


@pytest.mark.parametrize("sid, step_id, identity, sandbox, command", CASES)
def test_a_step_guards_its_scratch_directory_before_using_it(sid, step_id, identity, sandbox, command):
    assert f"mkdir -p {sandbox}\n" not in command + "\n" and f"mkdir -p {sandbox} " not in command
    assert not re.search(r"directory:\$\(id -u [a-z_]", command), \
        "a check must not trust a directory another account owns: keep this step's files in $C"
    guard = _guard(command, sandbox)
    assert guard is not None, "no creation or ownership guard for " + sandbox
    use = _first_use(command, sandbox)
    assert use is None or guard[1].start() < use, "the sandbox is used before it is guarded"


def test_each_scratch_directory_is_created_once_with_the_mode_its_checks_expect():
    for sid, doc in _scenarios().items():
        for sandbox in SANDBOXES[sid]:
            guards = [g for s in doc["steps"] for _, text in _texts(s) if (g := _guard(text, sandbox))]
            assert guards, (sid, sandbox, "no step makes or checks it")
            made = [re.search(r"mkdir -m ([0-7]{3})", g[2].group(0)).group(1) for g in guards if g[0] == "create"]
            assert len(made) == (0 if sandbox in OPERATOR_STAGED else 1), (sid, sandbox, made)
            expected = {mode for g in guards if g[0] == "check"
                        for mode in re.findall(r"irectory:[^:]+:([0-7]{3})", g[2].group(0))}
            allowed = set(made) or {"700"}       # an operator-staged directory must be the user's 0700 one
            assert expected <= allowed, (sid, sandbox, "created " + str(made), "checked for " + str(expected))


def _private_cases():
    for sid, names in sorted(PRIVATE_FILES.items()):
        for name in names.split():
            yield pytest.param(sid, name, id=f"{sid}:{name}")


PRIVATE_CASES = list(_private_cases())


def _private_users(doc: dict, name: str) -> list[tuple[dict, str]]:
    return [(s, text) for s in doc["steps"] for _, text in _texts(s) if f'"$C/{name}"' in text]


@pytest.mark.parametrize("sid, name", PRIVATE_CASES)
def test_a_single_step_file_lives_in_a_directory_the_step_makes(sid, name):
    doc = _corpus()[sid]
    old = "/tmp/" + name
    texts = [text for s in doc["steps"] for _, text in _texts(s)]
    texts += (doc.get("cleanup") or {}).get("commands") or []
    assert not [t for t in texts if _names(t, old)], f"{old} is still named by a command or cleanup line"
    users = _private_users(doc, name)
    assert users, f"no step keeps {name} in a private directory"
    default = (doc.get("execution_identity") or {}).get("default")
    for step, text in users:
        assert (step.get("identity") or default) in RUNS_AS_BEACON, step["id"]
        made = text.find(PRIVATE_DIR)
        assert 0 <= made < text.find("$C/"), f"{step['id']} uses $C before it makes it"


def _windows(doc: dict, step_id: str) -> str:
    return (next(s for s in doc["steps"] if s["id"] == step_id).get("platform_variants") or {})["windows"]


@pytest.mark.parametrize("sid", sorted(WINDOWS_SANDBOXES))
def test_a_windows_step_makes_or_checks_its_fixed_folder_first(sid):
    """The Windows half of the rule, as text. What it does on Windows PowerShell 5.1
    is proven by the CI windows job's sandbox phase, which runs these guards."""
    path, marker, creator, checkers = WINDOWS_SANDBOXES[sid]
    doc = _corpus()[sid]
    create = _windows(doc, creator)
    assert create.startswith(f"$sandbox = {path}\n"), create[:120]
    head = create[:create.index("is not empty: something else is at that path")]
    made = head.index("$null = [System.IO.Directory]::CreateDirectory($sandbox, $sd)")
    assert head.index('if (Test-Path -LiteralPath $sandbox) { cmd.exe /c rmdir /s /q "$sandbox" }') < made
    assert f"(A;OICI;FA;;;$me)(A;;0x1;;;{marker})" in head[:made]
    # The ACL arrives with the folder: a mkdir followed by Set-Acl leaves the folder
    # open to every account that may add files to its parent until the Set-Acl lands.
    assert "Set-Acl" not in head and "mkdir" not in head.replace("rmdir", "")
    assert made < head.index(f"($ids -contains '{marker}')") < head.index("if (Get-ChildItem -LiteralPath $sandbox -Force)")
    for step_id in checkers:
        check = _windows(doc, step_id)
        assert check.startswith(f"$sandbox = {path}\n"), (step_id, check[:120])
        head = check[:check.index("did not complete")]
        assert "$acl = Get-Acl -LiteralPath $sandbox" in head and f"($ids -contains '{marker}')" in head, step_id
        assert "AreAccessRulesProtected" in head and "GetOwner(" in head, step_id
    # PowerShell 5.1's Remove-Item -Recurse walks into a junction's target; cmd's rd does not.
    folder = path.split("'")[-2]
    texts = [v for s in doc["steps"] for k, v in (s.get("platform_variants") or {}).items() if k == "windows"]
    texts += doc["cleanup"]["commands"]
    assert not [t for t in texts if "Remove-Item -Recurse" in t and folder in t], folder


# --------------------------------------------------------------------------- behaviour

_IN_CONTAINER = os.path.exists("/.dockerenv") or os.environ.get("CORTEXSIM_SANDBOX_TESTS") == "1"
needs_root_in_a_container = pytest.mark.skipif(
    os.geteuid() != 0 or not _IN_CONTAINER,
    reason="plants paths at fixed system locations as root: runs only as root inside a container "
           "(the CI image), or with CORTEXSIM_SANDBOX_TESTS=1")


@pytest.fixture(scope="module")
def accounts():
    """The service accounts the steps run as, created in the container when absent."""
    wanted = {identity for _, _, identity, _, _ in (c.values for c in CASES)} - RUNS_AS_BEACON
    for name in sorted(wanted):
        try:
            pwd.getpwnam(name)
        except KeyError:
            subprocess.run(["useradd", "--system", "--no-create-home", "--shell", "/usr/sbin/nologin", name],
                           check=True)
    return wanted


def _guard_script(command: str, sandbox: str) -> str:
    """The step's own guard, as written, with the assignments it relies on."""
    _, _, match = _guard(command, sandbox)
    head = command[:match.start()]
    same_line = head.rsplit("\n", 1)[-1]          # e.g. `D=/tmp/.xmr; ` before the guard
    keep = [line.strip() for line in head[:len(head) - len(same_line)].splitlines()
            if re.fullmatch(r"\s*(?:set -eu|(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*=\S+)\s*", line)]
    prefix = same_line if re.fullmatch(r'(?:\s*|.*"\s*)[A-Za-z_][A-Za-z0-9_]*=\S+;\s*', same_line) else ""
    prefix = re.sub(r'^.*"\s*(?=[A-Za-z_][A-Za-z0-9_]*=)', "", prefix)   # a one-line YAML command
    return "\n".join(keep + [prefix + match.group(0), "echo REACHED"]) + "\n"


def _run(script: str, identity: str, shell: str = "/bin/sh") -> subprocess.CompletedProcess:
    argv = [shell, "-c", script]
    if identity not in RUNS_AS_BEACON:
        argv = ["runuser", "-u", identity, "--"] + argv
    return subprocess.run(argv, capture_output=True, text=True, timeout=30)


def _uid(identity: str) -> int:
    return 0 if identity in RUNS_AS_BEACON else pwd.getpwnam(identity).pw_uid


def _owner(script: str, runner: int) -> int:
    """Who the guard accepts besides the running user: root, or no one else."""
    return 0 if "directory:0:" in script else runner


def _clear(*paths: str) -> None:
    for path in paths:
        if os.path.islink(path) or os.path.isfile(path):
            os.unlink(path)
        else:
            shutil.rmtree(path, ignore_errors=True)


def _plant_dir(path: str, uid: int) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)   # /var/lib/cortexsim for an operator-staged one
    os.mkdir(path, 0o755)
    Path(path, "planted").write_text("planted\n")
    os.chown(path, uid, uid)
    os.chown(os.path.join(path, "planted"), uid, uid)


@needs_root_in_a_container
@pytest.mark.parametrize("sid, step_id, identity, sandbox, command", CASES)
def test_a_planted_scratch_directory_is_never_used(sid, step_id, identity, sandbox, command, tmp_path, accounts):
    kind = _guard(command, sandbox)[0]
    script = _guard_script(command, sandbox)
    runner = _uid(identity)
    elsewhere = str(tmp_path / "elsewhere")
    os.chmod(tmp_path, 0o755)
    try:
        if kind == "create":
            # Another user's directory is at the path: the step ends with its own
            # fresh directory, or it stops. It never carries on in theirs.
            _clear(sandbox); _plant_dir(sandbox, OTHER_UID)
            done = _run(script, identity)
            if "REACHED" in done.stdout:
                assert os.stat(sandbox).st_uid == runner and not os.listdir(sandbox), done
            else:
                assert done.returncode != 0, done
            # A symlink at the path is replaced, and its target is left alone.
            _clear(sandbox); _plant_dir(elsewhere, OTHER_UID); os.symlink(elsewhere, sandbox)
            done = _run(script, identity)
            if "REACHED" in done.stdout:
                assert not os.path.islink(sandbox) and os.stat(sandbox).st_uid == runner, done
            else:
                assert done.returncode != 0, done
            assert os.listdir(elsewhere) == ["planted"]
            # Nothing at the path: the step makes the directory and owns it.
            _clear(sandbox)
            done = _run(script, identity)
            assert "REACHED" in done.stdout and os.stat(sandbox).st_uid == runner, done
        else:
            mode = int(re.search(r"irectory:\$\(id -u\):([0-7]{3})", script).group(1), 8)
            owner = _owner(script, runner)
            for plant in ("missing", "other-owned", "symlink", "writable-by-others"):
                _clear(sandbox, elsewhere)
                if plant == "other-owned":
                    _plant_dir(sandbox, OTHER_UID)
                elif plant == "symlink":      # to a directory that would itself pass
                    os.mkdir(elsewhere); os.chmod(elsewhere, mode); os.chown(elsewhere, owner, owner)
                    os.symlink(elsewhere, sandbox)
                elif plant == "writable-by-others":
                    os.mkdir(sandbox); os.chmod(sandbox, 0o777); os.chown(sandbox, owner, owner)
                done = _run(script, identity)
                assert done.returncode == 1 and "REACHED" not in done.stdout, (plant, done)
            # The directory the creating step makes is accepted.
            _clear(sandbox, elsewhere)
            os.mkdir(sandbox); os.chmod(sandbox, mode); os.chown(sandbox, owner, owner)
            done = _run(script, identity)
            assert done.returncode == 0 and "REACHED" in done.stdout, done
    finally:
        _clear(sandbox, elsewhere)


@needs_root_in_a_container
@pytest.mark.parametrize("sid, name", PRIVATE_CASES)
def test_a_planted_file_or_link_at_the_old_name_is_never_written_or_read(sid, name, tmp_path):
    """The step's own private-directory line, run as root against a planted old name."""
    users = _private_users(_corpus()[sid], name)
    assert users and all(PRIVATE_DIR in text for _, text in users)
    old = "/tmp/" + name
    os.chmod(tmp_path, 0o755)
    target = tmp_path / "target"
    for plant in ("file", "symlink"):
        for ending in ("", "exit 3\n"):
            _clear(old)
            target.write_text("untouched\n")
            if plant == "file":         # another user's file, open to all, with their content
                Path(old).write_text("planted\n"); os.chown(old, OTHER_UID, OTHER_UID); os.chmod(old, 0o666)
            else:                       # another user's link to a file root would clobber
                os.symlink(target, old); os.lchown(old, OTHER_UID, OTHER_UID)
            script = (PRIVATE_DIR + "\n"
                      f'printf "step\\n" > "$C/{name}"\n'
                      f'cat "$C/{name}"\n'
                      'stat -c %F:%u:%a "$C"\n'
                      'echo "C=$C"\n' + ending)
            done = _run(script, "root")
            try:
                assert done.returncode == (3 if ending else 0), done
                assert done.stdout.splitlines()[:2] == ["step", "directory:0:700"], done
                made = re.search(r"^C=(/tmp/cortexsim\.\w+)$", done.stdout, re.M).group(1)
                assert not os.path.exists(made), "the private directory outlived the step"
                assert target.read_text() == "untouched\n"
                if plant == "file":
                    assert Path(old).read_text() == "planted\n" and os.stat(old).st_uid == OTHER_UID
            finally:
                _clear(old)


# A one-of-each sample of the guard forms in the corpus: the owner's check, a
# stored lookup also accepting root, and each again with the macOS fallback.
# The race replays each, as written, against an exchange.
RACE_FORMS = [
    ("SIM-ITDR-017", "step-02", "/tmp/cortexsim-itdr-017"),     # [ "$(stat ...)" = mine ]
    ("SIM-CDR-002", "step-02", "/tmp/.xmr"),                     # the same through a variable
    ("SIM-MP-013", "step-05", "/tmp/cortexsim-mp013"),           # stored: root's or mine
    ("SIM-TIM-008", "step-02", "/tmp/cortexsim-tim008"),         # portable: mine
    ("SIM-TIM-008", "step-03", "/tmp/cortexsim-tim008"),         # portable: root's or mine
    ("SIM-CLOUD-010", "step-02", "/tmp/cortexsim-cloud010"),     # portable: mine, mode 700
]
_EXCHANGER = r"""
import ctypes, os, sys
libc = ctypes.CDLL(None, use_errno=True)
a, b = (p.encode() for p in sys.argv[1:3])
while True:
    libc.renameat2(-100, a, -100, b, 2)   # AT_FDCWD, RENAME_EXCHANGE
"""


def _race(script: str, identity: str, path: str, decoy_target: str, own_dir: str, passes: int) -> dict[str, int]:
    """Run ``script`` ``passes`` times per shell while another user swaps ``path`` between
    a symlink to ``decoy_target`` (a directory the guard would accept) and ``own_dir``."""
    alt = path + ".alt"
    os.symlink(decoy_target, path); os.lchown(path, OTHER_UID, OTHER_UID)
    os.rename(own_dir, alt)
    swapper = subprocess.Popen([sys.executable, "-c", _EXCHANGER, path, alt],
                               preexec_fn=lambda: (os.setgid(OTHER_UID), os.setuid(OTHER_UID)))
    try:
        return {shell: sum("REACHED" in _run(script, identity, shell).stdout for _ in range(passes))
                for shell in ("/bin/dash", "/bin/bash")}
    finally:
        swapper.kill(); swapper.wait()


@needs_root_in_a_container
@pytest.mark.parametrize("sid, step_id, sandbox", RACE_FORMS, ids=[f"{s}:{t}" for s, t, _ in RACE_FORMS])
def test_a_guard_cannot_be_raced_by_an_exchange(sid, step_id, sandbox, accounts):
    """A non-sticky world-writable parent lets another user rename entries at will
    (and fs.protected_symlinks does not apply there), so the guard's path flips
    between a symlink to an acceptable directory and that user's own directory.
    The guard must never pass; the old three-test form, run the same way, does."""
    doc = _corpus()[sid]
    step = next(s for s in doc["steps"] if s["id"] == step_id)
    identity = step.get("identity") or (doc.get("execution_identity") or {}).get("default")
    runner = _uid(identity)
    base = tempfile.mkdtemp(dir="/tmp", prefix="cortexsim-race.")
    try:
        os.chmod(base, 0o755)
        parent = os.path.join(base, "open")
        os.mkdir(parent); os.chmod(parent, 0o777)              # world-writable, no sticky bit
        race_path = os.path.join(parent, "sb")
        script = _guard_script(step["command"], sandbox).replace(sandbox, race_path)
        mode = int(re.search(r"irectory:\$\(id -u\):([0-7]{3})", script).group(1), 8)
        owner = _owner(script, runner)
        decoy = os.path.join(base, "acceptable")
        os.mkdir(decoy); os.chmod(decoy, mode); os.chown(decoy, owner, owner)
        own = os.path.join(base, "theirs")
        os.mkdir(own); os.chmod(own, mode); os.chown(own, OTHER_UID, OTHER_UID)
        got = _race(script, identity, race_path, decoy, own, passes=200)
        # the control: root running three separate tests, each its own lookup, on the
        # same exchange between a link to root's directory and the other user's one
        _clear(race_path, race_path + ".alt")
        roots = os.path.join(base, "roots")
        os.mkdir(roots); os.chmod(roots, 0o700)
        os.mkdir(own); os.chmod(own, 0o700); os.chown(own, OTHER_UID, OTHER_UID)
        three = (f'[ -d "{race_path}" ] && [ ! -L "{race_path}" ] && [ -O "{race_path}" ] || exit 1\n'
                 "echo REACHED\n")
        control = _race(three, "root", race_path, roots, own, passes=200)
        assert sum(control.values()) > 0, ("the exchange never landed between two lookups, "
                                           "so this run proves nothing", control)
        assert got == {"/bin/dash": 0, "/bin/bash": 0}, (got, "control", control)
    finally:
        shutil.rmtree(base, ignore_errors=True)

#!/usr/bin/env python3
"""Dependency-light scenario-YAML linter for edit-time feedback.

The authoritative validator is the boot-time loader (core/engine/scenario_loader.py),
but that imports SQLAlchemy/Pydantic and only runs inside the app's Python 3.11
image — too heavy for a per-edit hook on the host. This linter reproduces the
loader's *contract* using only pyyaml, so it runs anywhere:

  - REQUIRED-field presence + enum membership + step structure  → ERROR (blocks),
    mirroring the loader, which REJECTS schema-invalid scenarios at boot.
  - cleanup shape (must be a {commands: [...]} mapping, not a list)  → ERROR,
    the exact CleanupSchema mismatch the loader rejects (Pydantic).
  - dangling ttp_ref / adapter_ref                              → WARNING only,
    mirroring the loader, which logs these as non-fatal warnings.
  - detection_id slug resolution against the referenced card    → WARNING,
    reproducing ttp_catalog._slug so a typo'd slug is caught at edit time.

Usage:  python3 lint-scenario.py <scenario.yml> [repo_root]
Exit:   0 = schema-valid (warnings may be printed), 1 = schema error / not lintable
"""
import glob
import json
import re
import sys
import textwrap
from pathlib import Path

try:
    import yaml
except ImportError:
    # No pyyaml → can't lint; don't block the edit, just say so.
    print("lint-scenario: pyyaml unavailable, skipping (boot loader is authoritative)")
    sys.exit(0)

# Must mirror core/engine/scenario_loader.VALID_PLANES exactly — a plane the
# loader accepts but this set omits makes the hook block an edit the boot loader
# would load cleanly (DLP was the drift this comment now guards against).
PLANES = {
    "EDR", "CDR", "NDR", "ITDR", "CLOUD_APP", "ANALYTICS", "AI_ACCESS", "AIRS",
    "AI_SPM", "BROWSER", "KOI", "ASM", "CSPM", "TIM", "EMAIL", "DLP",
}
DETECTION_TYPES = {"BIOC", "XQL", "Analytics", "Correlation", "IOC", "ABIOC"}
STATUSES = {"active", "draft", "deprecated"}
# Causality contract vocabulary (mirrors scenario_loader._PIVOTS / _PLATFORMS
# and causality_graph EDGE_* kinds).
PIVOTS = {
    "process_lineage", "network_session", "endpoint_network_stitch",
    "shared_entity", "exposure_exploit", "exploit_impact", "temporal",
}
PLATFORMS = {"linux", "windows", "macos", "container", "k8s"}
SCENARIO_ID_RE = re.compile(r"^SIM-[A-Z_]+-\d{3,}$")

REQUIRED_TOP = [
    "scenario_id", "name", "version", "status", "plane", "detection_types",
    "uc_ref", "tc_ref", "uc_name", "tc_name",
    "mitre_tactic", "mitre_tactic_name", "mitre_technique", "mitre_technique_name",
    "execution_identity", "push_supported", "pull_supported", "steps",
]
REQUIRED_STEP = ["id", "name", "command", "identity", "mitre_technique", "expected_detections"]
REQUIRED_DET = ["plane", "type", "description"]


def _index_ids(pattern: str, id_regex: str) -> set:
    ids = set()
    rx = re.compile(id_regex)
    for path in glob.glob(pattern, recursive=True):
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError:
            continue
        ids.update(rx.findall(text))
    return ids


def _slug(name: str, prefix: str) -> str:
    """EXACT copy of core/engine/ttp_catalog._slug so resolution never false-warns."""
    clean = "".join(c if c.isalnum() else "-" for c in str(name).lower()).strip("-")
    while "--" in clean:
        clean = clean.replace("--", "-")
    return f"{prefix}-{clean}"[:120]


def _card_detection_ids(card: dict) -> set:
    """Rebuild the set of valid detection_ids a card exposes, mirroring the
    per-kind id derivation in ttp_catalog (explicit detection_id/rule_id wins,
    else the slug of the name/value)."""
    dets = card.get("detections") or {}
    ids: set = set()
    for i, b in enumerate(dets.get("biocs") or []):
        ids.add(b.get("detection_id") or _slug(b.get("name") or f"bioc-{i+1}", "bioc"))
    for i, q in enumerate(dets.get("xql_queries") or []):
        ids.add(q.get("detection_id") or _slug(q.get("name") or f"xql-{i+1}", "xql"))
    for i, a in enumerate(dets.get("abiocs") or []):
        ids.add(a.get("detection_id") or _slug(a.get("name") or f"abioc-{i+1}", "abioc"))
    for i, r in enumerate(dets.get("correlation_rules") or []):
        ids.add(r.get("rule_id") or r.get("detection_id")
                or _slug(r.get("name") or f"correlation-{i+1}", "correlation"))
    for i, m in enumerate(dets.get("modeling_rules") or []):
        ids.add(m.get("detection_id") or _slug(m.get("name") or f"modeling-{i+1}", "modeling"))
    for i, ioc in enumerate(dets.get("iocs") or []):
        if isinstance(ioc, dict):
            val = ioc.get("value") or f"ioc-{i+1}"
            ioc_type = ioc.get("type") or ioc.get("ioc_type") or "ioc"
            ids.add(ioc.get("detection_id") or _slug(f"{ioc_type}-{val}", "ioc"))
        else:
            ids.add(_slug(str(ioc), "ioc"))
    return ids


def _load_cards_by_id(root: Path) -> dict:
    """Map every TTP card id -> its parsed JSON (for slug resolution)."""
    out: dict = {}
    for path in glob.glob(str(root / "detection_scanner" / "ttps" / "*.json")):
        try:
            card = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(card, dict) and card.get("id"):
            out[card["id"]] = card
    return out


# ---- Indicators (S-22..S-27) — must mirror core/engine/scenario_loader.py ----
INDICATOR_TYPES = frozenset({
    "sha256", "sha1", "md5", "domain", "url", "ipv4", "ipv6", "user_agent", "ja3", "ja4",
})
INDICATOR_SAFE_FORMS = frozenset({
    "log_field", "dns_lookup", "hash_only", "wildfire_test_file", "eicar",
})
SOURCE_URL_HOSTS = (
    "https://unit42.paloaltonetworks.com/",
    "https://github.com/paloaltonetworks/",
)
_HASH_TYPES = frozenset({"sha256", "sha1", "md5"})
_HASH_LEN = {"sha256": 64, "sha1": 40, "md5": 32}
_HASH_SAFE_FORMS = frozenset({"hash_only", "wildfire_test_file", "eicar", "log_field"})
_SAFE_FORMS_BY_TYPE = {
    **{t: _HASH_SAFE_FORMS for t in _HASH_TYPES},
    "domain": frozenset({"dns_lookup", "log_field"}),
    "url": frozenset({"log_field"}),
    "ipv4": frozenset({"log_field"}),
    "ipv6": frozenset({"log_field"}),
    "user_agent": frozenset({"log_field"}),
    "ja3": frozenset({"log_field"}),
    "ja4": frozenset({"log_field"}),
}
_INDICATOR_ID_RE = re.compile(r"^ind-\d{2}$")
_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9-]+\.)+[a-z]{2,}$")
_IPV4_RE = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")
# getent hosts / resolvectl query cannot name a resolver, so they are never exempt.
DNS_LOOKUP_RE = re.compile(r"^\s*(dig|nslookup|host|drill)\b")
FETCH_VERB_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(curl|wget|pip3?|npm|git\s+clone|Invoke-WebRequest|iwr|certutil|bitsadmin|Start-BitsTransfer|aria2c|scp|tftp|ftp)(?:\.exe)?(?![A-Za-z0-9_.-])", re.IGNORECASE)
# Mirrors core/eal_simulator/campaign.py::_CAMPAIGN_ID_RE (parity test in
# tests/engine/test_lint_scenario_hook.py). A rejected id makes the step emit nothing.
CAMPAIGN_ID_RE = re.compile(r"^CMP(?:-[A-Z0-9]+)+-\d{3,5}$")
_CAMPAIGN_ID_LINE_RE = re.compile(r"^\s*campaign_id:\s*(['\"]?)([^'\"\s#]*)\1\s*(?:#.*)?$")
# S-25 shared regexes: byte-identical copies of core/engine/scenario_loader.py; tests/engine/test_lint_scenario_hook.py pins them.
# Heredoc opener: '<<' / '<<-' that is not a here-string ('<<<'), with a
# word-like delimiter. Applied (by _heredoc_delim) to the line with comments and
# $(( )) / (( )) arithmetic removed, so '<<' there opens nothing.
_HEREDOC_RE = re.compile(r"(?<!<)<<(?!<)-?[ \t]*(['\"]?)([A-Za-z_][\w.-]*)\1")
_SH_COMMENT_RE = re.compile(r"(?:^|[ \t])#.*$")
_SH_ARITH_RE = re.compile(r"\(\((?:[^()]|\([^()]*\))*\)\)")
# Chaining that can add a connection to a lookup line: ; && || | $( ` and
# redirection / background / process substitution < > & <( >(.
_CHAIN_RE = re.compile(r";|&&|\|\||\||\$\(|`|<\(|>\(|<|>|&")
# A lookup line must name its resolver: dig/drill/host by a whitespace-led
# '@<token>' (which may not be the indicator); nslookup by $LAB_RESOLVER,
# $env:LAB_RESOLVER or ${LAB_RESOLVER...} as the token after the queried name.
_AT_LOOKUP_RE = re.compile(r"^\s*(?:dig|drill|host)\b.*?(?:^|\s)@\S+")
_NSLOOKUP_RESOLVER_RE = re.compile(
    r"^\s*nslookup\s+(?:-\S+\s+)*[^\s-]\S*\s+[\"']?(?:\$LAB_RESOLVER|\$env:LAB_RESOLVER|\$\{LAB_RESOLVER[^}\s]*\})[\"']?(?:\s|$)")
_RESOLVER_AT_RE = re.compile(r"(?:^|\s)@(\S+)")
# A params: block is data only inside a campaign-file write ...
_CAMPAIGN_FILE_WRITE_RE = re.compile(r"(?<![\w./-])cat\s+>\s*['\"]?[^\s'\"<>]+\.ya?ml['\"]?\s+<<(?!<)")
# Any spelling of a 'plugin' key (plugin:, plugin :, "plugin":, 'plugin':).
_PLUGIN_KEY_RE = re.compile(r"^\s*(?:-\s+)?[\"']?plugin[\"']?\s*:(.*)$")
_URL_HOST_RE = re.compile(r"^https?://(?:[^/@\s]*@)?(\[[^\]\s]+\]|[^/:?#\s]+)", re.IGNORECASE)
# ... whose every plugin is a collector-POST log streamer (records POSTed to an
# operator-supplied collector, so an indicator is a field value, not a peer).
# Mirrored in the hook; parity asserted in tests/engine/test_lint_scenario_hook.py.
COLLECTOR_POST_PLUGINS = frozenset({
    "ngfw_eal_emitter", "ngfw_threat_emitter", "cloud_audit_emitter",
    "cloud_storage_compute_emitter", "azure_audit_emitter", "k8s_audit_emitter",
    "m365_activity_emitter", "ad_windows_emitter", "idp_signin_emulator", "email_emitter",
    "duo_auth_emitter", "globalprotect_emitter", "third_party_alert_emitter",
    "third_party_firewall_emitter", "third_party_vpn_emitter",
})

# Marker-only steps: a command that only echoes / no-ops proves nothing.
# Canonical rule (shared with the coverage script): split on \n ; && || | ;
# every fragment must be a marker or a localhost curl.
_MARKER_LINE_RE = re.compile(
    r"^\s*(echo|printf|Write-Host|Write-Output|true)\b|^\s*[:#](?=\s|$)|^\s*$")
_LOCAL_CURL_RE = re.compile(r"curl.*(127\.0\.0\.1|localhost|\$\{?STAGE_HOST)")
_SEGMENT_SPLIT_RE = re.compile(r"\n|;|&&|\|\||\|")


def is_marker_only(command) -> bool:
    """True when every fragment is a marker or a localhost curl (None/empty: True)."""
    command = command or ""
    if "scripts.eal_simulator.cli" in command or "cortexsim-eal" in command:
        return False
    return all(_MARKER_LINE_RE.match(seg) or _LOCAL_CURL_RE.search(seg)
               for seg in _SEGMENT_SPLIT_RE.split(command))


def _ipv4_is_routable(value: str) -> bool:
    m = _IPV4_RE.match(value)
    if not m:
        return False
    a, b, c, d = (int(g) for g in m.groups())
    if max(a, b, c, d) > 255:
        return False
    if a in (0, 10, 127) or (a == 169 and b == 254) or (a == 192 and b == 168):
        return False
    if a == 172 and 16 <= b <= 31:
        return False
    return True


def _ipv6_is_routable(value: str) -> bool:
    import ipaddress
    try:
        ip = ipaddress.IPv6Address(value)
    except ValueError:
        return False
    return not (ip.is_loopback or ip.is_link_local or ip.is_unspecified
                or ip.is_multicast or (ip.packed[0] & 0xFE) == 0xFC)


def indicator_errors(indicators: list, step_ids: set) -> list:
    """S-22 / S-23 / S-24 / S-27 per indicator (dict-shaped)."""
    out = []
    seen = set()
    for k, ind in enumerate(indicators, 1):
        if not isinstance(ind, dict):
            out.append(f"S-22: indicators[{k}] is not a mapping")
            continue
        iid = ind.get("id")
        if isinstance(iid, str):
            if iid in seen:
                out.append(f"S-22: duplicate indicator id {iid!r}")
            seen.add(iid)
        typ, v = ind.get("type"), ind.get("value")
        if not isinstance(iid, str) or not _INDICATOR_ID_RE.match(iid):
            out.append(f"S-22: indicator id {iid!r} must match ind-NN")
        if not isinstance(typ, str) or typ not in INDICATOR_TYPES:
            out.append(f"S-22: indicator {iid} type {typ!r} not in {sorted(INDICATOR_TYPES)}")
            continue
        if not isinstance(v, str) or not v.strip():
            out.append(f"S-22: indicator {iid} value is empty")
            continue
        bad = None
        if typ in _HASH_TYPES:
            if not re.fullmatch(r"[A-Fa-f0-9]{%d}" % _HASH_LEN[typ], v):
                bad = f"is not a {_HASH_LEN[typ]}-hex {typ}"
        elif typ == "ipv4":
            if not _ipv4_is_routable(v):
                bad = "is not a routable dotted-quad (private/loopback/link-local/0.* refused)"
        elif typ == "ipv6":
            if not _ipv6_is_routable(v):
                bad = "is not a routable IPv6 address"
        elif typ == "domain":
            if "[" in v or "]" in v or not _DOMAIN_RE.match(v.lower()):
                bad = "is not a plain, un-defanged FQDN"
        elif typ == "url":
            if not v.lower().startswith(("http://", "https://")) or "hxxp" in v.lower():
                bad = "must start http:// or https:// and be un-defanged"
        if bad:
            out.append(f"S-22: indicator {iid} value {v!r} {bad}")
        src = ind.get("source_url")
        if not isinstance(src, str) or not src.lower().startswith(SOURCE_URL_HOSTS):
            out.append(f"S-23: indicator {iid} source_url must start with one of {list(SOURCE_URL_HOSTS)}")
        surf = ind.get("surfaces_in")
        if not isinstance(surf, list) or not surf:
            out.append(f"S-24: indicator {iid} surfaces_in must name at least one step")
        else:
            missing = [x for x in surf if not isinstance(x, str) or x not in step_ids]
            if missing:
                out.append(f"S-24: indicator {iid} surfaces_in names unknown step(s) {missing}")
        if "in_band" in ind and not isinstance(ind["in_band"], bool):
            out.append(f"S-22: indicator {iid} in_band must be a bool, got {ind['in_band']!r}")
        sf = ind.get("safe_form")
        if not isinstance(sf, str) or sf not in INDICATOR_SAFE_FORMS:
            out.append(f"S-27: indicator {iid} safe_form {sf!r} not in {sorted(INDICATOR_SAFE_FORMS)}")
        elif sf not in _SAFE_FORMS_BY_TYPE[typ]:
            out.append(
                f"S-27: indicator {iid} safe_form {sf!r} is not valid for type {typ!r} "
                f"(allowed: {sorted(_SAFE_FORMS_BY_TYPE[typ])})")
    return out


def _indicator_units(steps: list, cleanup_commands) -> list:
    """(label, command) for every executable string: step command, each
    platform_variants value, each cleanup command."""
    units = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        sid = step.get("id")
        units.append((f"step {sid}", step.get("command") or ""))
        pv = step.get("platform_variants")
        if isinstance(pv, dict):
            for os_name, cmd in pv.items():
                if isinstance(cmd, str):
                    units.append((f"step {sid} platform_variants[{os_name}]", cmd))
    for cmd in cleanup_commands or []:
        if isinstance(cmd, str):
            units.append(("cleanup", cmd))
    return units


def _heredoc_delim(line: str):
    """Mirrors scenario_loader._heredoc_delim."""
    code = _SH_ARITH_RE.sub(" ", _SH_COMMENT_RE.sub("", line))
    m = _HEREDOC_RE.search(code)
    return m.group(2) if m else None


def _heredoc_body(lines: list, delim: str) -> list:
    body = []
    for line in lines:
        if line.strip() == delim:
            break
        body.append(line)
    return body


def _resolver_named(line: str) -> bool:
    """dig/drill/host: a whitespace-led @<token>; nslookup: a LAB_RESOLVER variable."""
    return bool(_NSLOOKUP_RESOLVER_RE.match(line) or _AT_LOOKUP_RE.match(line))


def _plugin_value(raw: str) -> str:
    return re.sub(r"\s+#.*$", "", raw).strip().strip("'\"")


def _params_are_data(opener: str, body: list) -> bool:
    """A heredoc's params: blocks are data only when it writes a campaign file
    (cat > <path>.yml <<), the body parses as YAML with a non-empty steps[], and
    EVERY step's plugin is a collector-POST streamer. Any other shape, any parse
    failure, or any 'plugin' key (in any spelling) with another value fails closed."""
    if not _CAMPAIGN_FILE_WRITE_RE.search(_SH_COMMENT_RE.sub("", opener)):
        return False
    for line in body:
        m = _PLUGIN_KEY_RE.match(line)
        if m and _plugin_value(m.group(1)) not in COLLECTOR_POST_PLUGINS:
            return False
    try:
        doc = yaml.safe_load(textwrap.dedent("\n".join(body)))
    except Exception:  # noqa: BLE001 — any parse failure fails closed
        return False
    steps = doc.get("steps") if isinstance(doc, dict) else None
    if not isinstance(steps, list) or not steps:
        return False
    return all(isinstance(st, dict) and isinstance(st.get("plugin"), str)
               and st["plugin"] in COLLECTOR_POST_PLUGINS for st in steps)


def _indicator_probes(inds: list) -> list:
    """Mirrors scenario_loader._indicator_probes on dict-shaped input."""
    probes = []
    for ind in inds:
        typ, val, iid = ind["type"], ind["value"], ind.get("id")
        probes.append((typ, val, iid, ""))
        if typ == "url":
            m = _URL_HOST_RE.match(val)
            if m:
                host = m.group(1)
                if host.startswith("["):
                    probes.append(("ipv6", host[1:-1], iid, f" (host {host})"))
                else:
                    kind = "ipv4" if _IPV4_RE.match(host) else "domain"
                    probes.append((kind, host, iid, f" (host {host})"))
    return probes


def _indicator_pattern(typ: str, val: str) -> str:
    # Mirrors scenario_loader._probe_pattern exactly (drift guard: the parity test
    # in tests/engine/test_lint_scenario_hook.py). A domain may follow a '.'
    # (subdomain); nothing else may be glued to the front of any value.
    behind = r"(?<![A-Za-z0-9_-])" if typ == "domain" else r"(?<![A-Za-z0-9_.-])"
    return behind + re.escape(val) + r"(?![A-Za-z0-9_-]|\.[A-Za-z0-9])"


def unsafe_indicator_lines(indicators: list, steps: list, cleanup_commands=None) -> list:
    """S-25 / S-26 on dict-shaped input; same contract as the loader's function."""
    out = []
    inds = [i for i in indicators if isinstance(i, dict) and isinstance(i.get("type"), str)
            and i["type"] in INDICATOR_TYPES and isinstance(i.get("value"), str) and i["value"]]
    probes = _indicator_probes(inds)
    probe_key = [repr(p[2]) for p in probes]  # one S-25 per indicator per line (ids may be malformed)
    for label, command in _indicator_units(steps, cleanup_commands):
        lines = command.split("\n")
        heredoc = None
        params_data = False
        params_indent = None
        for n, line in enumerate(lines, start=1):
            in_params = False
            lookup_ok = False
            if heredoc is not None:
                if line.strip() == heredoc:
                    heredoc, params_indent = None, None
                    continue
                stripped = line.strip()
                indent = len(line) - len(line.lstrip())
                if params_indent is not None and stripped and indent <= params_indent:
                    params_indent = None
                if params_indent is None and re.fullmatch(r"params:\s*", stripped):
                    params_indent = indent
                elif params_indent is not None:
                    in_params = params_data
            else:
                delim = _heredoc_delim(line)
                if delim:
                    heredoc = delim
                    params_data = _params_are_data(line, _heredoc_body(lines[n:], delim))
                lookup_ok = (bool(DNS_LOOKUP_RE.match(line)) and not FETCH_VERB_RE.search(line)
                             and not _CHAIN_RE.search(line) and _resolver_named(line))
            hit = set()
            for k, (typ, val, iid, note) in enumerate(probes):
                if typ in _HASH_TYPES:
                    if re.search(re.escape(val), line, re.IGNORECASE) and FETCH_VERB_RE.search(line):
                        out.append(f"S-26: {label} line {n}: hash {iid} on a fetch line: {line.strip()[:120]}")
                    continue
                if typ not in ("domain", "url", "ipv4", "ipv6") or probe_key[k] in hit or in_params:
                    continue
                pat = _indicator_pattern(typ, val)
                if not re.search(pat, line, re.IGNORECASE):
                    continue
                if (typ == "domain" and lookup_ok and not any(
                        re.search(pat, tok, re.IGNORECASE) for tok in _RESOLVER_AT_RE.findall(line))):
                    continue
                hit.add(probe_key[k])
                out.append(
                    f"S-25: {label} line {n}: {typ} {iid}{note} outside a resolver-named DNS-lookup "
                    f"line or a collector campaign params: block: {line.strip()[:120]}")
    return out


def campaign_id_errors(steps: list) -> list:
    """ERROR for every heredoc `campaign_id:` line the EAL validator would refuse."""
    out = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        sid = step.get("id")
        cmds = [step.get("command") or ""]
        pv = step.get("platform_variants")
        if isinstance(pv, dict):
            cmds.extend(v for v in pv.values() if isinstance(v, str))
        for command in cmds:
            if not isinstance(command, str):
                continue
            heredoc = None
            for line in command.split("\n"):
                if heredoc is not None:
                    if line.strip() == heredoc:
                        heredoc = None
                        continue
                    m = _CAMPAIGN_ID_LINE_RE.match(line)
                    if m and not CAMPAIGN_ID_RE.match(m.group(2)):
                        out.append(f"campaign_id '{m.group(2)}' in step {sid} does not match "
                                   f"{CAMPAIGN_ID_RE.pattern}")
                else:
                    delim = _heredoc_delim(line)
                    if delim:
                        heredoc = delim
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print("lint-scenario: no file given", file=sys.stderr)
        return 1
    filepath = Path(sys.argv[1])
    root = Path(sys.argv[2]) if len(sys.argv) > 2 else Path.cwd()

    try:
        raw = yaml.safe_load(filepath.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: YAML parse error: {exc}")
        return 1
    if not isinstance(raw, dict):
        print("ERROR: YAML root is not a mapping")
        return 1

    errors: list[str] = []
    warnings: list[str] = []

    for key in REQUIRED_TOP:
        if key not in raw or raw[key] in (None, "", []):
            errors.append(f"missing required field: {key}")

    sid = raw.get("scenario_id", "")
    if sid and not SCENARIO_ID_RE.match(str(sid)):
        errors.append(f"scenario_id '{sid}' does not match SIM-{{PLANE}}-{{NNN}}")
    if raw.get("status") and raw["status"] not in STATUSES:
        errors.append(f"status '{raw['status']}' not in {sorted(STATUSES)}")
    if raw.get("plane") and raw["plane"] not in PLANES:
        errors.append(f"plane '{raw['plane']}' not a valid detection plane")

    dts = raw.get("detection_types") or []
    if isinstance(dts, list):
        bad = [d for d in dts if d not in DETECTION_TYPES]
        if bad:
            errors.append(f"detection_types {bad} not in {sorted(DETECTION_TYPES)}")

    # ---- cgo_anchor (optional causality-contract root) ----
    cgo_anchor = raw.get("cgo_anchor")
    if cgo_anchor is not None:
        if not isinstance(cgo_anchor, dict):
            errors.append("cgo_anchor must be a mapping {image_name, primary_username}")
        else:
            img = cgo_anchor.get("image_name")
            if not img or not isinstance(img, str):
                errors.append("cgo_anchor.image_name is required and must be a non-empty string")
            pu = cgo_anchor.get("primary_username")
            if pu is not None and not isinstance(pu, str):
                errors.append("cgo_anchor.primary_username must be a string")

    # execution_identity + default ∈ options
    ident = raw.get("execution_identity") or {}
    options = set()
    if isinstance(ident, dict):
        options = set(ident.get("options") or [])
        default = ident.get("default")
        if not default:
            errors.append("execution_identity.default missing")
        elif options and default not in options:
            errors.append(f"execution_identity.default '{default}' not in options")
        if not options:
            errors.append("execution_identity.options missing/empty")
    else:
        errors.append("execution_identity must be a mapping")

    # steps
    steps = raw.get("steps") or []
    # Build the id→index map (1-based) up front so causality.parent_step
    # earlier-ref checks can run inside the per-step loop.
    step_ids = {s.get("id") for s in steps if isinstance(s, dict)}
    step_index = {s.get("id"): i for i, s in enumerate(steps, 1) if isinstance(s, dict)}
    steps_declaring_causality = 0
    steps_missing_causality = 0
    if isinstance(steps, list):
        for i, step in enumerate(steps, 1):
            if not isinstance(step, dict):
                errors.append(f"step[{i}] is not a mapping")
                continue
            for key in REQUIRED_STEP:
                if key not in step or step[key] in (None, "", []):
                    errors.append(f"step[{i}] ({step.get('id', '?')}) missing: {key}")

            # ---- causality contract (optional, per-step) ----
            caus = step.get("causality")
            if caus is None:
                steps_missing_causality += 1
            else:
                steps_declaring_causality += 1
                if not isinstance(caus, dict):
                    errors.append(f"step[{i}] ({step.get('id','?')}) causality must be a mapping")
                else:
                    parent = caus.get("parent_step")
                    if not parent or not isinstance(parent, str):
                        errors.append(
                            f"step[{i}] ({step.get('id','?')}) causality.parent_step "
                            f"is required and must be a string"
                        )
                    else:
                        if parent == step.get("id"):
                            errors.append(
                                f"step[{i}] ({step.get('id','?')}) causality.parent_step "
                                f"is a self-reference"
                            )
                        elif parent not in step_ids or step_index.get(parent, 10**9) >= i:
                            errors.append(
                                f"step[{i}] ({step.get('id','?')}) causality.parent_step "
                                f"references unknown/forward step '{parent}'"
                            )
                    pivot = caus.get("pivot")
                    if pivot is not None and pivot not in PIVOTS:
                        errors.append(
                            f"step[{i}] ({step.get('id','?')}) causality.pivot '{pivot}' "
                            f"not in {sorted(PIVOTS)}"
                        )

            # ---- platforms (optional) ----
            platforms = step.get("platforms")
            if platforms is not None:
                if not isinstance(platforms, list):
                    errors.append(f"step[{i}] ({step.get('id','?')}) platforms must be a list")
                else:
                    badp = [p for p in platforms if p not in PLATFORMS]
                    if badp:
                        errors.append(
                            f"step[{i}] ({step.get('id','?')}) platforms {badp} not "
                            f"subset of {sorted(PLATFORMS)}"
                        )

            # ---- platform_variants (optional) ----
            pv = step.get("platform_variants")
            if pv is not None:
                if not isinstance(pv, dict):
                    errors.append(
                        f"step[{i}] ({step.get('id','?')}) platform_variants must be a mapping"
                    )
                else:
                    declared_platforms = set(platforms) if isinstance(platforms, list) else set()
                    for pkey in pv:
                        if pkey not in PLATFORMS:
                            errors.append(
                                f"step[{i}] ({step.get('id','?')}) platform_variants key "
                                f"'{pkey}' not in {sorted(PLATFORMS)}"
                            )
                        elif pkey not in declared_platforms:
                            warnings.append(
                                f"step[{i}] ({step.get('id','?')}) platform_variants has "
                                f"'{pkey}' not listed in that step's platforms"
                            )
            sid_ident = step.get("identity")
            if sid_ident and options and sid_ident not in options:
                errors.append(
                    f"step[{i}] identity '{sid_ident}' not in execution_identity.options"
                )
            for j, det in enumerate(step.get("expected_detections") or [], 1):
                if not isinstance(det, dict):
                    errors.append(f"step[{i}].expected_detections[{j}] is not a mapping")
                    continue
                for key in REQUIRED_DET:
                    if key not in det or det[key] in (None, ""):
                        errors.append(
                            f"step[{i}].expected_detections[{j}] missing: {key}"
                        )
                dtype = det.get("type")
                if dtype and dtype not in DETECTION_TYPES:
                    errors.append(
                        f"step[{i}].expected_detections[{j}] type '{dtype}' invalid"
                    )

    # ---- causality spine cross-step check (mirrors loader validate_causality_spine) ----
    # The loader only enforces "at most one root" once a STEP declares causality
    # (``if not declared: return`` — a zero-causality collection is a legacy star
    # it accepts untouched). cgo_anchor alone does NOT make a spine: a cgo_anchor
    # with 2+ steps and no per-step causality is accepted by the loader, so it
    # must only WARN here, never error — otherwise the hook blocks a loader-valid
    # edit and the warning branch below is dead.
    has_cgo = isinstance(cgo_anchor, dict)
    if steps_declaring_causality:
        if steps_missing_causality > 1:
            errors.append(
                f"more than one root step in a declared causality spine "
                f"({steps_missing_causality} steps lack causality; at most one root allowed)"
            )
    elif has_cgo and isinstance(steps, list) and len(steps) > 1:
        warnings.append(
            "cgo_anchor is declared but no step wires a causality.parent_step "
            "— the chain is not connected (steps 2..n should declare causality)"
        )

    # ---- indicators S-22..S-27 (mirror the loader) + marker-only warning ----
    step_dicts = [st for st in steps if isinstance(st, dict)] if isinstance(steps, list) else []
    indicators = raw.get("indicators")
    if indicators is not None:
        if not isinstance(indicators, list):
            errors.append("S-22: indicators must be a list")
        else:
            errors.extend(indicator_errors(indicators, {st["id"] for st in step_dicts if isinstance(st.get("id"), str)}))
            cl = raw.get("cleanup")
            cl_cmds = cl.get("commands") if isinstance(cl, dict) and isinstance(cl.get("commands"), list) else []
            errors.extend(unsafe_indicator_lines(indicators, step_dicts, cl_cmds))
    errors.extend(campaign_id_errors(step_dicts))
    for st in step_dicts:
        if is_marker_only(st.get("command")):
            warnings.append(
                f"marker-only step {st.get('id', '?')}: command only echoes/no-ops "
                f"and emits no signal a detection could key on")

    # ---- cleanup shape (ERROR — loader's CleanupSchema rejects a list) ----
    cleanup = raw.get("cleanup")
    if cleanup is not None:
        if not isinstance(cleanup, dict):
            errors.append(
                f"cleanup must be a mapping {{commands: [...]}}, not a "
                f"{type(cleanup).__name__} (a YAML list fails the loader's CleanupSchema)"
            )
        elif cleanup.get("commands") is not None and not isinstance(cleanup["commands"], list):
            errors.append("cleanup.commands must be a list of strings")

    # ---- dangling reference WARNINGS (non-fatal, matches loader) ----
    ttp_refs, adapter_refs, detection_ids = set(), set(), set()
    det_pairs: list = []  # (ttp_ref, detection_id) for slug resolution
    for step in steps if isinstance(steps, list) else []:
        if not isinstance(step, dict):
            continue
        for det in step.get("expected_detections") or []:
            if isinstance(det, dict):
                if det.get("ttp_ref"):
                    ttp_refs.add(det["ttp_ref"])
                if det.get("detection_id"):
                    detection_ids.add(det["detection_id"])
                    det_pairs.append((det.get("ttp_ref"), det["detection_id"]))
    for tool in raw.get("external_tools") or []:
        if isinstance(tool, dict) and tool.get("adapter_ref"):
            adapter_refs.add(tool["adapter_ref"])

    if ttp_refs:
        known = _index_ids(str(root / "detection_scanner" / "ttps" / "*.json"),
                            r"TTP-\d{4}-\d{4}")
        for ref in sorted(ttp_refs - known):
            warnings.append(f"dangling ttp_ref: {ref} (no card in detection_scanner/ttps/)")
    if adapter_refs:
        known = _index_ids(str(root / "tools" / "packs" / "*.yml"),
                           r"TOOL-[A-Z0-9-]+")
        for ref in sorted(adapter_refs - known):
            warnings.append(f"dangling adapter_ref: {ref} (no pack in tools/packs/)")
    if det_pairs:
        cards = _load_cards_by_id(root)
        for ttp_ref, det_id in det_pairs:
            if not ttp_ref:
                warnings.append(f"detection_id '{det_id}' has no ttp_ref to resolve against")
                continue
            card = cards.get(ttp_ref)
            if card is None:
                continue  # already flagged as a dangling ttp_ref above
            valid = _card_detection_ids(card)
            if det_id not in valid:
                warnings.append(
                    f"detection_id '{det_id}' does not resolve in card {ttp_ref} "
                    f"(the boot loader would log this as a dangling detection)"
                )

    for w in warnings:
        print(f"WARNING: {w}")
    for e in errors:
        print(f"ERROR: {e}")

    if errors:
        print(f"\n{len(errors)} schema error(s) — the boot loader would REJECT this file.")
        return 1
    print(f"OK: {sid or filepath.name} is schema-valid"
          + (f" ({len(warnings)} warning(s))" if warnings else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

# tools/packs

Tool Adapter packs — one YAML file per security tool the engine knows how to drive.

Each adapter declares the tool's integration tier, install method, invocation template, safety class, and Cortex signal mapping. The engine loads every `*.yml` here at startup, validates against the Pydantic schema in `core/tools/adapter_loader.py`, and exposes the result via `core/tools/adapter_catalog.py`.

## Reference

- Schema: [`_schema.yml`](./_schema.yml)
- Design doc: [`docs/superpowers/specs/2026-05-19-tool-adapter-framework-design.md`](../../docs/superpowers/specs/2026-05-19-tool-adapter-framework-design.md)
- Loader: [`core/tools/adapter_loader.py`](../../core/tools/adapter_loader.py)
- Catalog: [`core/tools/adapter_catalog.py`](../../core/tools/adapter_catalog.py)

## Adding a new adapter

1. Copy [`_schema.yml`](./_schema.yml) to `tools/packs/<tool>.yml` and fill in every required field.
2. Run `pytest tests/tools/ -v` — schema validation happens during the test suite.
3. If the tool is `safety_class: c2-framework` or `dual-use-lab-only`, double-check the consent-gate test in `tests/tools/test_adapter_loader.py` still covers your case.
4. If the adapter has `ttp_refs:`, ensure each referenced TTP exists under `detection_scanner/ttps/` — the startup loader warns on dangling refs.

## `install.artifact` — the payload shelf

A **tier-4** pack may declare **one** staged artifact. Without it the pack falls
back to `install.runtime_install_command`, which means **the target host fetches
the tool from the public internet at dispatch** — the first thing a customer's
default-deny egress policy blocks. A step whose tool never arrived runs anyway
and produces no detection, and the absent detection reads in a POV report as
"Cortex missed it".

```yaml
install:
  binary: /tmp/linpeas.sh
  runtime_install_command: "..."        # now OPTIONAL when artifact is present (TA-02)
  artifact:
    filename: linpeas.sh                # THE SHELF KEY — a bare filename, not a path
    url: "https://github.com/.../download/20260803-00785084/linpeas.sh"
    kind: file                          # `file` only; `archive` is REJECTED (TA-08)
    sha256: "0ea7e9ce…"                 # 64 lowercase hex
    pin: { type: release-tag, ref: "20260803-00785084" }
    license: "GPL-2.0-or-later"         # LOOK IT UP in the repo's LICENSE — never
                                        # copy it from another pack or from the
                                        # GitHub API (which returns NOASSERTION for
                                        # PEASS-ng). A staged artifact is
                                        # REDISTRIBUTED onto a host you do not own,
                                        # and this string travels into MANIFEST.json
                                        # and a POV report.
    stage_path: /tmp/linpeas.sh         # must agree with an absolute install.binary (TA-10)
    mode: "0755"
```

Seventeen validation codes, **`TA-01`..`TA-17`** (`TA-`, not `A-` —
`assertions.py` owns `A-10..A-24`). The load-bearing ones: **TA-05** makes
"pinned" impossible to claim without a digest, **TA-06** makes unpinned
impossible without a written `waiver_reason`, **TA-10** back-fills `stage_path`
from `install.binary` and rejects disagreement, **TA-12** rejects two packs
claiming one `filename` with different bytes.

## `install.artifact_exempt` — required when there is no artifact

**A tier-4 pack declares exactly one of `install.artifact` or
`install.artifact_exempt`. Declaring neither is REJECTED (TA-13).**

```yaml
install:
  runtime_install_command: "command -v hydra >/dev/null 2>&1 || apt-get install -y hydra"
  binary: hydra
  artifact_exempt:
    reason_code: DISTRO_PACKAGE         # closed vocabulary — see _schema.yml
    reason: >-                          # rendered VERBATIM to a DC. Second
      Hydra installs from the operating system's package repository. Staging
      it on the shelf would mean hosting a Debian mirror, so this tool needs
      internet access on the target host.
    revisit: null                       # REQUIRED for ARCHIVE_ONLY_NO_EXTRACTOR,
                                        # ARCHIVE_MEMBER_NOT_SELF_CONTAINED and
                                        # ARTIFACT_TOO_LARGE (TA-17); must be
                                        # ABSENT for the settled codes.
```

**Why this is a hard reject and not a warning.** Before the field, all 48
artifact-less tier-4 packs produced one byte-identical sentence in the console.
It read the same for a tool that will never be shelvable (apt) and one that is
shelvable the day an extractor exists — so a DC could not tell a **decision**
from an **omission**. A boot warning would have scrolled past in a log nobody
reads, which is exactly how 48 packs accumulated an identical
non-explanation. Rejecting makes "nobody got to it" *unrepresentable*.

Write the `reason` for the person reading it: second person, name the
consequence for the POV, no repo-internal vocabulary. **TA-16** rejects
placeholders (`TODO`/`TBD`/`N/A`/…) and anything under 40 characters — a reason
short enough to be a label is not an explanation.

Pick the code that matches the **decisive** blocker, not the first one you
notice. A scanner that downloads its rule feed at run time is
`NEEDS_RUNTIME_DATA`, not `ARTIFACT_TOO_LARGE`: staging its binary would leave
the egress dependency in place while CortexSim reported the scenario as
air-gapped, which is a false-green the shelf would manufacture itself.

After adding one, **regenerate the derived staging list in the same commit** —
`payloads/sources.json` is generated, not hand-maintained, and a stale copy fails
the drift gate:

```bash
python3 -m engine.payload_shelf --write     # regenerate
python3 -m engine.payload_shelf --check     # exit 1 + diff on drift
PAYLOAD_ALLOW_UNPINNED=0 ./scripts/build-payloads.sh
```

Full contract, error codes and the rename negative control:
[`docs/reference/payload-shelf.md`](../../docs/reference/payload-shelf.md).

## Status

**91 adapter packs ship across all 5 tiers** (`ls tools/packs/*.yml` minus
`_schema.yml`), 0 rejected at boot. Tier split: **56 tier-4**, 20 tier-3, 11
tier-5, 3 tier-1, 1 tier-2.

**8 of the 56 tier-4 packs declare an `install.artifact`** — `linpeas`, `pspy`,
`suid3num`, `lse`, `linenum`, `deepce`, `traitor`, `amicontained`, i.e. the Linux
privesc-enumerator family. **The other 48 still fetch from the public internet on
the target host at run time, and each now declares why** (`artifact_exempt`,
enforced by TA-13; 0 undeclared). That is the real headline: the shelf mechanism
is complete, its coverage is not.

Triaged 2026-08-06 against measured evidence: 18 `DISTRO_PACKAGE`, 12
`LANGUAGE_PACKAGE_MANAGER`, 7 `SOURCE_TREE_REQUIRED`, 6
`ARCHIVE_ONLY_NO_EXTRACTOR`, 2 `NEEDS_RUNTIME_DATA`, and one each of
`LICENCE_NO_REDISTRIBUTION`, `ARCHIVE_MEMBER_NOT_SELF_CONTAINED` and
`ARTIFACT_TOO_LARGE`. **Zero were converted, deliberately: not one of the 48 is
a bare single file at a pinned URL** — the entire remaining opportunity is
archive-shaped, and `kind: archive` is rejected because no consumer can unpack
one. **40 of the 48 are settled forever** — they would need someone else's
package index hosted — and the real backlog is the **8 adapters** carrying a
`revisit` line. (40 + 8 = 48; count them with the snippet under
"[Counting](#counting--the-command-wins-over-this-prose)" below, never from prose.)
Full triage, including the two dead upstream URLs found by probing every pinned
URL in this directory:
[`docs/reference/payload-shelf.md` §11](../../docs/reference/payload-shelf.md)
and [`adapter-catalog.md` §9.2](../../docs/reference/adapter-catalog.md).

The canonical doc with the full shipped-vs-pending breakdown is
[`docs/tool-adapters.md`](../../docs/tool-adapters.md); the verified inventory is
in [`docs/reference/adapter-catalog.md`](../../docs/reference/adapter-catalog.md).

## Counting — the command wins over this prose

Every number above drifts. These are the counted ground truth; when they and the
prose disagree, the command is right and the prose is a bug.

```bash
docker run --rm -v "$PWD:/app" -w /app -e CORTEXSIM_BASE_DIR=/app cortexsim:dev \
  python -c "
import sys, collections; sys.path.insert(0,'core')
from tools.adapter_loader import load_adapters
ads = list(load_adapters('/app/tools/packs').all())
t4 = [a for a in ads if a.tier == 4]
ex = [a for a in t4 if a.install.artifact_exempt]
rev = [a for a in ex if a.install.artifact_exempt.revisit]
print('packs', len(ads), 'per-tier', dict(sorted(collections.Counter(a.tier for a in ads).items())))
print('tier4', len(t4), 'staged', len([a for a in t4 if a.install.artifact]),
      'exempt', len(ex), 'undeclared', len(t4) - len(ex) - len([a for a in t4 if a.install.artifact]))
print('backlog', len(rev), 'settled', len(ex) - len(rev))
print(dict(collections.Counter(a.install.artifact_exempt.reason_code for a in ex)))
"
```

Verified 2026-08-06: `packs 91 · per-tier {1:3, 2:1, 3:20, 4:56, 5:11} ·
tier4 56 staged 8 exempt 48 undeclared 0 · backlog 8 settled 40`.

Do **not** count with `grep -c` over `tools/packs/*.yml` — `_schema.yml` is a
documented template that carries a `tier:` line and an example `artifact_exempt`
block, so every naive grep is off by exactly one in both directions.

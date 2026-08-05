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

Twelve validation codes, **`TA-01`..`TA-12`** (`TA-`, not `A-` — `assertions.py`
owns `A-10..A-24`). The load-bearing ones: **TA-05** makes "pinned" impossible to
claim without a digest, **TA-06** makes unpinned impossible without a written
`waiver_reason`, **TA-10** back-fills `stage_path` from `install.binary` and
rejects disagreement, **TA-12** rejects two packs claiming one `filename` with
different bytes.

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
the target host at run time.** That is the real headline: the shelf mechanism is
complete, its coverage is not. The unconverted families and why are enumerated in
[`docs/reference/adapter-catalog.md`](../../docs/reference/adapter-catalog.md)
§9.2 (~20 apt/yum, ~15 pip/gem/go/cargo, ~8 git-clone trees, ~5 release archives
that TA-08 rejects, plus kubescape at 262 MB against a 64 MiB cap).

The canonical doc with the full shipped-vs-pending breakdown is
[`docs/tool-adapters.md`](../../docs/tool-adapters.md); the verified inventory is
in [`docs/reference/adapter-catalog.md`](../../docs/reference/adapter-catalog.md).

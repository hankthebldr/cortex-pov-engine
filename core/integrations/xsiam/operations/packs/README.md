# XSIAM operation packs

Declarative catalog of Cortex XSIAM Platform API operations. One YAML file per API
**category**; each file is a mapping with a `category` and an `operations:` list. Loaded at
boot by `core/integrations/xsiam/operations/loader.py` into the singleton catalog and exposed
via `GET /api/xsiam/operations`. Files whose name starts with `_` are ignored by the loader.

## Pack shape

```yaml
category: "Correlation Rules"          # applied to every op in the file
operations:
  - op_id: OP-CORRELATIONS-GET         # ^OP-[A-Z0-9-]+$, unique across all packs
    name: "Get Correlation Rules"
    method: POST                       # GET | POST
    path: /public_api/v1/correlations/get   # must start with /public_api/
    access_class: read                 # read | write | destructive  → drives the gate
    consent: none                      # optional; defaults from access_class
    doc_link: https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/...
    tags: []                           # optional; `confirm-tail` = not in the source CSV
```

## Field notes

- **`access_class`** is the safety gate. `read` runs live; `write`/`destructive` are dry-run by
  default and require a global flag (`CORTEXSIM_XSIAM_ALLOW_WRITE` / `..._DESTRUCTIVE`) **plus**
  per-request consent (`write_authorized` / `destructive_authorized`) to send.
- **`path_params`** are **derived automatically** from `{token}`s in `path` — do not hand-list
  them (the schema validates any you provide match the path).
- **`path_param_allowlist`** (optional) restricts a path param to a closed set,
  `{param: [value, ...]}`. The executor refuses any other value before a tenant client is built,
  for a dry run too. Use it when a free-form param would let a read reach any object of its kind
  (`OP-SOCFW-LIST-GET` reads only the SOC Framework's two config lists, not any XSOAR list).
- **`consent`** defaults to `none`/`write`/`destructive` from `access_class`; only set it to
  override.
- Invalid rows are **rejected and logged** at boot, never fatal (one bad row won't sink a file).
- **Tags with behaviour.** `no-request-data-wrapper` makes the executor send `{}` instead of
  `{"request_data": {}}` for a bodyless POST (BYOS ingest rejects the wrapper). `zip-reply` marks
  an op whose tenant reply is a ZIP archive, not JSON (`OP-PLAYBOOKS-GET`, `OP-SCRIPTS-GET`): the
  executor reads it with `XsiamClient.request_archive` (identity only, streamed, refused past
  4 MiB) and returns
  `integrations/xsiam/archives.py::zip_members` (members read in memory, never extracted, under
  member and byte caps; only stored or deflated members are opened) as the reply. Every other
  tag is documentation only.
- **Provenance tags.** `confirm-tail` = not in the source CSV. `cortex-bot-observed` = harvested
  from a field tool whose author ran it against a live tenant (an observation, not a CortexSim
  verification). `prefix-inferred` = the full path was composed from that tool's default
  `/public_api/v1` prefix, not copied as a literal. `povkit-observed` = taken from povkit's
  tenant client (an observation, not a CortexSim verification). `path-unverified` = no documented
  or observed source.
- After editing packs, regenerate the doc mirror:
  `python3 scripts/gen_xsiam_operation_catalog.py` (dry run), then `--write`.

The human-readable mirror of this catalog is
`docs/reference/xsiam-platform-apis/14-operation-catalog.md`.

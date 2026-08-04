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
- **`consent`** defaults to `none`/`write`/`destructive` from `access_class`; only set it to
  override.
- Invalid rows are **rejected and logged** at boot, never fatal (one bad row won't sink a file).

The human-readable mirror of this catalog is
`docs/reference/xsiam-platform-apis/14-operation-catalog.md`.

# The K8s payload shelf

Staged tool artifacts that SimCore serves to pods in the **K8s delivery mode**.
This is the on-disk half of `core/api/payloads.py`; the contract it implements
is `docs/reference/k8s-delivery.md` §5.

For a cloud-native target **the deployment is the agent** — no beacon, no binary
on a customer host. `kubectl apply` is the delivery, the kubelet pulls the image,
and an init container pulls the payload from here.

## Filling it

```bash
./scripts/build-payloads.sh                        # stage everything in sources.json
PAYLOAD_OFFLINE=1 ./scripts/build-payloads.sh      # no network → empty shelf, exit 0
PAYLOAD_ALLOW_UNPINNED=0 ./scripts/build-payloads.sh   # CI: refuse an unpinned artifact
OUT_DIR=/srv/shelf ./scripts/build-payloads.sh     # stage elsewhere
```

Run it **before `docker build`** — `core/Dockerfile` COPYs this directory into
the image at `/app/payloads`. On a running host, `CORTEXSIM_PAYLOAD_DIST` points
the endpoints at an scp'd drop with no rebuild at all.

An **empty shelf is a valid state**. Nothing breaks; a scenario that declares
`cluster_posture.payloads` naming an artifact that is not here gets a `409
PAYLOAD_NOT_STAGED` from `GET /api/k8s/bootstrap/{id}` instead of a pod that
runs the attack without its tooling.

## What is served

| endpoint | returns |
|---|---|
| `GET /api/k8s/payloads` | inventory + `dist_dir`; the pod's **reachability probe**. Always unauthenticated. |
| `GET /api/k8s/payload/{name}` | the bytes + `X-CortexSim-Payload-SHA256` |
| `GET /api/k8s/payload/{name}/sha256` | bare hex + `\n` |
| `GET /api/k8s/bootstrap/{scenario_id}` | the generated in-pod payload + `X-CortexSim-Bootstrap-SHA256` |
| `GET /api/k8s/bootstrap/{scenario_id}/sha256` | bare hex + `\n` |
| `GET /api/k8s/posture-findings` | the derived posture-finding vocabulary |

## Integrity — where the anchor actually is

The `/sha256` endpoints are **for humans and for `curl`**. A pod must not use
them: a digest fetched from the same server that served the file proves nothing,
because a substituted server serves a matching pair. The expected digests are
baked into the manifest at generation time on the DC's own SimCore and travel
with the file in the `integrity.env` ConfigMap. The **manifest is the
out-of-band anchor**.

This is a deliberate divergence from the agent installer (`core/api/agents.py`),
which does fetch its digest because it has no such anchor. Do not "unify" them.

## Authentication — read this before a customer engagement

**Default is `open`: these endpoints require no credential.**

That is a considered default, not an oversight. The manifest's fetch script is a
plain `wget` with no `Authorization` header, so requiring a token today would
403 every served manifest this engine emits — a manufactured failure, which is
the outcome class this whole design exists to prevent.

The consequence has to be said plainly:

> **The generated manifest is a file you hand a customer.** It names your
> SimCore URL. It gets committed to a GitOps repo, pasted into a ticket and
> forwarded. With auth open, anything that can reach that URL can pull the
> staged offensive tooling off this shelf.

So: put SimCore on a network the customer's cluster can reach and a stranger
cannot, and keep the shelf to what the engagement actually needs. SimCore logs a
WARN at boot naming the directory and the artifact count whenever the shelf is
non-empty and auth is open.

The gate is built and tested for the moment the manifest can carry a header:

```bash
CORTEXSIM_K8S_PAYLOAD_AUTH=token
CORTEXSIM_K8S_PAYLOAD_TOKENS=cxp_first,cxp_second   # comma-separated
```

It covers `/payload/{name}` and `/payload/{name}/sha256` only. `/payloads` stays
open by contract — it is the reachability probe, and a probe that can fail for
two different reasons is a debugging trap. Setting `token` with an empty token
list fails **closed** with `PAYLOAD_AUTH_MISCONFIGURED`, never open.

## Licensing

Staged artifacts are third-party (GPL-3.0, MIT, …) and are **not vendored** —
`.gitignore` keeps them out of git history. Provenance travels with the artifact
instead: `source_url` and `license` flow from `sources.json` into
`MANIFEST.json`, out through the inventory endpoint, and into the manifest
banner. `MANIFEST.json` is provenance only; the endpoints always recompute
digests from the bytes on disk, so a stale or hand-edited manifest cannot make a
tampered file verify.

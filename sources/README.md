# sources

Git submodules — do not edit source files here. The Tool Instantiation Layer manages these.

All directories under `sources/` are git submodules defined in `.gitmodules`. They are
initialized by `install.sh` via `git submodule update --init --recursive`.

The Tool Instantiation Layer (`core/tools/instantiator.py`) is responsible for building
and running these tools. SimCore calls their native binaries and scripts directly — there
are no wrappers around external tool behavior.

To add a new submodule: update `.gitmodules`, add an entry to `core/tools/registry.py`,
and add the corresponding build/run logic to `install.sh`.

| Directory | Repo | Language | Role |
|---|---|---|---|
| signalbench | gocortexio/signalbench | Rust | MITRE-mapped endpoint telemetry generator |
| gocortexbrokenbank | gocortexio/gocortexbrokenbank | Python | Vulnerable CI/CD app for Cloud App scenarios |
| mocktaxii | gocortexio/mocktaxii | Python | STIX/TAXII 2.1 server for NDR scenarios |
| gcgit | gocortexio/gcgit | Rust | XSIAM REST / Git bridge |
| xdrtop | gocortexio/xdrtop | Rust | Terminal live XSIAM monitor |
| ackbarx | gocortexio/ackbarx | Rust | SNMP trap forwarder to XSIAM HTTP |
| CDR | hankthebldr/CDR | YAML/Shell | Kubernetes CDR scenario baseline |
| xsiam-prisma-cdr-lab | hankthebldr/xsiam-prisma-cdr-lab | Shell | Attack scenario shell library (branch 1.1) |
| atomic-red-team | redcanaryco/atomic-red-team | YAML/Shell | Atomic TTP library |

## Retired

| Directory | Repo | Retired | Why |
|---|---|---|---|
| MITRE-Turla-Carbon | Palo-Cortex/MITRE-Turla-Carbon | 2026-11-10 | Nothing in the engine read it. `install.sh` still fetched it, and with it `files/turla/Resources/Binaries/binaries.zip`, a password-zipped archive of compiled Turla emulation implants (EPIC, Carbon, keylogger, Mimikatz, Snake, LightNeuron), onto every jumpbox it bootstrapped. |

`scripts/check-installer-submodules.py` (in `make check-adapters` and the CI `adapters`
job) fails if `install.sh` would check out a submodule on its denylist, or any checked-out
submodule that ships a `Binaries/*.zip`. Before you register a submodule, check what it ships
and whether the engine reads it.

A clone that initialised the Turla submodule before 2026-11-10 keeps the files on disk after
`git pull`, because git leaves a populated submodule directory in place. `git submodule deinit`
no longer works once the gitlink is gone. Remove the files directly, from the repo root:

```bash
rm -rf sources/MITRE-Turla-Carbon .git/modules/sources/MITRE-Turla-Carbon
git config --remove-section submodule.sources/MITRE-Turla-Carbon
```

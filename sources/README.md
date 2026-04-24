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
| MITRE-Turla-Carbon | Palo-Cortex/MITRE-Turla-Carbon | C++ | MITRE Turla Carbon campaign reference |
| atomic-red-team | redcanaryco/atomic-red-team | YAML/Shell | Atomic TTP library |

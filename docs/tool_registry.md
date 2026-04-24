# Tool Registry

Maps external tools used by CortexSim scenarios to their associated MITRE ATT&CK techniques
and detection planes. Tools marked `install_inline: true` are downloaded at runtime inside
the scenario execution context. Tools marked `pre-installed` must be available on the target.

| Tool | Source | Type | TTPs | Detection Planes | Install Method |
|---|---|---|---|---|---|
| deepce | github.com/stealthcopter/deepce | script | T1613, T1082, T1611 | CDR | inline (curl) |
| linpeas | github.com/carlospolop/PEASS-ng | script | T1082, T1083, T1552 | CDR, EDR | inline (curl) |
| xmrig | github.com/xmrig/xmrig | binary | T1496, T1105, T1053.005 | CDR | inline (curl + tar) |
| nsenter | system (util-linux) | binary | T1611 | CDR | pre-installed |
| kubectl | kubernetes.io/docs/tasks/tools | binary | T1613, T1021.001, T1053.005, T1552.001 | CDR | pre-installed |
| signalbench | github.com/gocortexio/signalbench | binary | T1059, T1003, T1055 | EDR | submodule (cargo build) |
| mocktaxii | github.com/gocortexio/mocktaxii | service | — (TIM feed) | NDR | submodule (pip install) |
| ackbarx | github.com/gocortexio/ackbarx | service | T1095 | NDR | submodule (cargo build) |
| xdrtop | github.com/gocortexio/xdrtop | binary | — (monitor) | all | submodule (cargo build) |
| gocortexbrokenbank | github.com/gocortexio/gocortexbrokenbank | service | T1190, T1059.007 | CLOUD_APP | submodule (pip install) |
| wildfire-testfile | wicar.org / wildfire.paloaltonetworks.com | script | T1105, T1486 | CDR | inline (curl) |

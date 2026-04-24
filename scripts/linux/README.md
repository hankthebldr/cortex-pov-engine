# scripts/linux

Generated bash simulation bundles land here. Created by push_generator.py for Linux targets.

Each bundle is a self-contained bash script that can execute a CortexSim scenario on a clean
Ubuntu 22.04 target with no SimCore dependency at runtime. Bundles include the identity
harness setup, dependency checks, ordered TTP execution steps, and cleanup/teardown.

Files are named: `{scenario_id}-{timestamp}.sh`

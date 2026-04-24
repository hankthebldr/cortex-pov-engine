# scenarios/multi_plane

Multi-plane scenarios spanning 2+ detection engines (stitching validation).

These scenarios are designed to validate Cortex XSIAM's alert stitching and grouping
capabilities by generating coordinated signals across multiple detection planes in a
single kill chain. For example: CDR container escape followed by EDR lateral movement,
or NDR C2 traffic correlated with ITDR credential theft.

The Analytics engine correlation rules are the primary validation target for these scenarios.

Use case prefix: UCS-MP-xx

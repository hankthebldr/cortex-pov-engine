---
packet: none (lab probes on the personal Mac's lab VM, not a packet reply)
answered: 2026-11-11
answered_by: Claude Code session, personal Mac, lab VM pov-target-01 read back through XQL
source: personal-mac
---

Every source below is our own lab: one Linux VM (Ubuntu 24.04, kernel 6.8, systemd 255)
running Cortex XDR agent 9.4.0.470, driven by the CortexSim beacon as root, read back from
the lab tenant's `xdr_data` through SimCore's read-only XQL route. Four probe runs on
2026-11-11: 19c7a04d (token pmqfba025: CGO, argv, identity and cron matrix, first length
ladder), fe20956a (shipped cgo_anchor path), 31b71c6a (token pmq962e8a: second length
ladder, 8 s gaps) and cc70660c (shipped cgo_anchor path with 8 s gaps). One host, one
agent version, one distribution: every rule below is provisional until repeated on
another distribution and agent version.

```yaml
answers:
  - id: PM-CAU-01
    status: partial
    answer: >-
      On this host the CGO of every chain that starts in a systemd service is a
      degenerate record of systemd's executor: image name `9`, image path
      `/proc/self/fd/9`, the SHA-256 of empty input, command line `systemd-executor
      --deserialize N --log-target journal-or-kmsg`, and no user. It held for the
      beacon's own steps, for a renamed binary run directly as a transient unit's
      ExecStart, for a transient unit whose ExecStart is `sh -c`, and for a unit run
      as www-data. Neither the beacon nor an `sh -c` wrapper nor a renamed ExecStart
      became the CGO. Each transient unit was a separate chain with its own
      causality_actor_causality_id; all of the beacon's steps shared one chain. A
      chain launched by cron had CGO `cron`. An earlier lab session saw CGO
      `cortexsim-agent` for a beacon started before the agent was installed
      (PM-CAU-08).
    rule: >-
      On Linux hosts with systemd 255, do not key a detection on
      causality_actor_process_image_name, path or hash for any chain the beacon or a
      systemd unit starts; key on actor_process_* or action_process_*. A cgo_anchor
      is a label, not something the tenant will show.
    sources:
      - kind: lab_observation
        title: CGO matrix, probe run 19c7a04d (token pmqfba025)
        ref: Lab VM pov-target-01, XDR agent 9.4.0.470, Ubuntu 24.04, systemd 255; cases p0, c1a, c1b, c1c, id1; xdr_data PROCESS_START read via XQL
        date: 2026-11-11
        distribution: public
    follow_ups:
      - >-
        Repeat on Ubuntu 22.04 (systemd 249) with the same agent: is the `9` CGO
        specific to systemd 255's executor?

  - id: PM-CAU-02
    status: answered
    answer: >-
      action_process_image_name comes from the executable, not argv[0]. `bash -c
      "exec -a apache2 /bin/sh -c …"` was recorded as image `sh` with a command line
      that starts `apache2`. A renamed copy of the shell (`cp /usr/bin/dash
      /opt/cxprobe/apache2`) was recorded as image `apache2`, and its children as
      actor_process_image_name `apache2`. Neither changed the CGO.
    rule: >-
      To make a process appear under a service's image name, execute a renamed copy
      of a binary; an argv[0] rewrite changes only the command line. Neither
      relabels the CGO on a systemd 255 host.
    sources:
      - kind: lab_observation
        title: argv A/B and renamed-binary cases, probe run 19c7a04d (token pmqfba025)
        ref: Lab VM pov-target-01, XDR agent 9.4.0.470; cases c2a, c2b, c2c; xdr_data PROCESS_START
        date: 2026-11-11
        distribution: public

  - id: PM-CAU-03
    status: partial
    answer: >-
      `runuser -u www-data -- …` was recorded as its own process node (image
      `runuser`, user root) and stayed inside the beacon's chain (same
      causality_actor_causality_id). Its child ran as `pov-target-01\www-data`. The
      CGO's user did not change; it was null throughout. sudo, su and setpriv were
      not tested, nor `runuser -l`, which the harness uses.
    rule: >-
      A privilege wrapper continues the chain and changes the user of the processes
      below it only. Expect the wrapper as the parent of the step's first process.
    sources:
      - kind: lab_observation
        title: Identity cases, probe run 19c7a04d (token pmqfba025)
        ref: Lab VM pov-target-01, XDR agent 9.4.0.470; cases id2, id3; xdr_data PROCESS_START
        date: 2026-11-11
        distribution: public
    follow_ups:
      - >-
        Repeat with `runuser -l`, `sudo -u` and `su -s /bin/bash` (the harness's three
        modes) and record whether each is a node and which user its child carries.

  - id: PM-CAU-05
    status: partial
    answer: >-
      For cron, the scheduler is the CGO: a payload started by `* * * * * root bash -c
      /opt/cxprobe/c1c.sh` had CGO `cron` (`/usr/sbin/cron -f -P`). The script's own
      process was recorded under the script's file name, `c1c.sh`. Units started by
      systemd, including transient ones, have the `9` executor CGO (PM-CAU-01).
      Scheduled tasks on Windows were not tested.
    rule: >-
      A cron-persisted payload does not own its chain; cron does. Persistence
      detections keyed on the CGO must name cron.
    sources:
      - kind: lab_observation
        title: Cron case, probe run 19c7a04d (token pmqfba025)
        ref: Lab VM pov-target-01, XDR agent 9.4.0.470; case c1c; xdr_data PROCESS_START
        date: 2026-11-11
        distribution: public
  - id: PM-TEN-02
    status: partial
    answer: >-
      Command-line capture is all or nothing at a length limit. Two ladders of `sh -c`
      processes carried a token at the head, middle and tail of the argument. Full
      command lines of 138, 266, 310, 394, 522, 650, 2059 and 3083 characters were
      recorded whole, all three tokens intact. Command lines of 4107 characters and
      longer (arguments of 4096, 6144, 8192, 16384 and 32768) were recorded as
      argv[0] alone, `/bin/sh`: every token lost, nothing truncated. The limit lies
      between 3083 and 4107 characters; a 4096-byte limit fits, but that is our
      inference. Agent profile settings were not varied, and Windows was not tested.
    rule: >-
      Keep each step's whole command line under 3000 characters on Linux. A longer one
      reaches the tenant with no arguments at all, so stage the payload as a file and
      run the file.
    sources:
      - kind: lab_observation
        title: Command-line length ladders, probe runs 19c7a04d (pmqfba025) and 31b71c6a (pmq962e8a)
        ref: Lab VM pov-target-01, XDR agent 9.4.0.470, Ubuntu 24.04; action_process_image_command_line of each ladder child in xdr_data
        date: 2026-11-11
        distribution: public
    follow_ups:
      - >-
        Bisect 3100 to 4100 characters to fix the edge, and repeat on a Windows agent
        for powershell.exe and cmd.exe.

  - id: PM-CAU-04
    status: partial
    answer: >-
      Process events are lost in whole windows, not only for identical rapid starts.
      In two runs whose commands were all distinct and 5 s or 8 s apart, the agent
      recorded no process event of any kind for about 15 s (19c7a04d, 23:21:20 to
      23:21:30 UTC) and about 24 s (31b71c6a, 23:30:25 to 23:30:41), including short
      `head`, `tr` and `sleep` processes, while events on both sides of each window
      were recorded and later host events arrived normally. In the beacon's chained
      cgo_anchor mode the anchor shell itself was recorded both times, but of its
      children only the first one (a `sleep 8`, 8 s into the step) was recorded in the
      slower run, and none in the faster one; the beacon reported every step as run to
      completion with the expected durations. The cause is not established.
    rule: >-
      Pacing alone does not guarantee a step is recorded. A step with no recorded
      process events is evidence of a collection gap, not of a detection miss.
    sources:
      - kind: lab_observation
        title: Loss windows in probe runs 19c7a04d and 31b71c6a; anchor runs fe20956a and cc70660c
        ref: Lab VM pov-target-01, XDR agent 9.4.0.470, Ubuntu 24.04; per-minute xdr_data counts and per-step process events read via XQL, read more than 8 minutes after each run
        date: 2026-11-11
        distribution: public
    follow_ups:
      - >-
        Does the agent rate-limit or batch-drop process events per parent or per host,
        and does the endpoint record or an agent log say when it does?

  - id: PM-IDN-01
    status: partial
    answer: >-
      On process events the user fields split by scope: the process run under
      `runuser -u www-data` or `systemd-run --uid=www-data` carried
      action_process_username `pov-target-01\www-data`, its children carried
      actor_effective_username `pov-target-01\www-data`, and the CGO's
      causality_actor_effective_username was null in every case. Which of these an
      issue and the UserName grouping artifact use was not tested: no issue was
      raised.
    rule: >-
      A Linux service-account step attributes the user on the step's own processes
      only; expect no user on the chain's owner.
    sources:
      - kind: lab_observation
        title: Identity cases, probe run 19c7a04d (token pmqfba025)
        ref: Lab VM pov-target-01, XDR agent 9.4.0.470; cases id1, id2, id3; xdr_data PROCESS_START
        date: 2026-11-11
        distribution: public
    follow_ups:
      - >-
        Raise one Medium custom BIOC on a www-data child and read the issue's user
        fields and the case it joins.
```

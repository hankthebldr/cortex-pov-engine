/**
 * The detection-object library behind every Explain drill-down.
 *
 * SEED CATALOG — carried over verbatim from the design prototype
 * (`POVengine Console.dc.html`, Claude Design project "Cortex POV Engine UI
 * Redesign"; see docs/design/DESIGN-SYNC.md).
 *
 * These are the authored corpus values the design was drawn against. Where
 * SimCore serves the same shape over the API, the surface prefers the API and
 * falls back to this; where it does not serve it yet, this IS the model, and
 * its shape is what the endpoint should eventually return. Do not "improve"
 * values here to make a screen look better — they are the numbers the design
 * conversation settled on, and several of them (12 components / 4 ready / 6
 * partial / 2 missing, 21 of 34 stream shapes, 6 of 9 datasets readable) are
 * quoted on more than one surface precisely so those surfaces cannot disagree.
 * Every tally is DERIVED from these rows, never restated as a literal.
 */

export function detectionLib() {
  return {
    'bioc-edr-001-memory-read-service-acct': {
      type: 'BIOC', door: 'AGT', plane: 'EDR', mitre: 'T1003.001',
      state: 'awaiting', severity: 'high',
      source: 'rule bioc-edr-001-memory-read-service-acct\n  dataset = xdr_data\n  event_type = PROCESS\n  and action_process_image_name in ("gcore", "gdb", "procdump.exe")\n  and action_process_arguments contains_any ("cortex-broker", "lsass")\n  and actor_effective_username != action_process_owner\nseverity high',
      keys: ['action_process_image_name', 'action_process_arguments', 'actor_effective_username', 'causality_actor_process_image_name'],
      why: 'A debugger taking a core of a credential-bearing process is the behaviour, not the tool. The rule keys on the target argument rather than the binary name, so renaming gcore does not evade it — and it requires the acting user to differ from the process owner, which is what separates an operator debugging their own service from theft.',
      guard: 'Excludes the case where the acting user owns the target process, which is how a legitimate crash dump looks. Without that clause this rule fires on every developer core dump in the estate.',
      control: 'Negative control: take a core of a process holding no credential material. The rule must stay silent, and in the last control run it did.',
    },
    'abioc-edr-001-svc-acct-anomaly': {
      type: 'ABIOC', door: 'ENG', plane: 'EDR', mitre: 'T1003.001',
      state: 'awaiting', severity: 'high',
      source: 'analytics abioc-edr-001-svc-acct-anomaly\n  profile actor_effective_username over 14d\n  features [ process_spawn_rate, distinct_target_processes, debug_syscall_count ]\n  score when debug_syscall_count > p99(profile)\n  and actor is service_account\n  require baseline_days >= 7',
      keys: ['debug_syscall_count', 'actor_effective_username', 'process_spawn_rate'],
      why: 'A service account has a narrow, repetitive behavioural envelope, which is exactly what makes anomaly scoring work on it. A debug syscall from an identity that has never issued one in fourteen days is a strong signal precisely because the baseline is so tight.',
      guard: 'Refuses to score until seven days of baseline exist, so a freshly onboarded tenant cannot produce a cold-start false positive that reads as a detection.',
      control: 'This tenant holds baseline since 11 Sep, so the requirement is satisfied and the score is trustworthy.',
    },
    'xql-edr-001-enum-tokens': {
      type: 'XQL', door: 'AGT', plane: 'ITDR', mitre: 'T1087.001',
      state: 'verified', severity: 'medium',
      source: 'preset = xdr_process\n| filter action_process_image_name in ("getent","find","ls")\n| filter action_process_arguments contains_any (".aws",".kube","*.token")\n| fields _time, agent_hostname, actor_effective_username,\n         action_process_image_command_line\n| comp count() as hits by actor_effective_username, bin(_time, 1m)\n| filter hits >= 5',
      keys: ['action_process_arguments', 'actor_effective_username', '_time'],
      why: 'No single command here is suspicious. The query scores the burst: five or more credential-adjacent reads by one identity inside a minute. That shape is what an operator does once and an enumeration script does continuously.',
      guard: 'The one-minute bin and the count floor are the guard. Drop either and this becomes the noisiest query in the tenant.',
      control: 'Returned 2 rows against the tenant, both inside the run window, which is how this claim graduated from authored to verified.',
    },
    'corr-edr-001-cross-plane-stitch': {
      type: 'Correlation', door: 'ENG', plane: 'ANALYTICS', mitre: 'T1078.004',
      state: 'not present', severity: 'critical',
      source: 'correlation corr-edr-001-cross-plane-stitch\n  first  alert.source = "BIOC" and alert.name ~ "memory-read"\n  then   dataset = cloud_audit_logs\n         and event_name = "AssumeRole"\n         and source_ip not_in known_ranges(actor)\n  within 30m\n  join on extracted_key_id\n  emit incident severity critical',
      keys: ['extracted_key_id', 'event_name', 'source_ip', 'alert.name'],
      why: 'This is the only object in the chain that proves a story rather than an event. It joins the endpoint theft to the cloud use through the key id itself, which is why a thirty-minute window is tight enough to mean something and wide enough to survive a slow operator.',
      guard: 'Requires the join key to match, so two unrelated events in the same window cannot be stitched into a false narrative.',
      control: 'Installed in this tenant but disabled, so it cannot fire. Reported NOT PRESENT — the distinction from MISSED is the whole point.',
    },
    'xql-cdr-014-bucket-exfil': {
      type: 'XQL', door: 'CC', plane: 'CDR', mitre: 'T1567.002',
      state: 'awaiting', severity: 'high',
      source: 'dataset = cloud_audit_logs\n| filter event_name in ("GetObject","ListObjects")\n| comp count() as objs, sum(bytes_out) as vol\n  by user_identity_arn, bucket, bin(_time, 5m)\n| filter objs > 200 and vol > 50000000',
      keys: ['event_name', 'user_identity_arn', 'bucket', 'bytes_out'],
      why: 'Object reads are the most common event in a cloud audit log, so volume alone is worthless. The query keys on rate per identity per bucket, which is the shape of a sync rather than a read.',
      guard: 'Both thresholds must trip. A large single object and a chatty small-object service each stay below one of them.',
      control: 'Not yet re-asked of the tenant. Awaiting validation rather than assumed.',
    },
    'bioc-ndr-004-dns-entropy': {
      type: 'BIOC', door: 'BVM', plane: 'NDR', mitre: 'T1071.004',
      state: 'not present', severity: 'medium',
      source: 'rule bioc-ndr-004-dns-entropy\n  dataset = dns_story\n  and query_type = "TXT"\n  and shannon_entropy(subdomain) > 3.5\n  and distinct_subdomains_per_domain(5m) > 50\n  exclude domain in allowlist("cdn", "telemetry")',
      keys: ['query_type', 'subdomain', 'query_name'],
      why: 'Tunnelled data looks like noise because it is encoded, and encoding raises entropy. Pairing entropy with subdomain cardinality separates a tunnel from one oddly named record.',
      guard: 'Allowlists CDN and telemetry domains, which legitimately generate high-entropy subdomains all day.',
      control: 'Ships in a content pack this tenant has not taken, so it is reported NOT PRESENT.',
    },
  }
}

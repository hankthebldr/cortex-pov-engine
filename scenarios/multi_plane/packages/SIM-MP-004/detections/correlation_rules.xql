// SIM-MP-004 — XSIAM stitching correlation
// Goal: group the endpoint credential-access event with the subsequent CloudTrail
// sts:GetCallerIdentity + multi-service enumeration into a single incident story.
//
// Grouping key: AWS principal ARN
// Time window: 300 seconds from first endpoint signal
// Dedup: one incident per (hostname, aws_principal) pair per 1h

// ── Rule 1: Endpoint credential discovery → cloud pivot stitch ─────────────────
dataset = xdr_data
| alter src = coalesce(agent_hostname, action_process_image_name)
| filter (event_type = PROCESS and action_process_command_line contains "AKIA")
      or (event_type = PROCESS and action_process_image_name = "aws"
          and actor_effective_username in ("www-data", "nobody", "nginx"))
| join type=inner (
    dataset = cloud_audit_logs
    | filter event_name = "GetCallerIdentity"
    | filter user_identity_type = "IAMUser"
    | filter source_ip_address != "corp_egress_range"
    | fields event_name, user_identity_arn, source_ip_address, event_time
  ) as cdr
  on src == cdr.user_identity_arn within 300 seconds
| fields agent_hostname, actor_effective_username, cdr.user_identity_arn,
         cdr.source_ip_address, event_timestamp, cdr.event_time
| alter incident_key = concat(agent_hostname, "::", cdr.user_identity_arn)

// ── Rule 2: Multi-service enumeration burst from single principal ──────────────
// Detects T1580 — >= 3 distinct AWS service list/describe calls within 30s by
// same principal. CDR UEBA fallback signal.
dataset = cloud_audit_logs
| filter event_name in ("DescribeInstances", "ListUsers", "ListBuckets",
                        "ListRoles", "DescribeSecurityGroups", "GetAccountSummary")
| comp count_distinct(event_name) as service_fanout,
       values(event_name) as services_touched
  by user_identity_arn, bin(event_time, 30s)
| filter service_fanout >= 3

// ── Rule 3: Cross-account S3 copy with sensitivity-tagged source ───────────────
// Detects T1537 — s3:CopyObject or PutObject where source bucket tagged
// Sensitivity=High AND destination bucket outside owner account.
dataset = cloud_audit_logs
| filter event_name in ("CopyObject", "PutObject")
| filter request_parameters.bucket_tags contains "Sensitivity=High"
| filter response_elements.destination_account != request_parameters.source_account
| alter alert_name = "SIM-MP-004: Cross-account exfil of sensitivity-tagged data"
| fields user_identity_arn, request_parameters.source_bucket,
         response_elements.destination_bucket, event_time

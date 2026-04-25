-- Correlation Rule: Lab Exfil via SMB and Cloud
-- Sources: DS-01 + DS-02 (EAL) + DS-07
-- Window: 60 minutes
config timeframe = 60m, groupby = agent_hostname
| dataset = xdr_data
| filter event_type in (PROCESS, AUTH, NETWORK)
| filter (action_process_image_name = "smbclient") and (app_id = "smb" or app_id = "msrpc")
| fields agent_hostname, actor_effective_username, action_process_image_name, event_timestamp

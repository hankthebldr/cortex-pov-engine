#!/usr/bin/env python3
"""Build out MATURE endpoint scenarios across the offensive toolchain: each deploys a
real tool via adapter_ref (+ payload shelf for staged tools), modeled on edr-022/TTP-0176.
Reads each tools/packs/<tool>.yml for adapter_id + staged artifact (sha256/stage_path) or
runtime_install_command. Per-card validate + revert. Review-gated."""
import json, glob, yaml, re, os, subprocess, sys

LIN_CARD = glob.glob("detection_scanner/ttps/TTP-2026-0176-*.json")[0]
LIN_SCN  = glob.glob("scenarios/edr/edr-022-*.yml")[0]
WIN_CARD = glob.glob("detection_scanner/ttps/TTP-2026-0002-*.json")[0]
WIN_SCN  = glob.glob("scenarios/edr/edr-002-*.yml")[0]

TACTIC={"T1057":("TA0007","Discovery"),"T1552":("TA0006","Credential Access"),"T1548":("TA0004","Privilege Escalation"),
 "T1611":("TA0004","Privilege Escalation"),"T1613":("TA0007","Discovery"),"T1082":("TA0007","Discovery"),
 "T1046":("TA0007","Discovery"),"T1003":("TA0006","Credential Access"),"T1021":("TA0008","Lateral Movement"),
 "T1110":("TA0006","Credential Access"),"T1134":("TA0004","Privilege Escalation"),"T1068":("TA0004","Privilege Escalation"),
 "T1558":("TA0006","Credential Access"),"T1572":("TA0011","Command and Control"),"T1569":("TA0002","Execution")}

# tool | platform | techs(comma, sub ok) | human description | run command (uses {BIN})
TOOLS=[
 ("pspy","linux","T1057,T1552.007","snoops processes and cron jobs without root to capture credentials passed on command lines","{BIN} -pf -i 1000"),
 ("suid3num","linux","T1548.001","enumerates SUID binaries and auto-suggests GTFOBins privilege-escalation paths","python3 {BIN} -e"),
 ("amicontained","linux","T1611,T1613","introspects the container runtime, capabilities and seccomp to find escape paths","{BIN}"),
 ("lse","linux","T1082,T1046","runs Linux Smart Enumeration to surface privesc and lateral opportunities","{BIN} -l1"),
 ("linenum","linux","T1082,T1552.001","runs LinEnum to harvest system, credential and SUID findings","{BIN} -t"),
 ("credking","linux","T1110.001,T1552","sprays and harvests credentials across reachable services","{BIN} --help"),
 ("impacket","windows","T1003.003,T1021.002","runs secretsdump/psexec from Impacket to dump domain secrets and move laterally","{BIN}"),
 ("mimikatz","windows","T1003.001","dumps plaintext credentials and tickets from LSASS memory","{BIN} \"sekurlsa::logonpasswords\" exit"),
 ("pypykatz","windows","T1003.001","parses an LSASS dump with pypykatz to extract credentials offline","{BIN} lsa minidump lsass.dmp"),
 ("netexec","windows","T1021.002,T1110.001","sweeps SMB/WinRM across hosts to spray credentials and execute commands","{BIN} smb 10.0.0.0/24 -u user -p pass"),
 ("evil-winrm","windows","T1021.006","opens an interactive WinRM shell to a remote Windows host","{BIN} -i 10.0.0.5 -u user -p pass"),
 ("printspoofer","windows","T1134.002,T1068","abuses SeImpersonatePrivilege to escalate a service account to SYSTEM","{BIN} -i -c cmd"),
 ("nmap","linux","T1046,T1018","scans the internal network for reachable hosts and services","{BIN} -sS -T4 10.0.0.0/24"),
 ("frp","linux","T1572","establishes a reverse tunnel out to an attacker relay to pivot into the network","{BIN} -c frpc.ini"),
 # --- C2 frameworks ---
 ("sliver","windows","T1071.001,T1059.003","runs a Sliver C2 implant that beacons to the team server over HTTPS","{BIN}"),
 ("havoc","windows","T1071.001,T1055","runs a Havoc demon that beacons and injects into a host process","{BIN}"),
 ("empire","windows","T1059.001,T1071.001","runs an Empire PowerShell agent that stages and beacons to the listener","{BIN}"),
 ("metasploit","linux","T1059.004,T1071.001","generates and runs a Meterpreter session back to the handler","{BIN} -q -x 'use exploit/multi/handler; run'"),
 ("starkiller","windows","T1059.001,T1071.001","drives Empire agents through the Starkiller C2 interface","{BIN}"),
 # --- Active Directory attack ---
 ("bloodhound","windows","T1087.002,T1069.002","collects AD objects and ACLs to map attack paths to Domain Admin","{BIN} -c All"),
 ("rubeus","windows","T1558.003,T1550.003","roasts and forges Kerberos tickets (kerberoast / asreproast / pass-the-ticket)","{BIN} kerberoast"),
 ("bloodyad","windows","T1098,T1484.001","abuses AD ACLs/DACLs to add rights and persist via bloodyAD","{BIN} --host dc01 get writable"),
 ("krbrelayup","windows","T1558,T1068","performs a Kerberos relay to escalate from a domain user to SYSTEM","{BIN} relay -d corp.local"),
 ("tokenvator","windows","T1134.001,T1134.002","manipulates and impersonates Windows access tokens to escalate","{BIN} GetSystem"),
 # --- web / exploitation ---
 ("sqlmap","linux","T1190,T1213","automates SQL injection discovery and data exfiltration against a web app","{BIN} -u http://10.0.0.5/?id=1 --batch --dump"),
 ("nuclei","linux","T1595.002,T1190","scans web surfaces with templated checks for exploitable vulnerabilities","{BIN} -u http://10.0.0.5 -silent"),
 ("ffuf","linux","T1595.003","fuzzes web paths and parameters to discover hidden endpoints","{BIN} -u http://10.0.0.5/FUZZ -w wordlist.txt"),
 ("wpscan","linux","T1190,T1110.001","enumerates WordPress users/plugins and brute-forces credentials","{BIN} --url http://10.0.0.5 --enumerate u"),
 ("commix","linux","T1190,T1059.004","automates OS command-injection exploitation against a web app","{BIN} -u http://10.0.0.5/?x=1 --batch"),
 # --- recon / cred / password ---
 ("responder","linux","T1557.001,T1187","poisons LLMNR/NBT-NS to capture NetNTLM hashes on the LAN","{BIN} -I eth0 -wd"),
 ("smbmap","linux","T1135,T1021.002","enumerates SMB shares and permissions across reachable hosts","{BIN} -H 10.0.0.5 -u user -p pass"),
 ("hashcat","linux","T1110.002","cracks captured credential hashes offline with GPU acceleration","{BIN} -m 1000 hashes.txt wordlist.txt"),
 ("theharvester","linux","T1589.002,T1590","harvests emails, hosts and subdomains for a target domain (OSINT)","{BIN} -d corp.local -b all"),
]

def pack(tool):
    p=f"tools/packs/{tool}.yml"
    if not os.path.exists(p): return None
    d=yaml.safe_load(open(p))
    ins=d.get("install",{}) or {}; art=ins.get("artifact") or {}
    return {"adapter":d.get("adapter_id"),
            "staged":bool(art),
            "filename":art.get("filename"),"url":art.get("url"),"sha256":art.get("sha256"),
            "stage_path":art.get("stage_path"),
            "install":ins.get("runtime_install_command"),
            "src":d.get("source") or (d.get("metadata",{}) or {}).get("source") or "https://github.com"}

def slug(s,p):
    c="".join(ch if ch.isalnum() else "-" for ch in s.lower()).strip("-")
    while "--" in c: c=c.replace("--","-")
    return f"{p}-{c}"[:120]

def nextids():
    n=max(int(re.search(r'TTP-2026-(\d+)',f).group(1)) for f in glob.glob("detection_scanner/ttps/TTP-2026-*.json"))
    s=max(int(re.search(r'edr-(\d+)',f).group(1)) for f in glob.glob("scenarios/edr/edr-*.yml"))
    return f"TTP-2026-{n+1:04d}", f"{s+1:03d}"

def validate_ok():
    r=subprocess.run(["python3","detection_scanner/scripts/validate.py","--quiet"],capture_output=True,text=True)
    return "0 fail" in (r.stdout+r.stderr).splitlines()[-1] if (r.stdout+r.stderr).strip() else False

built=0; skipped=[]
for tool,plat,techs,desc,runtmpl in TOOLS:
    if glob.glob(f"scenarios/edr/edr-*-mature-{tool}.yml"):  # dedup: already built
        continue
    pk=pack(tool)
    if not pk or not pk["adapter"]: skipped.append((tool,"no pack")); continue
    TTP,SNUM=nextids(); SIM=f"SIM-EDR-{SNUM}"
    card=f"detection_scanner/ttps/{TTP}-sim-edr-{SNUM}.json"
    scn=f"scenarios/edr/edr-{SNUM}-mature-{tool}.yml"
    staged=pk["staged"]; BIN = pk["stage_path"] if staged else (tool if plat=="linux" else f"{tool}.exe")
    run = runtmpl.format(BIN=BIN)
    exc,exs = (LIN_CARD,LIN_SCN) if plat=="linux" else (WIN_CARD,WIN_SCN)
    primary=techs.split(",")[0]
    # ---- card ----
    d=json.load(open(exc)); d["id"]=TTP
    nm=f"{tool.capitalize()} — {desc.split(' and ')[0][:60]}"
    d["identity"]["name"]=f"Offensive Tooling: {tool} {('(payload-shelf staged)' if staged else '(adapter-deployed)')}"
    d["identity"]["summary"]=f"A service account deploys and runs {tool} ({desc}). Deployed via adapter_ref {pk['adapter']}{' and the digest-pinned payload shelf' if staged else ''}."
    d["identity"]["description"]=(
        f"{tool} {desc}. The tool is delivered through the Cortex POV {'payload shelf (sha256-pinned '+str(pk['sha256'])[:16]+'…, staged to '+str(BIN)+')' if staged else 'tool adapter ('+pk['adapter']+', installed by the pack at dispatch)'} "
        f"rather than an ad-hoc internet download, so it survives default-deny egress and the absent detection is never a manufactured false negative. "
        f"Cortex should detect the execution of {tool} and its artifacts.")
    tl=[]
    for t in techs.split(","):
        base=t.split(".")[0]; ta=TACTIC.get(base,("TA0002","Execution"))
        e={"technique_id":base,"name":t,"tactic_ids":[ta[0]],"tactic_names":[ta[1]]}
        if "." in t: e["subtechnique_id"]=t
        tl.append(e)
    d["mitre_attack"]={"matrix":"enterprise","techniques":tl}
    ex=d["execution"]; ex["target_platform"]=plat; ex["execution_framework"]="custom-cortex"
    if staged:
        deploy=(f"[ -s {BIN} ] || {{ echo '[FATAL] PAYLOAD_NOT_STAGED_ON_TARGET:{BIN} — TTP did NOT run; absent detections are NOT a Cortex miss' >&2; exit 1; }}\nsha256sum {BIN}\n")
    else:
        deploy=(f"# adapter {pk['adapter']} installs the tool at dispatch (pack runtime_install_command); this payload runs it.\n")
    ex["payload"]={"interpreter":("bash" if plat=="linux" else "powershell"),"input_variables":[],"timeout_seconds":180,
        "code":f"# MATURE: {tool} deployed via adapter_ref {pk['adapter']}{' + payload shelf' if staged else ''}.\n{deploy}{run}\n"}
    ex["expected_artifacts"]=[{"artifact_type":"process","description":f"{tool} executed by a service account.",
        "expected_value":f"action_process_image_name contains \"{os.path.basename(str(BIN))}\""}]
    ex["cleanup"]={"interpreter":("bash" if plat=="linux" else "powershell"),"code":f"echo '[CortexSim] {tool} run complete'"}
    iocs=[]
    if staged and pk["sha256"]:
        iocs.append({"ioc_type":"file-sha256","value":pk["sha256"],"reputation_required":"suspicious",
                     "context":f"Digest-pinned {pk['filename']} delivered via the payload shelf; dual-use, malicious-in-context when run by a service account."})
    iocs.append({"ioc_type":"filename" if plat=="windows" else "filepath","value":os.path.basename(str(BIN)) if plat=="windows" else BIN,
                 "context":f"{tool} artifact path/name on the target."})
    field = "actor_process_image_path" if plat=="linux" else "actor_process_image_name"
    match = BIN if plat=="linux" else os.path.basename(str(BIN))
    d["detections"]={"iocs":iocs,"biocs":[{
        "name":f"{tool.capitalize()} Executed by a Service Account on the Endpoint",
        "description":f"A non-interactive service account runs {tool} — an offensive {pk['adapter']}-wired tool "
                      f"not part of normal operations on this host.",
        "logic":("dataset = xdr_data\n| filter event_type = ENUM.PROCESS and event_sub_type = ENUM.PROCESS_START\n"
                 f"| filter {field} contains \"{match}\" or action_process_command_line contains \"{tool}\"\n"
                 "| comp count() as runs by agent_hostname, actor_effective_username\n| filter runs >= 1"),
        "mitre_technique_ids":[techs.split(",")[0]],"severity":"high","suppression_window_seconds":0}],
      "xql_queries":[{"purpose":"validation","name":f"{tool.capitalize()} Execution Hunt",
        "query":("dataset = xdr_data\n| filter event_type = ENUM.PROCESS and event_sub_type = ENUM.PROCESS_START\n"
                 f"| filter action_process_command_line contains \"{tool}\"\n"
                 "| fields agent_hostname, actor_effective_username, action_process_image_name, action_process_command_line"),
        "expected_rows_min":1,"dataset":"xdr_data"}],
      "correlation_rules":[],
      "abiocs":[{"name":f"First-Seen Execution of {tool} on This Host",
        "description":f"A host that has never run {tool} executes it shortly after it is staged/installed — the deploy-then-run chain of an offensive tool.",
        "logic":("dataset = xdr_data\n| filter event_type = ENUM.PROCESS and event_sub_type = ENUM.PROCESS_START\n"
                 f"| filter action_process_command_line contains \"{tool}\"\n| comp count() as first_runs by agent_hostname"),
        "behavioral_profile":"machine-learning",
        "causality_anchor":f"adapter deploy/stage of {tool} -> first execution by a service-account shell",
        "mitre_technique_ids":[techs.split(",")[0]],"severity":"high"}],
      "analytics_modules":[f"Cortex XSIAM Analytics (XDR Agent) — uncommon offensive tool ({tool}) executed by a service account; Required Data: XDR Agent; ATT&CK {techs.split(',')[0]}."]}
    d["references"]=[{"title":f"MITRE ATT&CK {primary}","url":f"https://attack.mitre.org/techniques/{primary.replace('.','/')}/","publisher":"MITRE","primary":True}]
    json.dump(d,open(card,"w"),indent=2); open(card,"a").write("\n")
    # ---- scenario ----
    s=yaml.safe_load(open(exs)); s["scenario_id"]=SIM; s["name"]=d["identity"]["name"]
    s["detection_types"]=["BIOC","XQL","Analytics","ABIOC","IOC"]; s["mitre_technique"]=primary
    s["external_tools"]=[{"name":tool,"source":pk["src"],"type":("script" if plat=="linux" else "binary"),"install_inline":False,"adapter_ref":pk["adapter"]}]
    plats=["linux","container"] if plat=="linux" else ["windows"]
    s["steps"]=[
      {"id":"step-01","name":f"Deploy preflight for {tool} ({'shelf-staged' if staged else 'adapter-installed'})",
       "command":(f"[ -s {BIN} ] || {{ echo '[{SIM}][FATAL] PAYLOAD_NOT_STAGED_ON_TARGET:{BIN}' >&2; exit 1; }}; sha256sum {BIN}; id" if staged
                  else f"echo '[{SIM}] adapter {pk['adapter']} provides {tool}'; id"),
       "identity":"www-data","platforms":plats,"mitre_technique":"T1082"},
      {"id":"step-02","name":f"Execute {tool}","command":run,"identity":"www-data","platforms":plats,
       "mitre_technique":primary,"causality":{"parent_step":"step-01","pivot":"process_lineage"},
       "expected_detections":[
         {"plane":"EDR","type":"BIOC","description":f"{tool} executed by a service account",
          "ttp_ref":TTP,"detection_id":slug(f"{tool.capitalize()} Executed by a Service Account on the Endpoint","bioc")},
         {"plane":"EDR","type":"Analytics","description":f"Analytics flags the uncommon {tool} execution",
          "ttp_ref":TTP,"detection_id":slug(f"{tool.capitalize()} Execution Hunt","xql")}]}]
    yaml.safe_dump(s,open(scn,"w"),sort_keys=False,width=100,allow_unicode=True)
    if validate_ok():
        built+=1; print(f"  OK  {TTP} {SIM}  {tool:12} adapter={pk['adapter']} {'STAGED '+str(BIN) if staged else 'adapter-install'}")
    else:
        os.remove(card); os.path.exists(scn) and os.remove(scn); skipped.append((tool,"validate")); print(f"  REVERTED {tool}")

print(f"\nbuilt {built} mature scenarios; skipped {skipped}")

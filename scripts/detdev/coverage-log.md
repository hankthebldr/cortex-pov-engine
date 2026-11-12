# Endpoint analytics coverage log

| platform | alert | TTP | scenario | result |
|---|---|---|---|---|
| windows | A process is masquerading as a common Microsoft product | TTP-2026-0198 | SIM-EDR-036 | ok |
| windows | A rare DLL signed by an uncommon vendor was hijacked into a Microsoft process | TTP-2026-0199 | SIM-EDR-037 | ok |
| windows | Abnormal RDP connections to multiple hosts from a rarely seen host | TTP-2026-0200 | SIM-EDR-038 | ok |
| windows | An unsigned process created a scheduled task and performed an injection | - | - | genfail |
| windows | Attempt to execute a command on a remote host using PsExec | TTP-2026-0201 | SIM-EDR-039 | ok |
| windows | Command execution via wmiexec | TTP-2026-0202 | SIM-EDR-040 | ok |
| windows | Copy a process memory file | TTP-2026-0203 | SIM-EDR-041 | ok |
| windows | PowerShell runs suspicious base64-encoded commands | - | - | genfail |
| windows | Suspicious dump of ntds.dit using Shadow Copy with ntdsutil or vssadmin | TTP-2026-0204 | SIM-EDR-042 | ok |
| windows | An uncommon service was started | TTP-2026-0205 | SIM-EDR-043 | ok |
| windows | 64-bit PowerShell spawning an uncommon 32-bit PowerShell | TTP-2026-0206 | SIM-EDR-044 | ok |
| windows | New addition to Windows Defender exclusion list | TTP-2026-0207 | SIM-EDR-045 | ok |
| windows | Hidden Attribute was added to a file using attrib | TTP-2026-0208 | SIM-EDR-046 | ok |
| windows | A rare local administrator login | TTP-2026-0209 | SIM-EDR-047 | ok |
| windows | An uncommon lolbin execution by scheduled task | TTP-2026-0210 | SIM-EDR-048 | ok |
| linux | Possible brute force on sudo user | TTP-2026-0211 | SIM-EDR-049 | ok |
| linux | Masquerading as the Linux crond process | TTP-2026-0212 | SIM-EDR-050 | ok |
| linux | Setuid and Setgid file bit manipulation | TTP-2026-0213 | SIM-EDR-051 | ok |
| linux | Reading bash command history file | TTP-2026-0214 | SIM-EDR-052 | ok |
| linux | Possible binary padding using dd | TTP-2026-0215 | SIM-EDR-053 | ok |
| linux | A compressed file was exfiltrated over SSH | TTP-2026-0216 | SIM-EDR-054 | ok |
| linux | Adding execution privileges | TTP-2026-0217 | SIM-EDR-055 | ok |
| linux | Kubernetes nsenter container escape | TTP-2026-0218 | SIM-EDR-056 | ok |
| linux | Suspicious process execution in a privileged container | TTP-2026-0219 | SIM-EDR-057 | ok |
| linux | Suspicious curl user agent | TTP-2026-0220 | SIM-EDR-058 | ok |
| windows | A commonly abused process connected to a rare cloud resource | TTP-2026-0221 | SIM-EDR-059 | ok |
| windows | A commonly abused process connected to a rare external host | TTP-2026-0222 | SIM-EDR-060 | ok |
| windows | A compromised process accessed a rare cloud resource | TTP-2026-0223 | SIM-EDR-061 | ok |
| windows | A compromised process accessed a rare external host | TTP-2026-0224 | SIM-EDR-062 | ok |
| windows | A contained executable from a mounted share initiated a suspicious outbound network connection | TTP-2026-0225 | SIM-EDR-063 | ok |
| windows | A contained executable was executed by an unusual process | TTP-2026-0226 | SIM-EDR-064 | ok |
| windows | A process connected to a rare cloud resource | TTP-2026-0227 | SIM-EDR-065 | ok |
| windows | A process connected to a rare external host | TTP-2026-0228 | SIM-EDR-066 | ok |
| windows | A process connected to rare external host | TTP-2026-0229 | SIM-EDR-067 | ok |
| windows | A process was executed with a command line obfuscated by Unicode character substitution | TTP-2026-0230 | SIM-EDR-068 | ok |
| windows | A rare DLL, signed by an uncommon vendor, was hijacked into a Microsoft process | TTP-2026-0231 | SIM-EDR-069 | ok |
| windows | A service was disabled | TTP-2026-0232 | SIM-EDR-070 | ok |
| windows | A suspicious process enrolled for a certificate | TTP-2026-0233 | SIM-EDR-071 | ok |
| windows | A user accessed an uncommon AppID | TTP-2026-0234 | SIM-EDR-072 | ok |
| windows | A user logged in from an abnormal country or ASN | TTP-2026-0235 | SIM-EDR-073 | ok |
| windows | Abnormal Communication to a Rare Domain | TTP-2026-0236 | SIM-EDR-074 | ok |
| windows | Abnormal communication with a rare combination of TLS and HTTP User Agent | TTP-2026-0237 | SIM-EDR-075 | ok |
| windows | Abnormal connections to a dormant host from a newly seen endpoint | TTP-2026-0238 | SIM-EDR-076 | ok |
| windows | Abnormal ICMP echo (PING) to multiple hosts | - | - | genfail |
| windows | Abnormal network communication through TOR using an uncommon port | TTP-2026-0239 | SIM-EDR-077 | ok |
| windows | Abnormal network communication with a rare combination of HTTP User Agent and HTTP Server | TTP-2026-0240 | SIM-EDR-078 | ok |
| windows | Abnormal process connection to default Meterpreter port | TTP-2026-0241 | SIM-EDR-079 | ok |
| windows | Abnormal RDP connections to multiple hosts | TTP-2026-0242 | SIM-EDR-080 | ok |
| windows | Abnormal RDP session to a remote host from a rarely seen host | TTP-2026-0243 | SIM-EDR-081 | ok |
| windows | Abnormal Recurring Communications to a Rare Domain | TTP-2026-0244 | SIM-EDR-082 | ok |
| windows | Abnormal SMB activity to multiple hosts | TTP-2026-0245 | SIM-EDR-083 | ok |
| windows | Abnormal SMB scanning activity to multiple hosts | TTP-2026-0246 | SIM-EDR-084 | ok |
| windows | Abnormal User Login to Domain Controller | TTP-2026-0247 | SIM-EDR-085 | ok |
| windows | An uncommon RDP session from a managed host | TTP-2026-0248 | SIM-EDR-086 | ok |
| windows | An uncommon RDP session was established | TTP-2026-0249 | SIM-EDR-087 | ok |
| windows | An unsigned process created scheduled task and performed an injection | TTP-2026-0250 | SIM-EDR-088 | ok |
| windows | Analytics enhanced NGFW - Rare Internal Firewall Vulnerability Threat Alert | TTP-2026-0251 | SIM-EDR-089 | ok |
| windows | AppleScript interpreter dynamic library loaded into a process | TTP-2026-0252 | SIM-EDR-090 | ok |
| windows | AppleScript process executed with a rare command line | TTP-2026-0253 | SIM-EDR-091 | ok |
| windows | Attempt to execute a command on a remote host using PsExec.exe | TTP-2026-0254 | SIM-EDR-092 | ok |
| windows | Cached credentials discovery with cmdkey | TTP-2026-0255 | SIM-EDR-093 | ok |
| windows | Cloud IMDS access followed by remote token usage | TTP-2026-0256 | SIM-EDR-094 | ok |
| windows | Common third-party software name masquerading | TTP-2026-0257 | SIM-EDR-095 | ok |
| windows | Commonly abused process launched as a system service | TTP-2026-0258 | SIM-EDR-096 | ok |
| windows | Conhost.exe spawned a suspicious cmd process | TTP-2026-0259 | SIM-EDR-097 | ok |
| windows | Contained process execution with a rare GitHub URL | TTP-2026-0260 | SIM-EDR-098 | ok |
| windows | Discovery of host users via WMIC | TTP-2026-0261 | SIM-EDR-099 | ok |
| windows | Encoded information using Windows certificate management tool | TTP-2026-0262 | SIM-EDR-100 | ok |
| windows | Execution of an uncommon process at an early startup stage | TTP-2026-0263 | SIM-EDR-101 | ok |
| windows | Execution of an uncommon process at an early startup stage by Windows system binary | TTP-2026-0264 | SIM-EDR-102 | ok |
| windows | Execution of an uncommon process with a local/domain user SID at an early startup stage | TTP-2026-0265 | SIM-EDR-103 | ok |
| windows | Execution of an uncommon process with a local/domain user SID at early startup by a system binary | TTP-2026-0266 | SIM-EDR-104 | ok |
| windows | Execution of command from within a Kubernetes pod using kubelet credentials | TTP-2026-0267 | SIM-EDR-105 | ok |
| windows | Execution of dllhost.exe with an empty command line | TTP-2026-0268 | SIM-EDR-106 | ok |
| windows | Execution of masqueraded third-party utility | TTP-2026-0269 | SIM-EDR-107 | ok |
| windows | Extracting credentials from Unix files | TTP-2026-0270 | SIM-EDR-108 | ok |
| windows | File transfer from unusual IP using known tools | - | - | genfail |
| windows | Fodhelper.exe UAC bypass | TTP-2026-0271 | SIM-EDR-109 | ok |
| windows | Globally uncommon high entropy module was loaded | TTP-2026-0272 | SIM-EDR-110 | ok |
| windows | Globally uncommon high entropy process was executed | TTP-2026-0273 | SIM-EDR-111 | ok |
| windows | Globally uncommon image load from a signed process | TTP-2026-0274 | SIM-EDR-112 | ok |
| windows | Globally uncommon injection from a signed process | TTP-2026-0275 | SIM-EDR-113 | ok |
| windows | Globally uncommon IP address by a common process (sha256) | TTP-2026-0276 | SIM-EDR-114 | ok |
| windows | Globally uncommon IP address connection from a signed process | TTP-2026-0277 | SIM-EDR-115 | ok |
| windows | Globally uncommon process execution from a signed process | - | - | genfail |
| windows | Globally uncommon root domain from a signed process | TTP-2026-0278 | SIM-EDR-116 | ok |
| windows | Globally uncommon root-domain port combination by a common process (sha256) | TTP-2026-0279 | SIM-EDR-117 | ok |
| windows | Globally uncommon root-domain port combination from a signed process | TTP-2026-0280 | SIM-EDR-118 | ok |
| windows | HTTP with suspicious characteristics | TTP-2026-0281 | SIM-EDR-119 | ok |
| windows | Injection into rundll32.exe | TTP-2026-0282 | SIM-EDR-120 | ok |
| windows | Installation of a new System-V service | TTP-2026-0283 | SIM-EDR-121 | ok |
| windows | Interactive login by a service account | TTP-2026-0284 | SIM-EDR-122 | ok |
| windows | JS runtime attempted to read Git authentication tokens | TTP-2026-0285 | SIM-EDR-123 | ok |
| windows | Kerberos Pre-Auth Failures by Host | TTP-2026-0286 | SIM-EDR-124 | ok |
| windows | Kerberos Pre-Auth Failures by User and Host | TTP-2026-0287 | SIM-EDR-125 | ok |
| windows | Kerberos Traffic from Non-Standard Process | TTP-2026-0288 | SIM-EDR-126 | ok |
| windows | Kerberos User Enumeration | TTP-2026-0289 | SIM-EDR-127 | ok |
| windows | LDAP traffic from non-standard process | TTP-2026-0290 | SIM-EDR-128 | ok |
| windows | Linux process execution with a rare GitHub URL | TTP-2026-0291 | SIM-EDR-129 | ok |
| windows | LOLBAS executable injects into another process | TTP-2026-0292 | SIM-EDR-130 | ok |
| windows | LOLBIN process executed with a high integrity level | TTP-2026-0293 | SIM-EDR-131 | ok |
| windows | Manipulation of netsh helper DLLs Registry keys | TTP-2026-0294 | SIM-EDR-132 | ok |
| windows | Massive upload to a rare storage or mail domain | TTP-2026-0295 | SIM-EDR-133 | ok |
| windows | Memory dumping with comsvcs.dll | TTP-2026-0296 | SIM-EDR-134 | ok |
| windows | Microsoft HTML Application Host spawns from CMD or PowerShell | TTP-2026-0297 | SIM-EDR-135 | ok |
| windows | Microsoft Office injects code into a process | TTP-2026-0298 | SIM-EDR-136 | ok |
| windows | Microsoft Office Process Spawning a Suspicious One-Liner | TTP-2026-0299 | SIM-EDR-137 | ok |
| windows | Microsoft Office process spawns a commonly abused process | TTP-2026-0300 | SIM-EDR-138 | ok |
| windows | Microsoft Office process spawns conhost.exe | TTP-2026-0301 | SIM-EDR-139 | ok |
| windows | Mimikatz command-line arguments | TTP-2026-0302 | SIM-EDR-140 | ok |
| windows | Mshta.exe launched with suspicious arguments | TTP-2026-0303 | SIM-EDR-141 | ok |
| windows | Mshta.exe spawns from a browser process | TTP-2026-0304 | SIM-EDR-142 | ok |
| windows | Msiexec execution of an executable from an uncommon remote location | TTP-2026-0305 | SIM-EDR-143 | ok |
| windows | Multiple discovery commands on a Linux host by the same process | TTP-2026-0306 | SIM-EDR-144 | ok |
| windows | Multiple discovery commands on a Windows host by the same process | TTP-2026-0307 | SIM-EDR-145 | ok |
| windows | Multiple Rare LOLBIN Process Executions by User | TTP-2026-0308 | SIM-EDR-146 | ok |
| windows | Multiple Rare Process Executions in Organization | TTP-2026-0309 | SIM-EDR-147 | ok |
| windows | Multiple Weakly-Encrypted Kerberos Tickets Received | TTP-2026-0310 | SIM-EDR-148 | ok |
| windows | NTLM Brute Force on a Service Account | TTP-2026-0311 | SIM-EDR-149 | ok |
| windows | Office process spawned with suspicious command-line arguments | TTP-2026-0312 | SIM-EDR-150 | ok |
| windows | Phantom DLL Loading | TTP-2026-0313 | SIM-EDR-151 | ok |
| windows | Ping to localhost from an uncommon, unsigned parent process | TTP-2026-0314 | SIM-EDR-152 | ok |
| windows | Possible DLL Hijack into a Microsoft process | TTP-2026-0315 | SIM-EDR-153 | ok |
| windows | Possible DLL Search Order Hijacking | TTP-2026-0316 | SIM-EDR-154 | ok |
| windows | Possible external RDP Brute-Force | TTP-2026-0317 | SIM-EDR-155 | ok |
| windows | Possible Kerberos relay attack | TTP-2026-0318 | SIM-EDR-156 | ok |
| windows | Possible malicious .NET compilation started by a commonly abused process | TTP-2026-0319 | SIM-EDR-157 | ok |
| windows | Possible network service discovery via command-line tool | TTP-2026-0320 | SIM-EDR-158 | ok |
| windows | Possible RDP session hijacking using tscon.exe | TTP-2026-0321 | SIM-EDR-159 | ok |
| windows | Potential extraction of NAA Account Credentials in Microsoft Configuration Manager | TTP-2026-0322 | SIM-EDR-160 | ok |
| windows | PowerShell running with download in the command line | TTP-2026-0323 | SIM-EDR-161 | ok |
| windows | PowerShell script executed from a temporary directory | TTP-2026-0324 | SIM-EDR-162 | ok |
| windows | PowerShell suspicious flags | TTP-2026-0325 | SIM-EDR-163 | ok |
| windows | PsExec was executed with a suspicious command line | TTP-2026-0326 | SIM-EDR-164 | ok |
| windows | Rare access to known advertising domains | TTP-2026-0327 | SIM-EDR-165 | ok |
| windows | Rare AppID usage to a rare destination | TTP-2026-0328 | SIM-EDR-166 | ok |
| windows | Rare binary connected to a rare cloud resource | TTP-2026-0329 | SIM-EDR-167 | ok |
| windows | Rare binary connected to a rare external host | TTP-2026-0330 | SIM-EDR-168 | ok |
| windows | Rare communication over email ports to external email server by unsigned process | TTP-2026-0331 | SIM-EDR-169 | ok |
| windows | Rare file transfer over SMB protocol | TTP-2026-0332 | SIM-EDR-170 | ok |
| windows | Rare LOLBIN Process Execution by User | TTP-2026-0333 | SIM-EDR-171 | ok |
| windows | Rare MS-Update traffic over HTTP | TTP-2026-0334 | SIM-EDR-172 | ok |
| windows | Rare NTLM Access By User To Host | TTP-2026-0335 | SIM-EDR-173 | ok |
| windows | Rare NTLM Usage by User | TTP-2026-0336 | SIM-EDR-174 | ok |
| windows | Rare process executed by an AppleScript | TTP-2026-0337 | SIM-EDR-175 | ok |
| windows | Rare process execution by user | TTP-2026-0338 | SIM-EDR-176 | ok |
| windows | Rare process execution in organization | TTP-2026-0339 | SIM-EDR-177 | ok |
| windows | Rare process spawned by srvany.exe | TTP-2026-0340 | SIM-EDR-178 | ok |
| windows | Rare process with VNC server capabilities started | TTP-2026-0341 | SIM-EDR-179 | ok |
| windows | Rare RDP session to a remote host | TTP-2026-0342 | SIM-EDR-180 | ok |
| windows | Rare security product signed executable executed in the network | TTP-2026-0343 | SIM-EDR-181 | ok |
| windows | Rare signature signed executable executed in the network | TTP-2026-0344 | SIM-EDR-182 | ok |
| windows | Rare SMB session to a remote host | TTP-2026-0345 | SIM-EDR-183 | ok |
| windows | Rare SMTP/S Session | TTP-2026-0346 | SIM-EDR-184 | ok |
| windows | Rare Unix process divided files by size | TTP-2026-0347 | SIM-EDR-185 | ok |
| windows | Rare unsigned process execution by scheduled task | TTP-2026-0348 | SIM-EDR-186 | ok |
| windows | Rare Unsigned Process Spawned by Office Process Under Suspicious Directory | TTP-2026-0349 | SIM-EDR-187 | ok |
| windows | Rare Windows Remote Management (WinRM) HTTP Activity | TTP-2026-0350 | SIM-EDR-188 | ok |
| windows | Rare WinRM Session | TTP-2026-0351 | SIM-EDR-189 | ok |
| windows | RDP Connection to localhost | TTP-2026-0352 | SIM-EDR-190 | ok |
| windows | RDP from an unmanaged endpoint in a typically managed subnet | TTP-2026-0353 | SIM-EDR-191 | ok |
| windows | Recurring access to rare domain | TTP-2026-0354 | SIM-EDR-192 | ok |
| windows | Recurring access to rare IP | TTP-2026-0355 | SIM-EDR-193 | ok |
| windows | Recurring rare domain access from an unsigned process | TTP-2026-0356 | SIM-EDR-194 | ok |
| windows | Recurring rare domain access to dynamic DNS domain | TTP-2026-0357 | SIM-EDR-195 | ok |
| windows | Registration of Uncommon .NET Services and/or Assemblies | TTP-2026-0358 | SIM-EDR-196 | ok |
| windows | Registry credentials extraction | TTP-2026-0359 | SIM-EDR-197 | ok |
| windows | Remote command execution via wmic.exe | TTP-2026-0360 | SIM-EDR-198 | ok |
| windows | Remote PsExec-like command execution | TTP-2026-0361 | SIM-EDR-199 | ok |
| windows | Remote service command execution from an uncommon source | TTP-2026-0362 | SIM-EDR-200 | ok |
| windows | Remote service start from an uncommon source | TTP-2026-0363 | SIM-EDR-201 | ok |
| windows | Remote WMI process execution | TTP-2026-0364 | SIM-EDR-202 | ok |
| windows | Retrieval of kubelet credentials | TTP-2026-0365 | SIM-EDR-203 | ok |
| windows | Rundll32.exe executes a rare unsigned module | TTP-2026-0366 | SIM-EDR-204 | ok |
| windows | Rundll32.exe running with no command-line arguments | TTP-2026-0367 | SIM-EDR-205 | ok |
| windows | Rundll32.exe spawns conhost.exe | TTP-2026-0368 | SIM-EDR-206 | ok |
| windows | Scrcons.exe Rare Child Process | TTP-2026-0369 | SIM-EDR-207 | ok |
| windows | Screensaver process executed from Users or temporary folder | TTP-2026-0370 | SIM-EDR-208 | ok |
| windows | Script file added to startup-related Registry keys | TTP-2026-0371 | SIM-EDR-209 | ok |
| windows | Scripting engine connected to a rare external host | TTP-2026-0372 | SIM-EDR-210 | ok |
| windows | Service execution via sc.exe | TTP-2026-0373 | SIM-EDR-211 | ok |
| windows | Signed process creates a scheduled task via file access | TTP-2026-0374 | SIM-EDR-212 | ok |
| windows | Signed process performed an unpopular DLL injection | TTP-2026-0375 | SIM-EDR-213 | ok |
| windows | Signed process performed an unpopular injection | TTP-2026-0376 | SIM-EDR-214 | ok |
| windows | SMB Traffic from Non-Standard Process | TTP-2026-0377 | SIM-EDR-215 | ok |
| windows | Stored credentials exported using credwiz.exe | TTP-2026-0378 | SIM-EDR-216 | ok |
| windows | Suspicious .NET process loads an MSBuild DLL | TTP-2026-0379 | SIM-EDR-217 | ok |
| windows | Suspicious authentication package registered | TTP-2026-0380 | SIM-EDR-218 | ok |
| windows | Suspicious Certutil AD CS contact | TTP-2026-0381 | SIM-EDR-219 | ok |
| windows | Suspicious certutil command line | TTP-2026-0382 | SIM-EDR-220 | ok |
| windows | Suspicious data encryption | TTP-2026-0383 | SIM-EDR-221 | ok |
| windows | Suspicious disablement of the Windows Firewall | TTP-2026-0384 | SIM-EDR-222 | ok |
| windows | Suspicious DNS traffic | TTP-2026-0385 | SIM-EDR-223 | ok |
| windows | Suspicious docker image download from an unusual repository | TTP-2026-0386 | SIM-EDR-224 | ok |
| windows | Suspicious dump of ntds.dit using Shadow Copy with ntdsutil/vssadmin | - | - | genfail |
| windows | Suspicious External RDP Login | TTP-2026-0387 | SIM-EDR-225 | ok |
| windows | Suspicious failed HTTP request - potential Spring4Shell exploit | TTP-2026-0388 | SIM-EDR-226 | ok |
| windows | Suspicious HTTP parameters detected | TTP-2026-0389 | SIM-EDR-227 | ok |
| windows | Suspicious ICMP packet | TTP-2026-0390 | SIM-EDR-228 | ok |
| windows | Suspicious ICMP traffic that resembles smurf attack | TTP-2026-0391 | SIM-EDR-229 | ok |
| windows | Suspicious module load using direct syscall | TTP-2026-0392 | SIM-EDR-230 | ok |
| windows | Suspicious Network Connection Originating from AWS SSM Agent | TTP-2026-0393 | SIM-EDR-231 | ok |
| windows | Suspicious NTLM authentication with machine account | TTP-2026-0394 | SIM-EDR-232 | ok |
| windows | Suspicious PowerShell Command Line | TTP-2026-0395 | SIM-EDR-233 | ok |
| windows | Suspicious PowerShell Enumeration of Running Processes | TTP-2026-0396 | SIM-EDR-234 | ok |
| windows | Suspicious print processor registered | - | - | genfail |
| windows | Suspicious process accessed a site masquerading as Google | TTP-2026-0397 | SIM-EDR-235 | ok |
| windows | Suspicious process executed with a high integrity level | TTP-2026-0398 | SIM-EDR-236 | ok |
| windows | Suspicious process execution from tmp folder | TTP-2026-0399 | SIM-EDR-237 | ok |
| windows | Suspicious process loads a known PowerShell module | TTP-2026-0400 | SIM-EDR-238 | ok |
| windows | Suspicious Process Spawned by Adobe Reader | TTP-2026-0401 | SIM-EDR-239 | ok |
| windows | Suspicious Process Spawned by wininit.exe | TTP-2026-0402 | SIM-EDR-240 | ok |
| windows | Suspicious proxy environment variable setting | TTP-2026-0403 | SIM-EDR-241 | ok |
| windows | Suspicious RunOnce Parent Process | TTP-2026-0404 | SIM-EDR-242 | ok |
| windows | Suspicious runonce.exe parent process | TTP-2026-0405 | SIM-EDR-243 | ok |
| windows | Suspicious SearchProtocolHost.exe parent process | TTP-2026-0406 | SIM-EDR-244 | ok |
| windows | Suspicious setspn.exe execution | TTP-2026-0407 | SIM-EDR-245 | ok |
| windows | Suspicious SMB connection from domain controller | TTP-2026-0408 | SIM-EDR-246 | ok |
| windows | Suspicious successful RDP connection to localhost | TTP-2026-0409 | SIM-EDR-247 | ok |
| windows | Suspicious systemd timer activity | TTP-2026-0410 | SIM-EDR-248 | ok |
| windows | Suspicious time provider registered | TTP-2026-0411 | SIM-EDR-249 | ok |
| windows | Suspicious usage of File Server Remote VSS Protocol (FSRVP) | TTP-2026-0412 | SIM-EDR-250 | ok |
| windows | Svchost.exe loads a rare unsigned module | TTP-2026-0413 | SIM-EDR-251 | ok |
| windows | Uncommon AppleScript containing a potential obfuscation technique was executed | TTP-2026-0414 | SIM-EDR-252 | ok |
| windows | Uncommon AppleScript containing a potential persistence command was executed via the command line | TTP-2026-0415 | SIM-EDR-253 | ok |
| windows | Uncommon AppleScript designed to access credential files was executed via the command line | TTP-2026-0416 | SIM-EDR-254 | ok |
| windows | Uncommon AppleScript designed to access cryptocurrency wallet data was executed via the command line | TTP-2026-0417 | SIM-EDR-255 | ok |
| windows | Uncommon AppleScript designed to access sensitive application data was executed via the command line | TTP-2026-0418 | SIM-EDR-256 | ok |
| windows | Uncommon AppleScript designed to capture screen or clipboard data was executed via the command line | TTP-2026-0419 | SIM-EDR-257 | ok |
| windows | Uncommon AppleScript potentially utilizes credential-grabbing techniques to steal user passwords | TTP-2026-0420 | SIM-EDR-258 | ok |
| windows | Uncommon AppleScript was executed via the command line to contact an external server | TTP-2026-0421 | SIM-EDR-259 | ok |
| windows | Uncommon AppleScript with a potential defense-evasion command was executed via the command line | TTP-2026-0422 | SIM-EDR-260 | ok |
| windows | Uncommon AppleScript with a potential discovery command was executed via the command line | TTP-2026-0423 | SIM-EDR-261 | ok |
| windows | Uncommon ARP cache listing via arp.exe | TTP-2026-0424 | SIM-EDR-262 | ok |
| windows | Uncommon attempt to clear shell history | TTP-2026-0425 | SIM-EDR-263 | ok |
| windows | Uncommon cloud CLI tool usage | TTP-2026-0426 | SIM-EDR-264 | ok |
| windows | Uncommon communication to an instant messaging server | TTP-2026-0427 | SIM-EDR-265 | ok |
| windows | Uncommon DLL-sideloading from a logical CD-ROM (ISO) device | TTP-2026-0428 | SIM-EDR-266 | ok |
| windows | Uncommon DotNet module load relationship | TTP-2026-0429 | SIM-EDR-267 | ok |
| windows | Uncommon driver loaded | TTP-2026-0430 | SIM-EDR-268 | ok |
| windows | Uncommon execution of ODBCConf | TTP-2026-0431 | SIM-EDR-269 | ok |
| windows | Uncommon IP Configuration Listing via ipconfig.exe | TTP-2026-0432 | SIM-EDR-270 | ok |
| windows | Uncommon kernel module load | TTP-2026-0433 | SIM-EDR-271 | ok |
| windows | Uncommon Launch Agent persistency was registered or modified | TTP-2026-0434 | SIM-EDR-272 | ok |
| windows | Uncommon Launch Daemon persistency was registered or modified | TTP-2026-0435 | SIM-EDR-273 | ok |
| windows | Uncommon Linux process communication to a rare external host | TTP-2026-0436 | SIM-EDR-274 | ok |
| windows | Uncommon Linux remote shell command execution | TTP-2026-0437 | SIM-EDR-275 | ok |
| windows | Uncommon Linux shell command execution | TTP-2026-0438 | SIM-EDR-276 | ok |
| windows | Uncommon local scheduled task creation via schtasks.exe | TTP-2026-0439 | SIM-EDR-277 | ok |
| windows | Uncommon login item persistency was registered or modified | TTP-2026-0440 | SIM-EDR-278 | ok |
| windows | Uncommon macOS process communication to a rare external host | TTP-2026-0441 | SIM-EDR-279 | ok |
| windows | Uncommon macOS shell command execution | TTP-2026-0442 | SIM-EDR-280 | ok |
| windows | Uncommon Managed Object Format (MOF) compiler usage | TTP-2026-0443 | SIM-EDR-281 | ok |
| windows | Uncommon msiexec execution of an arbitrary file from a remote location | TTP-2026-0444 | SIM-EDR-282 | ok |
| windows | Uncommon net group command execution | TTP-2026-0445 | SIM-EDR-283 | ok |
| windows | Uncommon net localgroup command execution | TTP-2026-0446 | SIM-EDR-284 | ok |
| windows | Uncommon network connection under an untrusted and newly created causality actor | TTP-2026-0447 | SIM-EDR-285 | ok |
| windows | Uncommon network connection under an untrusted causality actor | TTP-2026-0448 | SIM-EDR-286 | ok |
| windows | Uncommon Node.js package initiated communication to an external host | TTP-2026-0449 | SIM-EDR-287 | ok |
| windows | Uncommon RDP connection | TTP-2026-0450 | SIM-EDR-288 | ok |
| windows | Uncommon recurring rare external host access | TTP-2026-0451 | SIM-EDR-289 | ok |
| windows | Uncommon remote monitoring and management tool | TTP-2026-0452 | SIM-EDR-290 | ok |
| windows | Uncommon remote scheduled task creation | TTP-2026-0453 | SIM-EDR-291 | ok |
| windows | Uncommon remote service start via sc.exe | TTP-2026-0454 | SIM-EDR-292 | ok |
| windows | Uncommon routing table listing via route.exe | TTP-2026-0455 | SIM-EDR-293 | ok |
| windows | Uncommon Service Create/Config | TTP-2026-0456 | SIM-EDR-294 | ok |
| windows | Uncommon service stop operation | TTP-2026-0457 | SIM-EDR-295 | ok |
| windows | Uncommon signed process execution by scheduled task | TTP-2026-0458 | SIM-EDR-296 | ok |
| windows | Uncommon SQL like command line | TTP-2026-0459 | SIM-EDR-297 | ok |
| windows | Uncommon user management via net command | TTP-2026-0460 | SIM-EDR-298 | ok |
| windows | Uncommon VNC server communication | TTP-2026-0461 | SIM-EDR-299 | ok |
| windows | Uncommon WPAD queries | TTP-2026-0462 | SIM-EDR-300 | ok |
| windows | Unpopular rsync process execution | TTP-2026-0463 | SIM-EDR-301 | ok |
| windows | Unprivileged process opened a registry hive | TTP-2026-0464 | SIM-EDR-302 | ok |
| windows | Unsigned and unpopular process performed a DLL injection | TTP-2026-0465 | SIM-EDR-303 | ok |
| windows | Unsigned and unpopular process performed an injection | TTP-2026-0466 | SIM-EDR-304 | ok |
| windows | Unsigned DLL Hijack into a Microsoft process | TTP-2026-0467 | SIM-EDR-305 | ok |
| windows | Unsigned DLL Side-Loading | TTP-2026-0468 | SIM-EDR-306 | ok |
| windows | Unsigned process creates a scheduled task via file access | TTP-2026-0469 | SIM-EDR-307 | ok |
| windows | Unsigned process injecting into a Windows system binary with no command line | TTP-2026-0470 | SIM-EDR-308 | ok |
| windows | Unsigned process running from a temporary directory | TTP-2026-0471 | SIM-EDR-309 | ok |
| windows | Untrusted process contacted LLM API | TTP-2026-0472 | SIM-EDR-310 | ok |
| windows | Unusual ADFS Remote Synchronization network connections from non-ADFS server | TTP-2026-0473 | SIM-EDR-311 | ok |
| windows | Unusual AWS credentials creation | TTP-2026-0474 | SIM-EDR-312 | ok |
| windows | Unusual AWS user added to group | TTP-2026-0475 | SIM-EDR-313 | ok |
| windows | Unusual Azure AD sync module load | TTP-2026-0476 | SIM-EDR-314 | ok |
| windows | Unusual cloud Instance Metadata Service (IMDS) access | TTP-2026-0477 | SIM-EDR-315 | ok |
| windows | Unusual compressed file password protection | TTP-2026-0478 | SIM-EDR-316 | ok |
| windows | Unusual DB process spawning a shell | TTP-2026-0479 | SIM-EDR-317 | ok |
| windows | Unusual internal access to network device management interface | TTP-2026-0480 | SIM-EDR-318 | ok |
| windows | Unusual Kubernetes dashboard communication from a pod | TTP-2026-0481 | SIM-EDR-319 | ok |
| windows | Unusual Lolbins Process Spawned by InstallUtil.exe | TTP-2026-0482 | SIM-EDR-320 | ok |
| windows | Unusual process accessed the PowerShell history file | TTP-2026-0483 | SIM-EDR-321 | ok |
| windows | Unusual process executed by AWS Systems Manager | TTP-2026-0484 | SIM-EDR-322 | ok |
| windows | Unusual Process Spawned by Nginx in Ingress-Nginx pod | TTP-2026-0485 | SIM-EDR-323 | ok |
| windows | Unusual weak authentication by user | TTP-2026-0486 | SIM-EDR-324 | ok |
| windows | Weakly-Encrypted Kerberos TGT Response | TTP-2026-0487 | SIM-EDR-325 | ok |
| windows | Weakly-Encrypted Kerberos Ticket Requested | TTP-2026-0488 | SIM-EDR-326 | ok |
| windows | Web server CGO executed an uncommon process | TTP-2026-0489 | SIM-EDR-327 | ok |
| windows | Windows CGO, actor and action processes with anomalous characteristics | - | - | genfail |
| windows | Windows CGO, actor process and action module with anomalous characteristics | TTP-2026-0490 | SIM-EDR-328 | ok |
| windows | Windows LOLBIN executable connected to a rare external host | TTP-2026-0491 | SIM-EDR-329 | ok |
| windows | WmiPrvSe.exe Rare Child Command Line | TTP-2026-0492 | SIM-EDR-330 | ok |
| windows | Wscript/Cscript loads .NET DLLs | TTP-2026-0493 | SIM-EDR-331 | ok |
| windows | Wsmprovhost.exe Rare Child Process | TTP-2026-0494 | SIM-EDR-332 | ok |
| linux | Multiple uncommon SSH Servers with the same Server host key | TTP-2026-0495 | SIM-EDR-333 | ok |
| linux | Rare process created an SSH session to an uncommon cloud resource | TTP-2026-0496 | SIM-EDR-334 | ok |
| linux | Rare process created an SSH session to an uncommon external host | TTP-2026-0497 | SIM-EDR-335 | ok |
| linux | Rare SSH Session | TTP-2026-0498 | SIM-EDR-336 | ok |
| linux | SSH authentication brute force attempts | TTP-2026-0499 | SIM-EDR-337 | ok |
| linux | Sudoedit Brute force attempt | TTP-2026-0500 | SIM-EDR-338 | ok |
| linux | Suspicious container orchestration job | TTP-2026-0501 | SIM-EDR-339 | ok |
| linux | Suspicious container reconnaissance activity in a Kubernetes pod | TTP-2026-0502 | SIM-EDR-340 | ok |
| linux | Suspicious container runtime connection from within a Kubernetes Pod | TTP-2026-0503 | SIM-EDR-341 | ok |
| linux | Suspicious sshpass command execution | TTP-2026-0504 | SIM-EDR-342 | ok |
| linux | Uncommon reverse SSH tunnel to external domain/ip | TTP-2026-0505 | SIM-EDR-343 | ok |
| linux | Uncommon SSH session was established | TTP-2026-0506 | SIM-EDR-344 | ok |
| linux | Unusual SSH Activity | TTP-2026-0507 | SIM-EDR-345 | ok |
| linux | Unusual SSH activity that resembles SSH proxy | TTP-2026-0508 | SIM-EDR-346 | ok |

# Tray Build Server

Deployment URLs (**Admin → Tray → Deployment URLs**) give each company a
Windows setup `.exe` and `.msi` with the portal address and the company's
install token already built in. A URL can also offer an encrypted `.ppkg`
provisioning package for new Windows computers. MyPortal queues the builds
and a Windows machine running the **MyPortal build agent** produces them
using WiX and, for provisioning, Windows Configuration Designer.

```
MyPortal ──(queued build: release tag, portal URL, token)──▶ build agent
   ▲                                                             │
   └──────────(signed setup .exe + .msi, uploaded over HTTPS)────┘
```

The agent:

1. asks MyPortal for the next build every two minutes;
2. checks out that tray release tag from GitHub and builds the tray binaries
   from source the same way the *Build MSI* workflow does (cached per release);
3. builds `myportal-tray.msi` with the company's settings as property
   defaults, then wraps it in a Burn setup bundle;
4. signs the binaries, the MSI, the bundle engine and the bundle with your
   code-signing certificate;
5. if provisioning is enabled, embeds the signed MSI in an encrypted `.ppkg`
   with the local administrator and optional Wi-Fi, PC name and directory
   join settings;
6. uploads the files and marks the build complete, or reports the error so
   it shows on the Deployment URLs page.

Builds are queued when a deployment URL is created, when you press
**Rebuild**, and automatically when MyPortal caches a newer tray release.

The GitHub release workflows are not involved: the public MSI, pkg and DMG
are built exactly as before, and the company-specific installers never leave
your build server and MyPortal.

## 1. Prepare the server

Any always-on Windows machine works: Windows Server 2022 or later, or
Windows 11. It needs outbound HTTPS to your MyPortal server, `github.com`,
`proxy.golang.org`, `registry.npmjs.org` and `api.nuget.org`. It does not need any inbound ports.

Install, as an administrator:

| Tool | Install |
| --- | --- |
| PowerShell 7.2+ | `winget install Microsoft.PowerShell` |
| .NET SDK 8+ | `winget install Microsoft.DotNet.SDK.8` |
| Git | `winget install Git.Git` |
| Go (the version in `tray/go.mod` or later) | `winget install GoLang.Go` |
| Node.js 20 LTS | `winget install OpenJS.NodeJS.LTS` |

Install them machine-wide so the agent, which runs as SYSTEM, can find them.

For provisioning packages, also install the **Configuration Designer**
component of the [Windows ADK](https://learn.microsoft.com/en-us/windows-hardware/get-started/adk-install).
The agent needs its `ICD.exe` command-line tool, not just a desktop shortcut.
It looks in the ADK's usual installation location; use `-IcdPath` when
installing the agent if ICD is elsewhere. WCD is optional for MSI/EXE builds.
Agents without it leave provisioning jobs queued so a capable agent can
claim them. Update older agents before enabling provisioning.

The install script adds WiX v7 itself. WiX v7 requires accepting the
FireGiant [Open Source Maintenance Fee EULA](https://docs.firegiant.com/wix/osmf/),
which the agent does with `wix eula accept wix7` on each run, the same EULA
the *Build MSI* workflow accepts with `-acceptEula wix7`.

## 2. Import the code-signing certificate

Use the same certificate as the *Build MSI* workflow so the per-company
installers carry the same publisher as the released MSI.

1. Copy the `.pfx` to the server.
2. Run `certlm.msc` → **Personal** → **Certificates** → *All Tasks* →
   **Import**. Mark the key as **not exportable**.
3. Note the certificate's **Thumbprint** (Details tab).

For a hardware token or cloud HSM, install the vendor's CSP/KSP so the
certificate appears in **Local Machine → Personal** with a private key.

## 3. Create an API key in MyPortal

**Admin → API keys → New key**, then:

* **Allowed IPs**: the build server's public IP.
* **Permissions**: only these paths.

| Path | Methods |
| --- | --- |
| `/api/tray/build-agent/jobs/claim` | POST |
| `/api/tray/build-agent/jobs/{build_id}/artifacts/{kind}` | PUT |
| `/api/tray/build-agent/jobs/{build_id}/complete` | POST |
| `/api/tray/build-agent/jobs/{build_id}/fail` | POST |

The claim response contains company install tokens and, for provisioning
jobs, the administrator and Wi-Fi credentials. Keep the key restricted to
these paths and the server's IP. The API uses HTTPS; the build work folder
is restricted to SYSTEM and Administrators and transient files are deleted
after each job.

## 4. Allow large uploads through your reverse proxy

The agent uploads each installer in 8 MB parts, so it gets through the
default 15 MB limit in `deploy/nginx/` and proxies such as Cloudflare without
changes. Agents installed before part uploads send each installer in one
request, which needs a larger limit. The nginx configs in `deploy/nginx/`
already raise it for `/api/tray/build-agent/`; if you use your own proxy and
an older agent, add the same:

```nginx
location ^~ /api/tray/build-agent/ {
  client_max_body_size 1g;
  proxy_request_buffering off;
  proxy_read_timeout 600s;
  proxy_send_timeout 600s;
  proxy_pass http://myportal_app;
  proxy_set_header Host $host;
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

## 5. Install the agent

Copy the `tray/build-agent` folder from the repository to the server, open
PowerShell 7 **as Administrator** in it and run:

```powershell
.\Install-MyPortalBuildAgent.ps1 `
  -PortalUrl https://portal.example.com `
  -ApiKey (Read-Host 'MyPortal API key') `
  -CertificateThumbprint '0123456789ABCDEF0123456789ABCDEF01234567' `
  -TimestampServer http://timestamp.digicert.com `
  -RunNow
```

This installs WiX into `C:\Program Files\MyPortalBuildAgent\tools`, writes
`C:\ProgramData\MyPortalBuildAgent\config.json` (readable only by SYSTEM and
Administrators) and registers the **MyPortal Build Agent** scheduled task,
which runs as SYSTEM every two minutes.

Optional parameters: `-WorkDir` (default `C:\MyPortalBuild`), `-GitHubRepo`
(for a fork), `-WixVersion` (default `7.*`) and `-IcdPath` (full path to the
ADK's `ICD.exe`). Run the script again from the latest complete
`tray/build-agent` folder to update the agent and its provisioning helper,
change settings or update WiX. Keep all helper scripts together.

## 6. Check it works

1. Create a deployment URL in MyPortal. Its **Windows installer** column
   shows *Queued*, then *Building*, then *Ready* with the release tag.
2. Open the URL and download **Download for Windows**. The file's
   *Properties → Digital Signatures* tab should show your certificate.
3. Agent activity is logged to `C:\MyPortalBuild\agent.log`. When MyPortal has
   nothing to build, the agent logs *No builds to run:* with the reason, such
   as no cached tray release or no active deployment URLs.

## Provision new Windows computers

When creating a URL, open **Provision new Windows computers** and enable
**Build a Windows provisioning package (.ppkg)**. Existing URLs continue
to provide their normal installers. Provisioning settings are fixed for a
URL; create a new URL to change them.

The initial scope is **Windows 11 Pro, Enterprise and Education, x64**. Home
and ARM64 are outside this workflow. The package can leave directory join
for later, join **Microsoft Entra ID (Azure AD)**, or join a **local Active
Directory domain**.
The [Accounts CSP](https://learn.microsoft.com/en-us/windows/client-management/mdm/accounts-csp)
used for local account provisioning requires a supported Windows edition.

Configure:

| Setting | Behavior |
| --- | --- |
| Local administrator | Always `ProvisioningAdmin`. Supply a 12–127 character password using at least three of uppercase, lowercase, digits and symbols, without the account name. Store it securely; the portal does not reveal this password later. |
| Wi-Fi | Optional SSID, at most 32 UTF-8 bytes. A supplied password configures WPA2-Personal and must be 8–63 printable ASCII characters; a blank password with an SSID configures an open network. Leave both blank to use Ethernet or configure Wi-Fi separately. Enterprise Wi-Fi is outside this workflow. |
| PC name template | Optional letters, digits and hyphens with one `%RAND:n%` macro, where `n` is 1–15. The expanded name must fit in 15 characters. For example, `MP-%RAND:8%`. AD join requires one random token; a blank name defaults to `MP-%RAND:8%` in that mode. Other modes keep the existing name when blank. Serial-number macros are not supported by this form. |
| Directory join | Choose one of **Leave directory join for later**, **Microsoft Entra ID (Azure AD)**, or **Local Active Directory domain**. Each option still creates `ProvisioningAdmin`. Only the selected directory's credentials are submitted. |

### Microsoft Entra ID (Azure AD) join

In Windows Configuration Designer, use the desktop provisioning wizard's
**Enroll in Azure AD** option and **Get Bulk Token**. Sign in with a suitably
authorised tenant account and generate the bulk token, then save the project.
Copy the saved project's **Accounts → Azure → BPRT** value into MyPortal's
**WCD bulk enrolment token (BPRT)** field. Do not upload the entire project:
it can contain other credentials. An ordinary access token, user password
or client secret is not a BPRT. MyPortal uses the supplied token and does
not generate it or refresh it.

The optional authority defaults to `https://login.microsoftonline.com/common`.
Use the matching Microsoft public, US Government or China cloud authority
with a tenant path. Accepted hosts are `login.microsoftonline.com`,
`login.microsoftonline.us` and `login.chinacloudapi.cn`; ports, queries and
fragments are not accepted.

The PC needs internet access during OOBE. The tenant account used to obtain
the token needs permission to create bulk enrolment packages, and applicable
Microsoft licensing and tenant policies still apply. WCD bulk tokens have
a maximum lifetime of 180 days. A package rebuild does not extend or renew
the embedded token: create a new URL with a fresh BPRT before it expires.
See Microsoft's [bulk enrolment package guidance](https://learn.microsoft.com/en-us/intune/device-enrollment/windows/create-bulk-package).

This joins the device to Entra ID. Intune/MDM enrolment additionally depends
on the tenant's automatic MDM enrolment settings; the package does not
configure those settings or guarantee Intune enrolment.

### Local Active Directory join

Supply the domain's DNS name, such as `corp.example.com`, a join account
as `CORP\provisioning` or `provisioning@corp.example.com`, and that account's
password. Optionally give the target computer OU's distinguished name,
such as `OU=Workstations,DC=corp,DC=example,DC=com`; blank uses the domain's
default computer location. The join account should have only the delegated
permissions needed for that location.

This is an online domain join. During OOBE, the PC must reach a domain
controller and resolve the domain using working AD DNS. Configuring Wi-Fi
does not supply a VPN, and the package does not create an offline-domain-join
blob. AD mode requires a PC name with a random token to avoid a reused
static name; if blank, MyPortal uses `MP-%RAND:8%`. See Microsoft's
[WCD account settings](https://learn.microsoft.com/en-us/windows/configuration/wcd/wcd-accounts).

### API directory settings

The deployment-link creation API accepts these settings inside
`provisioning.directory_join`, using the `mode` discriminator shown below.
Omit `directory_join`, or set it to `null`, to leave directory join for
later. Supply only the fields for the selected mode.

| Mode | Fields inside `directory_join` |
| --- | --- |
| `entra` | `mode: "entra"`, required `bulk_token` (WCD BPRT), optional `authority` (defaults to `https://login.microsoftonline.com/common`). |
| `active_directory` | `mode: "active_directory"`, required `domain_name`, `username` and `password`, optional `organizational_unit`. |

The HTML form uses `provisioning_join_mode` values `none`, `entra` and
`active_directory` with mode-specific inputs. Directory credentials are included in the encrypted
provisioning configuration and are not returned by link listings or shown
on the public download page.

### Package behavior and passwords

The package creates the account before hiding the interactive OOBE flow and
installs the embedded company MSI silently as SYSTEM. It needs no installer
download during initial setup. The service enrols once the PC can reach
MyPortal; the tray icon runs in the user's session after sign-in. Other
provisioning can follow later.

WCD encrypts each package and generates a separate **package unlock
password**. After the build is ready, expand **Windows provisioning
package** on the admin URL row and select **Show package password**. This
admin-only action retrieves the password for the current build; it is not
included on the public download page. Share it separately from the link.
Rebuilding generates a new package password, so keep a downloaded package
paired with the password for that build. Old passwords are not retained in
the admin UI after a newer build replaces them.

The provisioning settings and package passwords are encrypted at rest on
MyPortal. Keep both the URL and package private. A package can configure
multiple computers using the same bootstrap administrator password and,
if configured, directory join credentials. Rotate the local password per
device or remove the account when the remaining setup is complete. Windows
account password policy still applies. Protect bulk tokens and domain join
credentials as well as the local account password.

The MSI embedded in the package is signed. The `.ppkg` uses WCD encryption;
do not assume it is signed or that Windows will trust its publisher
automatically. Package consent is part of the user flow.

The current agent recognises the generated password in **English ICD
output**. Use an English ADK/WCD installation. If output is unfamiliar or
the password cannot be extracted, the build fails without publishing an
unusable package; raw ICD output is not sent to the portal's build log.

### End-user steps

1. Download **Provisioning package (.ppkg)** from the URL on another computer
   and copy the file to the root of a USB drive.
2. Insert the drive at the new or reset PC's first Windows setup screen.
   If provisioning does not start, press the **Windows key five times**.
   Use a network with internet access for Entra join, or working domain
   controller connectivity and AD DNS for local domain join.
3. Select the package if prompted, verify it with IT, accept the provisioning
   confirmation and enter the **package unlock password** supplied by IT.
4. Leave the drive connected until provisioning finishes. Windows may restart.
   Sign in with the separate **ProvisioningAdmin account password** from IT.

This is a USB-assisted bootstrap. A Download URL alone cannot run on a PC
before it has completed setup. Microsoft's
[package application instructions](https://learn.microsoft.com/en-us/windows/configuration/provisioning-packages/provisioning-apply-package)
describe the OOBE prompts. Supported
[provisioning commands](https://learn.microsoft.com/en-us/windows/configuration/provisioning-packages/provisioning-script-to-install-app)
must be silent and finish within OOBE's 30-minute limit.

**Expiry and revocation only stop downloads and new MyPortal enrolment.**
A package already copied to USB can still create its local account, apply
Wi-Fi or PC naming settings, change OOBE while offline, and join the selected
directory if its credentials remain valid and the network is reachable.
Revocation does not roll back those settings, remove a local account or
unjoin a provisioned PC. Revoke or rotate directory credentials through the
directory when necessary.

### Validate before distributing

Windows builds and OOBE cannot be exercised in the Linux development
environment. Before sending a package to users, build it on the configured
Windows server and test it on a reset Windows 11 VM and representative
hardware. Check package unlock, OOBE completion, administrator sign-in,
silent MSI installation, service enrolment after connectivity is restored,
tray startup after sign-in, the optional Wi-Fi/name settings, and reboots.
For each join mode, verify directory membership after reboot and the chosen
name/OU. Also test no network, an expired BPRT, unreachable domain controller,
a wrong package password and a revoked MyPortal enrolment token.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `agent.log` repeats *Installing WixToolset.BootstrapperApplications.wixext* | Update the agent: older copies did not accept the WiX v7 EULA, so the extension install failed without logging why. Run `Install-MyPortalBuildAgent.ps1` again from the latest `tray/build-agent` folder. Any other *WiX setup failed* line in the log names the cause. |
| *wix build failed with exit code 388* (*WIX0388 … 'setup.exe' creates an insecure bundle*) | The agent is an older copy that named the bundle `setup.exe`, which WiX v7 refuses. Run `Install-MyPortalBuildAgent.ps1` again from the latest `tray/build-agent` folder, then press **Rebuild**. The bundle is now built as `MyPortal-Tray-Setup.exe`; downloads keep their `MyPortal-Tray-<Company>-Setup.exe` name. |
| Uploads fail with *413 Request Entity Too Large* | A proxy in front of MyPortal rejected the upload; MyPortal itself never answers 413 here. Run `Install-MyPortalBuildAgent.ps1` again from the latest `tray/build-agent` folder so the agent uploads in 8 MB parts, then press **Rebuild**. |
| *WiX setup failed: wix extension failed* with a NuGet error | The agent downloads the WiX Burn extension from `api.nuget.org` using its own NuGet.Config, so check the server can reach `api.nuget.org` over HTTPS (through any proxy the SYSTEM account uses). |
| Builds stay *Queued* | Check the scheduled task's last result and `agent.log`. A 401 or 403 means the API key, its IP allow list or its path permissions are wrong. |
| Only provisioning builds stay *Queued* | Update the agent and install the ADK Configuration Designer component. Check `IcdPath` in `config.json` if ICD is installed outside its usual location. An agent without WCD can still build ordinary installers. |
| Provisioning build cannot retrieve the generated encryption password | The build fails instead of publishing a package without a usable password. Check the installed WCD version and update the agent; do not send ICD output or project XML to users, as they contain credentials. |
| *Failed: Release … predates deployment URL support* | The cached tray release was tagged before this feature. Publish a new tray release, then press **Rebuild**. |
| *Failed: go build / npm ci failed* | Check Go and Node.js are installed machine-wide and the server can reach `proxy.golang.org` and `registry.npmjs.org`. Delete the release's folder under `C:\MyPortalBuild\payloads` to force a clean build. |
| *Failed: Code-signing certificate … was not found* | Import the certificate into **Local Machine → Personal** with its private key and check the thumbprint in `config.json`. |
| Upload fails with 413 | Raise the proxy upload limit as in step 4. |
| A build stopped half way | Builds that do not report back within two hours are handed out again automatically. |

Failed builds are not retried for the same release; press **Rebuild** after
fixing the cause. Expired and revoked deployment URLs are not built, and
their installers stop downloading because the link no longer resolves.
Already downloaded provisioning packages retain their local setup behavior
as described above.

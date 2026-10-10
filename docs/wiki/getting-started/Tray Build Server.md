# Tray Build Server

Deployment URLs (**Admin → Tray → Deployment URLs**) give each company a
Windows `setup.exe` and `.msi` with the portal address and the company's
install token already built in. WiX can only build those on Windows, so
MyPortal queues the builds and a Windows machine running the **MyPortal build
agent** produces them.

```
MyPortal ──(queued build: release tag, portal URL, token)──▶ build agent
   ▲                                                             │
   └──────────(signed setup.exe + .msi, uploaded over HTTPS)─────┘
```

The agent:

1. asks MyPortal for the next build every two minutes;
2. downloads `myportal-tray-windows-payload.zip` for that tray release from
   GitHub Releases (the signed tray binaries and WiX sources published by the
   *Build MSI* workflow);
3. builds `myportal-tray.msi` with the company's settings as property
   defaults, then wraps it in a `setup.exe` Burn bundle;
4. signs the MSI, the bundle engine and the bundle with your code-signing
   certificate;
5. uploads both files and marks the build complete, or reports the error so
   it shows on the Deployment URLs page.

Builds are queued when a deployment URL is created, when you press
**Rebuild**, and automatically when MyPortal caches a newer tray release.

## 1. Prepare the server

Any always-on Windows machine works: Windows Server 2022 or later, or
Windows 11. It needs outbound HTTPS to your MyPortal server, `github.com`
and `api.nuget.org`. It does not need any inbound ports.

Install, as an administrator:

| Tool | Install |
| --- | --- |
| PowerShell 7.2+ | `winget install Microsoft.PowerShell` |
| .NET SDK 8+ | `winget install Microsoft.DotNet.SDK.8` |

The install script adds WiX v7 itself.

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

The claim response contains company install tokens, so keep the key
restricted to these paths.

## 4. Allow large uploads through your reverse proxy

The tray installers are larger than the default 15 MB upload limit. The
nginx configs in `deploy/nginx/` already raise the limit for
`/api/tray/build-agent/`; if you use your own proxy, add the same:

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
(for a fork) and `-WixVersion` (default `7.*`). Run the script again to
change settings or update WiX.

## 6. Check it works

1. Create a deployment URL in MyPortal. Its **Windows installer** column
   shows *Queued*, then *Building*, then *Ready* with the release tag.
2. Open the URL and download **Download for Windows**. The file's
   *Properties → Digital Signatures* tab should show your certificate.
3. Agent activity is logged to `C:\MyPortalBuild\agent.log`.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Builds stay *Queued* | Check the scheduled task's last result and `agent.log`. A 401 or 403 means the API key, its IP allow list or its path permissions are wrong. |
| *Failed: Could not download myportal-tray-windows-payload.zip* | The cached tray release predates the build agent. Publish a new tray release, wait for the *Build MSI* workflow, then press **Rebuild**. |
| *Failed: Code-signing certificate … was not found* | Import the certificate into **Local Machine → Personal** with its private key and check the thumbprint in `config.json`. |
| Upload fails with 413 | Raise the proxy upload limit as in step 4. |
| A build stopped half way | Builds that do not report back within two hours are handed out again automatically. |

Failed builds are not retried for the same release; press **Rebuild** after
fixing the cause. Revoking a deployment URL stops its builds, and its old
installers stop downloading because the link no longer resolves.

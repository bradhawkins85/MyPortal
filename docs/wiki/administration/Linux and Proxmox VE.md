# Linux and Proxmox VE

The MyPortal tray runs on Linux desktops and headless Linux servers, including Proxmox VE hosts.
It enrols the device, keeps it checked in and installs the MyPortal RMM agent, so Linux devices get
scripts, checks, inventory and the asset page's RMM actions like Windows and macOS devices.

## Install

1. Create an install token under **Admin → Tray → Install tokens** (or on the company's tray
   settings page so the device joins that company).
2. On the device, as root:

   ```sh
   URL='https://portal.example.com'
   TOKEN='<install token>'
   curl -fsSL "$URL/static/tray/myportal-tray-linux.tar.gz" | tar -xz -C /tmp
   /tmp/myportal-tray/install.sh --url "$URL" --token "$TOKEN"
   ```

   The package is cached on MyPortal by the **Update Tray Icon Installer** scheduled task from the
   release's `myportal-tray-linux.tar.gz`. It works on amd64 and arm64 with systemd.

   With a company deployment URL (**Admin → Tray → Deployment URLs**) there is no token to copy:
   run `curl -fsSL '<deployment URL>/install.sh' | sudo bash`, adding `-s -- --headless` after
   `bash` for servers.

3. Install the RMM agent from the asset page (**Install RMM agent**), or switch on **Install the RMM
   agent automatically** for the company.

## Desktops and servers

| | Desktop | Server (headless) |
|---|---|---|
| Chosen when | the computer boots to a graphical login, or `--desktop` | otherwise, or `--headless` |
| Tray icon and menu | Yes, in each user's session (XDG autostart) | No |
| Chat, notifications, ticket form | Yes (`notify-send`, `zenity`/`kdialog`, the browser) | Logged only |
| Check-ins, configuration, commands, network scans, self-updates | Yes | Yes |
| RMM agent install and updates | Yes | Yes |

GNOME needs the AppIndicator extension (`gnome-shell-extension-appindicator`) to show tray icons;
KDE, Cinnamon, XFCE, MATE and Budgie show them already. The icon appears the next time each user
signs in.

## Proxmox VE

Install the tray headless on each node. Once the RMM agent is installed the asset's Hardware page
gets a **Proxmox VE** section (version, cluster and quorum, nodes, every VM and container, storage,
ZFS pools, recent backup jobs, subscription and pending updates), and **Proxmox VE** checks can alert
on a stopped guest, full storage, a degraded ZFS pool, a failed backup, lost quorum or an offline
node. See [RMM Inventory](RMM%20Inventory.md) and [RMM Checks](RMM%20Checks.md). Scripts run as root
with Bash, so `pvesh`, `qm`, `pct` and `apt` can be used from the Scripts page.

## Updates and removal

Tray self-updates need a **Linux** entry under **Admin → Tray → Versions** (versions for **All**
platforms carry a Windows or macOS installer and are never offered to Linux). The update runs
`install.sh --upgrade`, which keeps the settings in `/etc/myportal-tray/tray.env`, the enrolment and
the desktop or headless choice.

Remove the tray with `/opt/myportal-tray/uninstall.sh`. Add `--with-rmm` to remove the RMM agent too
and `--purge` to forget the enrolment and settings.

Logs: `journalctl -u MyPortalTrayService`, `/opt/myportal-tray/logs/service.log` and, for the
desktop UI, `~/.local/state/myportal-tray/ui.log`.

# RMM Vulnerabilities

**RMM > Vulnerabilities** (`/rmm/vulnerabilities`) lists the third-party apps on a company's
devices that have known security vulnerabilities (CVEs), worst first, and tells you which version
fixes them. It uses the software each device's RMM agent reports in its daily inventory, CVE data
from the US National Vulnerability Database (NVD) and CISA's *Known Exploited Vulnerabilities*
(KEV) catalogue.

## Reading the page

- The counters show open CVEs that are **actively exploited** (on CISA's KEV list), **critical**,
  **high** and **medium and low**, and how many devices are affected.
- Each vulnerable app is a card: its worst severity, how many CVEs are open, the devices and
  versions it is installed at, and **Update to X or later**, the version that fixes every CVE
  listed. Open a card to see each CVE with its CVSS score, a summary, a link to NVD, and for
  exploited ones CISA's required action. Exploited CVEs and ransomware use are flagged.
- Search by app or CVE, or show only actively exploited, critical, or high-and-critical apps.
- Each asset page has a **Vulnerabilities** section for that device.

## Acting on it

- **Raise a ticket** opens a ticket for the company with the app, affected devices (linked to the
  ticket), the fix and the worst CVEs. Priority is high when a CVE is exploited or critical.
- **Accept risk** on one CVE, or **Accept risk for all its CVEs**, records why (for example, the app
  only runs on an isolated kiosk) and an optional review date. Accepted CVEs stay listed as
  accepted, not open, and reopen when the exception is removed or expires.
- Updating the app (for example from **Patch management**) resolves its findings when the device
  next reports its software.

## What is and isn't covered

- Apps are matched to NVD products by name. A built-in list covers common apps (Chrome, Firefox,
  Edge, 7-Zip, Adobe Acrobat Reader, Notepad++, VLC, PuTTY, WinRAR, WinSCP, FileZilla, Wireshark,
  TeamViewer, AnyDesk, Node.js, Git, KeePass, VMware Tools, Citrix Workspace, LibreOffice, Foxit
  PDF Reader, iTunes and Docker Desktop). Super admins can add more.
- Windows and Microsoft Office are covered by the **Windows updates** section instead.
- Linux distribution packages (dpkg, RPM, pacman) are left out: distributions backport security
  fixes without changing the version number, so version matching would flag patched packages.
- An app with no usable version number is never flagged.

## Sync and mappings (super admins)

**Sync and mappings** (`/rmm/vulnerabilities/settings`) shows when CISA's list and NVD were last
downloaded and devices last matched, and any errors. Data syncs once a day; **Sync now** starts it
within a minute in the background. The first download of each product fetches all its CVEs; later
ones fetch only CVEs changed since.

Set `NVD_API_KEY` ([request one](https://nvd.nist.gov/developers/request-an-api-key)) to raise
NVD's rate limit. Without it, requests are spaced about 6.5 seconds apart.

**Unmapped software** lists the most installed programs no mapping covers. **Map** opens the
editor: choose whether the program's name *is exactly*, *starts with* or *contains* the text, then
**Find in NVD** to pick the product (vendor and product). Your mappings are checked before the
built-in ones. Saving a mapping requests a sync.

## Reports and status strips

The *Vulnerabilities* category of the [Reporting Catalogue](Reporting%20Catalogue.md#vulnerabilities)
has status strips for Reports and the Dashboard (NVD and CVE catalogue totals and CVE detections),
plus reports of assets by vulnerability count, every detection and CVEs by affected devices. Copies
limited to devices online in the last 30 or 90 days, or offline for over 90 days, show whether
detections apply to devices still in use. Each has a company scoped copy.

## API

| Method | Path | Who |
| --- | --- | --- |
| `GET` | `/api/rmm/vulnerabilities` | RMM access, current company |
| `GET` | `/api/rmm/assets/{asset_id}/vulnerabilities` | RMM access |
| `GET`/`POST` | `/api/rmm/vulnerabilities/mappings` | Super admins |
| `PUT`/`DELETE` | `/api/rmm/vulnerabilities/mappings/{id}` | Super admins |
| `POST` | `/api/rmm/vulnerabilities/sync` | Super admins |
| `GET` | `/api/rmm/vulnerabilities/cpe-search?q=` | Super admins |

# RMM Inventory

Devices with the [RMM agent](RMM%20Scripts.md) send MyPortal a snapshot of their hardware and
installed software. Open an asset and choose **Hardware & software** to see it. Technicians with
the `menu.rmm_scripts` permission see the section; those who can run scripts can also press
**Refresh now**.

## What is collected

| | Windows | macOS | Linux |
|---|---|---|---|
| Maker, model, serial number, BIOS / firmware | WMI | `system_profiler` | `/sys/class/dmi` |
| Operating system, version and build | WMI and the registry (for example 23H2, build 22631.4317) | `sw_vers` | `/etc/os-release` and the kernel version |
| Processor, cores and threads | WMI | `sysctl` | `/proc/cpuinfo` |
| Memory, and each module | WMI | `sysctl` | `/proc/meminfo` |
| Volumes (size, free, file system) and drives | WMI and `Get-PhysicalDisk` | the startup volume | mounted file systems and `/sys/block` |
| Graphics | WMI | `system_profiler` | – |
| Network adapters with an address | WMI | Go's network list | Go's network list |
| Domain and last boot | WMI | last boot | NIS domain and last boot |
| Installed software | the registry's uninstall entries, for the machine and each signed-in user | applications outside `/System` | `dpkg`, `rpm` or `pacman` packages |

On Windows, software that hides itself from **Apps & features** (system components and updates) is
left out; installed and missing Windows updates have their own **Windows updates** section (see
[RMM Patch Management](RMM%20Patch%20Management.md#device-windows-updates)). A part that cannot be read (for example a broken WMI class) is skipped; the rest is still
sent and the section lists what failed under **Some details could not be read**. At most 5000
programs are kept per device.

## When devices send it

The agent sends a snapshot shortly after its service starts, then asks MyPortal every two minutes
whether a new one is due. One is due when the last is more than 24 hours old, or when a technician
pressed **Refresh now** on the asset page since the last one arrived. So a refresh normally shows up
within a few minutes of the device being online; the section says when a refresh is still waiting.

Only the latest snapshot is kept, in MyPortal's main database (`rmm_device_inventory` and
`rmm_device_software`). It is removed with the agent.

Each snapshot also populates the asset's **Network interfaces** section and the network map
with the reported adapter names, MAC addresses and IP addresses. Existing installations populate
on the next inventory snapshot; **Refresh now** requests one sooner. Adapters match existing
interfaces by MAC address or name, so refreshes preserve documented links, names, kinds and radio
settings. An assigned IPAM address takes precedence over the observed device address; otherwise
the first valid IPv4 address (or IPv6 if there is no IPv4 address) is shown. All reported addresses
remain available in hardware inventory. Interfaces missing from a later snapshot are retained,
since disconnected adapters may not be reported by the device.

## Picking devices by inventory

Snapshots are also there to choose which devices a script, schedule or check runs on.
`app.services.rmm_inventory` describes a condition as a field, an operator and a value, and every
condition must match:

| Field | Operators | Example |
|---|---|---|
| RAM (GB), total disk (GB), system disk free (GB), CPU cores, logical processors, days since boot | is at least, is at most, is below, is above, is, is not | RAM is at least 16 |
| Operating system, manufacturer, model, CPU, domain, architecture | is, is not, contains, does not contain, starts with | Operating system contains Windows 11 |
| OS version, OS build | is, is not, starts with, is at least, is below | OS build is at least 22000 |
| Software | is installed, is not installed, is installed below version, is installed at version or above | Google Chrome is not installed; Google Chrome is installed below version 120 |

Notes:

- Memory is rounded to the nearest GB before comparing, because a 16 GB device reports a little
  less than 16.
- A software name matches any installed program whose name contains it, ignoring case. Use `*` and
  `?` as wildcards for an exact pattern, for example `Mozilla Firefox*`. For the version operators
  the value is the name, a space, then the version.
- A device that has not sent a snapshot yet matches no condition, not even "is not installed", so
  nothing runs on a device MyPortal knows nothing about.

## API

- Agents use `GET /api/rmm/agent/inventory/config` (`{"due": true|false, "poll_seconds": 120}`)
  and `POST /api/rmm/agent/inventory`.
- `GET /api/rmm/assets/{id}/inventory` returns an asset's latest snapshot and software list for the
  current company; `POST /rmm/inventory/assets/{id}/refresh` asks the device for a new one.

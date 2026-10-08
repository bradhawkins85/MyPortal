# Network Map

The **Network map** (left menu → Assets & Network → Network map, `/network-map`) draws a
company's network from what is already documented in MyPortal:

- **Racks** – racked equipment and the patching between rack ports.
- **IPAM** – the addresses (and subnets) each asset holds.
- **Assets** – every device, shown with the icon for its asset type, plus the network and
  radio interfaces documented on devices outside racks and the links between them.

The map has two layouts:

- **Topology** (default) – laid out like a controller's topology view. The internet (or,
  without a WAN link, the modem/router/firewall) sits on the left and every device is placed
  one column to the right of the device it connects through, so each link runs left to right
  as a smooth curve. Wireless links are dashed, with their frequency, distance and signal
  under the link and port names above it.
- **Sites and racks** – devices grouped into boxes by **site** (a rack's location, or the
  asset's location) and, inside a site, by **rack**, with devices outside racks laid out in
  rows from the internet edge down to endpoints.

On screen the map follows MyPortal's dark theme; exports are drawn on white for printing.

## Browsing

- Drag to pan, scroll (or `+`/`-`) to zoom, and **Fit** to see the whole map.
- Select a device to highlight its links and see its addresses, interfaces and links in the
  side panel. Select a linked device's name to jump to it.
- **Find a device** searches by name or IP address.
- Filters: **Layout**, **Detail**, **Site** (a site plus the devices it links to), **Device types**,
  **Show subnets**, **Hide devices not linked to other devices**, **Include undocumented
  devices** and **Ignore rack grouping** (sites layout).
- **Hide devices not linked to other devices** is on by default, so the map shows only
  devices with at least one link (belonging to a subnet does not count). Untick it to add
  them back; in the topology layout they are listed underneath under *Not linked to other
  devices*. Exports follow the same setting.

Routers, firewalls, switches, wireless devices and servers always appear. Computers,
printers, phones and similar devices appear once they are documented on the network (racked,
given an IP address or interface, or linked); tick **Include undocumented devices** to show
every asset.

## Exporting

**Export** offers PDF, PNG and SVG with the same options as browsing, including the layout:

| Level of detail | Shows | PDF adds |
| --- | --- | --- |
| Overview | Icons, names and links | – |
| Standard | Types, IP addresses, port names, wireless frequency/distance/signal | A device list |
| Detailed | Every interface, radio settings (mode, frequency, width, SSID, azimuth), serial and OS | A full inventory with interfaces and links, plus device identification images |

Choose the **device types** to include (quick picks: all, types in use, network only) and,
for PDF, the paper size. The map is scaled to fit one page in the best orientation. PNG
exports are rendered at up to 3× resolution in the browser. Exports are recorded in the
audit log.

Detailed PDF exports append identification pages for the devices visible on the map. These pages use product images attached to the rack item first, device images second, and accessible asset photos as the final fallback. Devices without an available image are omitted from the identification pages. Images are embedded in the PDF for offline use.

## Asset types

Assets use a fixed list of types, each with its own icon (modem, router, firewall, VPN
gateway, switch, patch panel, load balancer, access point, point-to-point wireless bridge,
wireless controller, server, virtualisation host, virtual machine, NAS/SAN, desktop,
laptop, thin client, tablet, mobile phone, printer/MFP, scanner, IP phone, phone system,
display, conference system, IP camera, NVR, access control, UPS, PDU, IoT device, cloud
service and other).

- **Manual assets** choose their type from the list when created or edited.
- **Synchronised assets** (Tactical RMM, Syncro) are typed automatically from what the
  integration reports. Tactical RMM's *workstation*/*server* monitoring type is combined
  with the chassis (laptop vs desktop) and virtualisation flag; free-text types such as
  *Managed Printer* are matched to the list. The reported type is kept unchanged (billing
  counts still use it).
- To correct a synced asset, open it and choose a type under **Synced inventory → Asset
  type**. Sync keeps a type chosen there; choose **Automatic** to follow the integration
  again.

## Devices outside racks and wireless links

Open an asset and use **Network interfaces** to document its ports and radios:

- **Kinds:** Ethernet, SFP/fibre, WAN/internet, long-range radio, Wi-Fi radio and
  virtual/VLAN. A WAN interface connects the device to the *Internet* on the map.
- **Radio settings:** mode (point-to-point master/station, point-to-multipoint AP/station,
  Wi-Fi AP/client), frequency, channel width, SSID/link name, azimuth, transmit power and
  antenna gain.
- An interface can be tied to a documented IP address.

Then link interfaces to each other or to free rack data ports (from the asset page, or the
**Links** section of the network map), choosing the medium: copper, fibre, direct-attach,
wireless, VPN or other. Wireless links record frequency, distance and signal.

Rules: a wireless link joins two radio interfaces; radio interfaces only carry wireless links
and can carry several (point-to-multipoint); a wired port or interface carries one link; a
rack port already patched to another rack port is managed in **Racks**.

### Example: point-to-point bridge between two sites

1. Create two assets of type **Point-to-point wireless bridge**, with locations *Head office*
   and *Warehouse*.
2. On each, add `eth0` (Ethernet, with its IP) and `radio0` (Long-range radio, e.g.
   point-to-point master/station, 5800 MHz, azimuth).
3. Link Head office `eth0` to the core switch's rack port, Warehouse `eth0` to the warehouse
   switch, and the two `radio0` interfaces with medium **Wireless** (e.g. 4200 m, −54 dBm).

The map shows both sites with a dashed radio link between the bridges, labelled with its
frequency, distance and signal, and marks each radio-equipped device with an antenna badge.

## Permissions

The **Network map** role permission (Infrastructure group) controls access: *read* allows
browsing and exporting; *write* allows interfaces and links to be documented. Roles saved
before this permission existed inherit it from **Network Devices**.

## Turning the feature off

The network map is its own feature pack, `network_map`, listed under **Administration →
Feature packs**. Add it to `DISABLED_FEATURE_PACKS` (for example
`DISABLED_FEATURE_PACKS=network_map`) and restart to remove the menu entry, the
**Network interfaces** card on asset pages, the exports and every `/network-map` and
`/api/network-map` route. Documented interfaces and links stay in the database and return
when the pack is enabled again. The asset type list and icons belong to the Assets pack and
stay available. Disabling the Assets pack also hides the network map, since it is drawn from
assets.

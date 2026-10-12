# Warranty Lookup

The Warranty lookup module records each asset's warranty start and expiry
dates from the manufacturer's own free warranty service. The dates appear on
the asset's **Warranty** card, in the asset list's *Warranty end* column and on
the **Expirations** page.

Only the asset's serial number is sent, and only to its own manufacturer.

## Supported manufacturers

| Manufacturer | Service used | Credentials |
| --- | --- | --- |
| Dell (incl. Alienware) | Dell TechDirect Warranty API (`apigtwb2c.us.dell.com`) | Free. Register at [tdm.dell.com](https://tdm.dell.com) and request Warranty API access. |
| HP | HP Developer Portal Warranty API (`warranty.api.hp.com`) | Free. Create an app with the Warranty API at [developers.hp.com](https://developers.hp.com). |
| Lenovo | Lenovo's support-site warranty lookup (`pcsupport.lenovo.com`) | None. If Lenovo has issued you a Warranty API ClientID, set it to use `supportapi.lenovo.com` instead. |

ASUS, MSI, Gigabyte, Intel, Apple, Microsoft, Acer and HPE do not offer a free
automated warranty interface; their checks are behind a CAPTCHA or sign-in.
For those assets the Warranty card links to the vendor's own check page, and
technicians enter the dates by hand.

The manufacturer is taken from the RMM agent's hardware inventory, or the
motherboard manufacturer reported by Syncro or Tactical RMM.

## Setup

1. Add the credentials for the vendors you use to `.env`:

   ```
   WARRANTY_LOOKUP_DELL_CLIENT_ID=
   WARRANTY_LOOKUP_DELL_CLIENT_SECRET=
   WARRANTY_LOOKUP_HP_API_KEY=
   WARRANTY_LOOKUP_HP_API_SECRET=
   WARRANTY_LOOKUP_LENOVO_CLIENT_ID=   # optional
   ```

2. Turn on **Warranty lookup** in **Admin → Modules**.

Optional tuning:

- `WARRANTY_LOOKUP_REFRESH_DAYS` (default `30`) — how long before an asset is
  checked again.
- `WARRANTY_LOOKUP_BATCH_SIZE` (default `200`) — the most assets checked in one
  run.

## When lookups run

- Every day at 05:15 (server time), for assets not checked within the refresh
  window. Assets that have never been checked go first.
- From a scheduled task with the **Look up asset warranties** command,
  optionally limited to one company.
- On demand with **Check warranty now** on an asset's Warranty card.

Requests are spaced out to stay within the vendors' rate limits.

## Manual dates

Technicians can enter warranty dates on the Warranty card. Manual dates are
kept by integration syncs and skipped by the daily lookup. Clearing both dates
hands the asset back to the automatic lookup. Dates found by a vendor lookup
are also kept when Syncro or Tactical RMM next sync the asset.

## Lookup results

The Warranty card shows the last check and its outcome:

- **Found** — dates were recorded.
- **Not found** — the vendor did not recognise the serial number.
- **Unsupported** — the manufacturer has no free automated lookup, or is unknown.
- **Not configured** — the vendor's credentials are not set.
- **Error** — the vendor rejected the credentials, rate limited the request or
  was unavailable; the next run tries again.

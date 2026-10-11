# Tags

Tags are labels on **assets** and **companies**. They pick out the devices and customers an
automation or script should apply to, alongside the other filters (company, asset type and so
on). There is one shared list of tags for the whole portal.

## Tagging an asset

Open the asset (Assets → select an asset). The **Tags** card shows the asset's tags.

- Click **Search or create a tag**, type part of a name, and pick a tag from the list.
- If no tag matches, choose **Create tag "…"** (or press Enter) to create it and add it in one
  step. Names are matched without regard to case or extra spaces, so "server" and "Server" are
  the same tag.
- Select **×** on a tag to remove it.

Changes save straight away. Technicians need write access to **Assets** to change an asset's
tags. Tags are internal: customers never see them, in the portal or the customer preview.

The asset list shows a **Tags** column, and the asset search box also matches tag names.

## Automatic tags

MyPortal tags assets automatically from their asset type and operating system. These tags are
marked **auto** and cannot be removed by hand; they follow the asset as it changes. To keep one
off a particular device, block it (see below).

| Tag | Added to |
| --- | --- |
| Server | Servers, virtualisation hosts and storage, and virtual machines whose type or OS says server |
| Workstation | Desktop computers, thin clients and other virtual machines |
| Laptop | Laptops, notebooks and convertibles |
| Virtual machine | Assets reported as virtual machines |
| Windows / macOS / Linux | Assets running that operating system family |

Automatic tags are re-applied whenever an asset is synchronised or its type is changed, and
in a background pass shortly after start-up and every six hours. An administrator can also
select **Re-apply automatic tags** on the Tags page. A tag a technician picked by hand is never
removed by a rule.

## Blocking a tag on one device

Sometimes a rule gets a device wrong for your purposes, for example a desktop PC that is really
serving as a file server. Select **Block** on its **Workstation** chip in the asset's **Tags**
card, then add **Server** by hand. A blocked tag:

- is removed from the device and listed under **Blocked on this device**;
- is never added back by the automatic rules, at sync time or in the background pass;
- does not reach the device through its company's tags, so scripts and automations filtered by
  that tag skip it.

Select **Unblock** to lift the block; an automatic tag whose rule still matches comes straight
back. Picking a blocked tag from the search list also unblocks it. Blocks affect only that
device, and blocking and unblocking are recorded in the audit log.

## Tagging a company

Super administrators tag a company on its edit page (Companies → edit → **Tags**). When scripts
and automations filter by tag, a company's tags count for every asset of that company. For
example, tag your fully managed customers "Managed" to target all of their devices.

## Managing tags

Super administrators manage the tag list at **Tags** in the admin menu (`/admin/tags`, also linked from
the Assets page actions and each Tags card):

- Create a tag with a colour and a description.
- Rename, recolour or describe any tag. Renaming an automatic tag keeps its rule.
- Delete a tag. This removes it from every asset and company. Automatic tags cannot be deleted.

## For developers

- Tables: `tags`, `asset_tags` (with `source` of `auto` or `manual`) and `company_tags`
  (migration 466), and `asset_tag_blocks` (migration 469).
- Repository: `app/repositories/tags.py`. `list_asset_ids_with_tags(tag_ids, company_id=None,
  match_all=False)` returns the unarchived assets carrying any (or every) given tag, counting
  company tags except where the asset blocks them, for script and automation filters.
  `block_asset_tag` / `unblock_asset_tag` manage blocks.
- Rules: `app/services/tags.py` (`AUTO_TAGS`, `auto_tag_keys`).
- API: `GET /api/tags?q=`, `POST /api/tags {"name"}`, `GET`/`PUT /api/assets/{id}/tags
  {"tag_ids": [...]}`, `POST`/`DELETE /api/assets/{id}/tags/{tag_id}/block` and
  `GET`/`PUT /api/companies/{id}/tags`. A `PUT` replaces the record's hand-picked tags;
  automatic tags are unaffected. Asset responses include `blocked`, the tags blocked on it.
- Feature pack `tags`; disable it with `DISABLED_FEATURE_PACKS=tags`.

# Configuration Reference

MyPortal loads configuration from environment variables declared in a `.env`
file. The template at `.env.example` lists every supported key alongside
recommended defaults. Copy the template to `.env` (or point your process manager
at a dedicated path) and edit values as required for the deployment target.

The [Onboarding Wizard](Onboarding%20Wizard.md) (`scripts/onboarding_wizard.py`,
or `myportal-docker setup` on Docker) prompts for these values feature by
feature, and `--check` verifies an existing file.

For integration-specific guidance refer to the dedicated documentation under
`docs/`. For example, [docs/xero.md](xero.md) outlines the callback URL and
credential requirements for the Xero module.

## UI Auto Refresh

`ENABLE_AUTO_REFRESH` controls whether browser clients automatically poll the
server for new data. When set to `true`, list and dashboard views schedule
background refreshes so agents see near real-time updates without reloading the
page. Leave the flag at its default value of `false` if you prefer to refresh
manually or want to reduce background traffic for constrained environments.

The deployment helpers (`scripts/install_production.sh`,
`scripts/install_development.sh`, `scripts/upgrade.sh`, and
`scripts/restart.sh`) seed the flag into `.env` if the file was created before
the option existed. Override the value directly in `.env` or through your
process manager's secret store.

## Asset types

`ASSET_TYPE_MODE` controls the type picker on **Create asset** (`/assets/new`)
and when editing a manually created asset:

- `auto` (default) – choose from the built-in IT catalogue (routers, switches,
  laptops, printers and so on). Each type has its own icon and network map row.
- `custom` – the built-in catalogue plus any type typed into the box. Typing a
  catalogue name (for example "router") still uses the catalogue type.
- `manual` – no built-in types. Use this when MyPortal is not tracking IT
  equipment.

In `custom` and `manual` modes the box suggests the types already entered for
the company, and a typed type that differs only in case or spacing reuses the
existing spelling, so duplicates such as "Forklift" and "forklift " are not
created. Typed-in types are drawn with the generic "Other" icon on the network
map and appear under **Custom** in its type filter. Assets synchronised from
integrations always use the built-in catalogue.

## Component availability

All bundled components are available by default. Operators who need a reduced
deployment can exclude components with `DISABLED_FEATURE_PACKS` and
`DISABLED_MODULES`. See [Component Deactivation](Component%20Deactivation.md)
for valid slugs, dependency and precedence rules, examples, and the deployment
verification checklist.

# Configuration Reference

MyPortal loads configuration from environment variables declared in a `.env`
file. The template at `.env.example` lists every supported key alongside
recommended defaults. Copy the template to `.env` (or point your process manager
at a dedicated path) and edit values as required for the deployment target.

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

## Component availability

All bundled components are available by default. Operators who need a reduced
deployment can exclude components with `DISABLED_FEATURE_PACKS` and
`DISABLED_MODULES`. See [Component Deactivation](Component%20Deactivation.md)
for valid slugs, dependency and precedence rules, examples, and the deployment
verification checklist.

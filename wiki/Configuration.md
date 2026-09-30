# Configuration Reference

MyPortal loads configuration from environment variables declared in a `.env` file. The template at `.env.example` lists every supported key alongside recommended defaults. Copy the template to `.env` (or point your process manager at a dedicated path) and edit values as required for the deployment target.

For integration-specific guidance refer to the dedicated documentation. For example, [Xero Integration](Xero-Integration) outlines the callback URL and credential requirements for the Xero module.

## UI Auto Refresh

`ENABLE_AUTO_REFRESH` controls whether browser clients automatically poll the server for new data. When set to `true`, list and dashboard views schedule background refreshes so agents see near real-time updates without reloading the page. Leave the flag at its default value of `false` if you prefer to refresh manually or want to reduce background traffic for constrained environments.

The deployment helpers (`scripts/install_production.sh`, `scripts/install_development.sh`, `scripts/upgrade.sh`, and `scripts/restart.sh`) seed the flag into `.env` if the file was created before the option existed. Override the value directly in `.env` or through your process manager's secret store.

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

## Core Configuration

### Database Settings

- **DATABASE_URL** - MySQL connection string (e.g., `mysql://user:password@localhost/myportal`)
- SQLite is used as fallback when MySQL is not configured
- Dates and times are stored in UTC format in the database
- Dates and times are displayed in local timezone to users

### Security Settings

- **SESSION_SECRET** - Strong random value for session encryption (required)
- **TOTP_ENCRYPTION_KEY** - Strong random value for TOTP encryption (required)
- Sessions use secure cookies with sliding expiration
- CSRF protection is automatically applied on authenticated state-changing requests

### SMTP Configuration

- **SMTP_HOST** - SMTP server hostname
- **SMTP_PORT** - SMTP server port
- **SMTP_USERNAME** - SMTP authentication username
- **SMTP_PASSWORD** - SMTP authentication password
- **SMTP_FROM** - Default sender email address

See [SMTP Relay](SMTP-Relay) for detailed configuration.

### SMS Settings

- **SMS_ENDPOINT** - SMS gateway endpoint URL
- **SMS_AUTH** - SMS gateway authentication credentials

Configure when enabling outbound SMS notifications so the portal can relay messages to your gateway securely.

### Redis Configuration

- **REDIS_URL** - Redis connection string for caching and session storage (optional)
- Used for distributed caching in multi-worker deployments

### Azure Graph / Office 365

- **AZURE_TENANT_ID** - Azure AD tenant identifier
- **AZURE_CLIENT_ID** - Azure AD application client ID
- **AZURE_CLIENT_SECRET** - Azure AD application secret

Required for Microsoft 365 license synchronization. See setup instructions in the [Setup and Installation](Setup-and-Installation#office-365-sync) guide.

## Migration Settings

- **MIGRATION_LOCK_TIMEOUT** - Advisory lock timeout in seconds (default: appropriate for most deployments)
- Increase if your production servers need a longer window for migration execution

## Fail2ban Integration

- **FAIL2BAN_LOG_PATH** - Path to log file for Fail2ban monitoring

When configured, MyPortal records structured authentication events for Fail2ban filtering. See [Fail2ban](Fail2ban) for complete setup.

## Related Documentation

- [Setup and Installation](Setup-and-Installation)
- [IMAP Configuration](IMAP)
- [SMTP Relay](SMTP-Relay)
- [Xero Integration](Xero-Integration)
- [Fail2ban](Fail2ban)

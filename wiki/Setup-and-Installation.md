# Setup and Installation

MyPortal is a Python-first customer portal built with FastAPI, async MySQL access, and Jinja-powered views. The application provides a modern, extensible architecture for customer management, ticketing, automation, and integrations.

## Requirements

- Python 3.10 or higher
- MySQL database (SQLite as fallback)
- Native libraries for WeasyPrint (for PDF generation)

## Installation Steps

### 1. Install Native Dependencies

WeasyPrint requires native libraries for PDF rendering. On Debian/Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y libpango-1.0-0 libpangocairo-1.0-0
```

For other distributions, see [WeasyPrint's installation docs](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#installation).

### 2. Create Virtual Environment

Create a project-local virtual environment to avoid conflicts with system Python:

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .\.venv\Scripts\activate
```

Or use the automated bootstrap script:

```bash
python scripts/bootstrap_venv.py
```

Pass `--recreate` to rebuild the environment from scratch.

### 3. Install Dependencies

Upgrade pip and install the project:

```bash
python -m pip install --upgrade pip
pip install -e .
```

### 4. Configure Environment

Copy the example environment file and configure it:

```bash
cp .env.example .env
```

Edit `.env` and update these critical settings:

- **MySQL credentials** - Database connection details
- **SESSION_SECRET** - Strong random value for session encryption
- **TOTP_ENCRYPTION_KEY** - Strong random value for TOTP encryption
- **SMS_ENDPOINT** and **SMS_AUTH** (optional) - For SMS notifications

Optional settings include:
- Redis connection details
- SMTP server configuration
- Azure Graph credentials for Office 365 sync

### 5. Start Development Server

```bash
uvicorn app.main:app --reload
```

On startup, the application automatically:
- Creates the database if it doesn't exist
- Applies pending SQL migrations
- Imports change log entries from `changes/` directory

### 6. Access the Application

- **Portal UI**: http://localhost:8000
- **API Documentation**: http://localhost:8000/docs

The first visit redirects to registration when no users exist. The first registered user becomes the super administrator.

## Production Deployment

### Automated Installation

Use the provided installation scripts:

```bash
# Production installation
sudo scripts/install_production.sh

# Development installation (doesn't interact with production database)
sudo scripts/install_development.sh
```

These scripts:
- Check and install Python requirements
- Create systemd services for running as a service
- Pull code from GitHub private repos using credentials from .env
- Set up appropriate database isolation

### Manual Production Setup

For production with Uvicorn:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

Or use Gunicorn with Uvicorn workers.

### Systemd Service

For a hardened systemd configuration, see [Systemd Service](Systemd-Service).

The configuration covers:
- Creating a dedicated service account
- Isolating environment variables
- Configuring auto-restarts
- Verifying unit status

## Database Migrations

Migrations are stored in `migrations/` and automatically applied on startup. Each migration is tracked in the `migrations` table to prevent re-execution.

### Reprocessing Migrations

To re-apply specific migrations (e.g., after manual database restore):

```bash
source .venv/bin/activate
python - <<'PY'
import asyncio
from app.core.database import db

async def main() -> None:
    # Re-run specific migration
    await db.reprocess_migrations(["001_initial_schema"])
    await db.disconnect()

asyncio.run(main())
PY
```

## Updating from GitHub

### Automated Updates

Use the upgrade script:

```bash
scripts/upgrade.sh
```

This creates a restart flag at `var/state/restart_required.flag`. Schedule `scripts/process_update_flag.sh` via cron to:
- Check for the flag every minute
- Reinstall dependencies
- Restart the ASGI service when required

Example cron configuration in `deploy/cron/process_update_flag.cron`.

### Manual Updates

```bash
git pull origin main
pip install -e .
systemctl restart myportal.service
```

## First-Time Setup

1. Access http://localhost:8000
2. You'll be redirected to registration (no users exist yet)
3. Create the first account - this becomes the super administrator
4. Configure integration modules from **Admin → Integration modules**
5. Set up users and companies as needed

## Fail2ban Integration

To protect against brute force attacks, configure Fail2ban:

1. Set `FAIL2BAN_LOG_PATH` in `.env`
2. Copy filter: `deploy/fail2ban/myportal-auth.conf` to `/etc/fail2ban/filter.d/`
3. Copy jail: `deploy/fail2ban/myportal-auth.local` to `/etc/fail2ban/jail.d/`
4. Update `logpath` in jail configuration
5. Restart Fail2ban: `systemctl restart fail2ban`

See [Fail2ban](Fail2ban) for details.

## Troubleshooting

### Migration Lock Timeout

If migrations take longer than expected, increase the timeout:

```bash
export MIGRATION_LOCK_TIMEOUT=300  # 5 minutes
```

### Database Connection Issues

- Verify MySQL credentials in `.env`
- Ensure MySQL server is running
- Check that the database exists (created automatically on first run)
- For SQLite fallback, ensure write permissions to the database file location

### Permission Errors

Ensure the application user has:
- Read/write access to `app/static/uploads`
- Read access to all application files
- Write access to log directories (if configured)

## Next Steps

- [Configure authentication](Authentication-API)
- [Set up integrations](Xero-Integration)
- [Configure API keys](API-Keys)
- [Review configuration options](Configuration)

# MyPortal Copilot Instructions

## Project Overview

MyPortal is a Python-first customer portal built with FastAPI, async MySQL, and Jinja-powered views. The application provides a modern, extensible architecture for customer management, ticketing, automation, and integrations.

### Technology Stack

- **Backend**: Python 3.10+, FastAPI, SQLAlchemy 2.0+
- **Database**: MySQL (primary), SQLite (fallback)
- **Frontend**: Jinja2 templates, responsive layouts
- **Testing**: pytest, pytest-asyncio
- **Key Dependencies**: uvicorn, aiomysql, pydantic, passlib, python-jose, redis, httpx

## Development Workflow

### Running the Application

- Use `python -m uvicorn app.main:app --reload` for development
- Production deployments use systemd services (see `scripts/install_production.sh`)
- Development environment runs alongside production without sharing databases

### Testing

- Run tests with `pytest` from the repository root
- Tests are located in the `tests/` directory
- Always test code changes to prevent Internal Server Error occurrences
- Use existing test patterns (pytest-asyncio, fixtures in conftest.py)

### Building and Deployment

- Installation scripts: `scripts/install_production.sh`, `scripts/install_development.sh`
- Update script: `scripts/upgrade.sh`
- Install scripts should:
  - Check and install Python requirements (python3, venv)
  - Create systemd services for running as a service
  - Pull code from GitHub private repos using credentials from .env
  - Set up development installer that doesn't interact with production database

## Code Guidelines

### Language and Framework

- All code should be based on Python
- Use async/await patterns consistently with FastAPI
- Follow existing code structure in `app/` directory

### Database and Migrations

- SQL migrations should be applied automatically during application startup
- Use SQLite as fallback when MySQL is not configured
- Store dates and times in UTC format in the database
- Display dates and times in local timezone to users
- Use file-driven SQL migration runner where possible

### Security Best Practices

- Always consider security when implementing changes
- Follow security best practices for authentication and authorization
- The first user created is the super admin
- Login page redirects to registration when no users exist
- Use CSRF protection on authenticated state-changing requests
- Validate and sanitize all user inputs
- Do not commit secrets or credentials to the repository

### API Development

- Include CRUD Swagger UI for all API endpoints
- Update API documentation when endpoints are created or modified
- Use RESTful conventions for API design
- Implement rate limiting for external API integrations

## UI and Frontend Guidelines

### Layout and Design

- Use 3-part layout (unless otherwise specified):
  - Left menu with buttons and relevant icons
  - Right header with page-specific menus
  - Right body with actual app data
- Apps should be themeable with custom favicons and logos
- Use responsive layout design
- Apps should not exceed viewport width or height (unless explicitly specified)
- Keep row divider heights consistent across table width

### Tables and Data Display

- All tables should have sorting and filtering capabilities
- Use consistent styling for data tables
- Implement pagination for large datasets

## Change Log Management

### Creating Change Log Entries

- Maintain a change log for each new feature or fix
- Store change logs in the `changes/` folder
- Each change should generate a new file with a GUID as the filename
- Import change log files to the `change_log` database table
- Migrate historical entries from `changes.md` to the database

### Change Log Format

Change log files must use this JSON format:

```json
{
  "guid": "",
  "occurred_at": "",
  "change_type": "",
  "summary": "",
  "content_hash": ""
}
```

## Integration Guidelines

### External APIs

When working with external APIs, use the documentation for these systems:

- **SyncroRMM**: https://api-docs.syncromsp.com/
- **TacticalRMM**: https://api.hawkinsitsolutions.com.au/api/schema/swagger-ui/

### Webhook Management

- Monitor external webhook calls
- Implement retry logic for failed webhooks
- Make monitoring accessible via admin page
- Track webhook events in the database

## Environment Configuration

### .env File Management

- Create and maintain `.env.example` file with all available environment variables
- Update `.env.example` automatically as new variables are added
- Never commit actual `.env` files with real credentials
- Document each environment variable's purpose

## Code Quality Standards

### File Management

- Do not generate binary files
- Python bytecode cache files should not be committed to the repository
- Use `.gitignore` to exclude build artifacts and dependencies

### Code Style

- When moving items between locations, match the style of the destination rather than the source
- Follow existing code patterns and conventions in the repository
- Write clear, self-documenting code with minimal comments
- Use type hints for function parameters and return values

### Testing Requirements

- Write tests for new features and bug fixes
- Use regression tests where possible
- Ensure tests cover edge cases and error conditions
- Run full test suite before submitting changes

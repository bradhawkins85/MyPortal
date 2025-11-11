# CI/CD Pipeline Documentation

This document describes the CI/CD pipeline implementation for MyPortal using GitHub Actions.

## Overview

The CI/CD pipeline consists of three main workflows:

1. **CI Workflow** (`ci.yml`) - Runs on every pull request and push
2. **Deployment Workflow** (`deploy.yml`) - Deploys to staging/production
3. **Security Scanning** (`security.yml`) - Daily security scans and vulnerability checks

## CI Workflow

### Triggers
- Pull requests to `main` or `develop` branches
- Pushes to `main` or `develop` branches

### Jobs

#### 1. Lint (`lint`)
- Checks code style using `ruff`
- Fails the build if linting errors are found
- Configuration in `pyproject.toml` under `[tool.ruff]`

#### 2. Type Check (`type-check`)
- Runs `mypy` for static type checking
- Currently set to continue on errors (non-blocking)
- Configuration in `pyproject.toml` under `[tool.mypy]`

#### 3. Test (`test`)
- Runs full test suite with pytest
- Tests against Python 3.10, 3.11, and 3.12
- Generates coverage reports
- Uploads coverage artifacts for Python 3.10

#### 4. Build (`build`)
- Builds Docker image
- Tests the built image by running it and checking health endpoint
- Uses Docker layer caching for faster builds

#### 5. Security Scan (`security-scan`)
- Runs `safety` to check for known vulnerabilities in dependencies
- Runs `bandit` for security linting of Python code
- Uploads security reports as artifacts

#### 6. Status Check (`status-check`)
- Final job that checks if all required jobs passed
- Fails if any required job failed

### Required Secrets
None - CI workflow runs without secrets

## Deployment Workflow

### Triggers
- Push to `develop` branch → Deploy to staging
- Push to `main` branch → Deploy to production
- Manual dispatch via GitHub Actions UI

### Jobs

#### 1. Build (`build`)
- Builds Docker image
- Pushes to GitHub Container Registry (ghcr.io)
- Tags images appropriately:
  - `branch-name`
  - `branch-name-{sha}`
  - `latest` (for main branch)

#### 2. Deploy to Staging (`deploy-staging`)
- Runs when code is pushed to `develop` branch
- Validates environment variables
- Runs database migrations (automatic on startup)
- Deploys new Docker image
- Runs smoke tests
- Sends deployment notifications

#### 3. Deploy to Production (`deploy-production`)
- Runs when code is pushed to `main` branch
- Requires manual approval (GitHub environment protection)
- Creates backup point for rollback
- Uses blue-green deployment strategy
- Runs comprehensive smoke tests
- Monitors deployment for errors
- Sends deployment notifications

#### 4. Rollback (`rollback`)
- Runs if deployment fails
- Switches traffic back to previous version
- Notifies team of rollback

### Deployment Secrets

**Note:** The deployment steps in the workflow are currently placeholders. The workflow will run successfully even if these secrets are not configured, but will display warnings. Configure these secrets when implementing actual deployment infrastructure.

#### Staging Environment
- `STAGING_DB_HOST` - Database host for staging (optional until deployment is implemented)
- `STAGING_URL` - Staging environment URL (optional, for smoke tests)

#### Production Environment
- `PRODUCTION_DB_HOST` - Database host for production (optional until deployment is implemented)
- `PRODUCTION_URL` - Production environment URL (optional, for smoke tests)

### Environment Protection Rules

Configure these in GitHub Settings → Environments:

#### Staging Environment
- No required reviewers (auto-deploy)
- Optional: Limit to `develop` branch

#### Production Environment
- Required reviewers: 1-2 team members
- Limit to `main` branch only
- Optional: Deployment delay

## Security Scanning Workflow

### Triggers
- Daily at 2 AM UTC (scheduled)
- Push to `main` or `develop` branches
- Manual dispatch

### Jobs

#### 1. Dependency Scan (`dependency-scan`)
- Runs `safety` to check for known vulnerabilities
- Runs `pip-audit` for additional vulnerability checks
- Creates GitHub issue if vulnerabilities found
- Uploads reports as artifacts (retained for 90 days)

#### 2. Code Security (`code-security`)
- Runs `bandit` for security linting
- Checks for common security issues in Python code
- Uploads reports as artifacts

#### 3. Docker Security (`docker-security`)
- Builds Docker image
- Scans image with Trivy
- Uploads results to GitHub Security tab

## Helper Scripts

### `scripts/pre_deploy_check.sh`
Pre-deployment validation script that checks:
- Required environment variables are set
- Python version is >= 3.10
- Required packages are installed
- Migrations directory exists

Usage:
```bash
bash scripts/pre_deploy_check.sh
```

### `scripts/smoke_tests.sh`
Smoke tests to verify basic functionality after deployment.

Tests:
- Health check endpoint
- Static file serving
- Login/home page
- API documentation

Usage:
```bash
bash scripts/smoke_tests.sh [BASE_URL]
# Default: http://localhost:8000
```

## Docker Support

### Dockerfile
Multi-stage Dockerfile that:
- Uses Python 3.10 slim base image
- Installs system dependencies
- Creates non-root user
- Includes health check
- Exposes port 8000

### docker-compose.yml
Complete stack with:
- MyPortal application
- MySQL database
- Redis cache

Usage:
```bash
# Start services
docker-compose up -d

# View logs
docker-compose logs -f myportal

# Stop services
docker-compose down
```

## Database Migrations

Migrations run automatically on application startup:
- Located in `migrations/` directory
- Applied in alphabetical/numerical order
- Tracked in database to prevent re-running

No manual migration commands needed!

## Health Checks

The application provides a health check endpoint at `/health`:
- Returns HTTP 200 when healthy
- Used by Docker healthcheck
- Used by smoke tests
- Used by load balancers

## Zero-Downtime Deployments

### Blue-Green Deployment Strategy

1. **Deploy to Green Environment**
   - New version deployed to inactive environment
   - Migrations run automatically
   - Health checks verify readiness

2. **Run Smoke Tests**
   - Verify critical functionality
   - Check database connectivity
   - Validate API endpoints

3. **Switch Traffic**
   - Load balancer switches to green environment
   - Blue environment remains ready for quick rollback

4. **Monitor**
   - Watch for errors in logs
   - Monitor response times
   - Check error rates

5. **Rollback if Needed**
   - Quick switch back to blue environment
   - No data loss (migrations are forward-compatible)

## Rollback Procedures

### Automatic Rollback
- Triggered if deployment fails
- Switches traffic back to previous version
- Team is notified

### Manual Rollback
1. Go to GitHub Actions → Workflows → Deploy
2. Click on failed deployment
3. Re-run workflow with previous commit SHA

Or using Docker:
```bash
# List available image tags
docker images ghcr.io/bradhawkins85/myportal

# Deploy previous version
docker pull ghcr.io/bradhawkins85/myportal:main-<previous-sha>
docker stop myportal && docker rm myportal
docker run -d --name myportal ghcr.io/bradhawkins85/myportal:main-<previous-sha>
```

## Notifications

Currently configured to output to workflow logs. To enable notifications:

### Slack
Add to workflow:
```yaml
- name: Notify Slack
  uses: slackapi/slack-github-action@v1
  with:
    webhook-url: ${{ secrets.SLACK_WEBHOOK_URL }}
    payload: |
      {
        "text": "Deployment to ${{ github.event.inputs.environment }} completed"
      }
```

### Email
Add to workflow:
```yaml
- name: Send Email
  uses: dawidd6/action-send-mail@v3
  with:
    server_address: smtp.gmail.com
    server_port: 465
    username: ${{ secrets.EMAIL_USERNAME }}
    password: ${{ secrets.EMAIL_PASSWORD }}
    subject: Deployment Status
    to: team@example.com
    from: github-actions@example.com
```

## Monitoring

After deployment, monitor:
- Application logs via Docker/Kubernetes
- Database connection pool
- Response times
- Error rates
- Resource usage (CPU, memory)

## Troubleshooting

### Build Fails
1. Check linting errors: `ruff check app/`
2. Check tests: `pytest tests/ -v`
3. Check dependencies: `pip install -e ".[dev]"`

### Deployment Fails
1. Check environment variables are set
2. Run pre-deployment validation: `bash scripts/pre_deploy_check.sh`
3. Check database connectivity
4. Review application logs

### Health Check Fails
1. Check application is running: `docker ps`
2. Check application logs: `docker logs myportal`
3. Test manually: `curl http://localhost:8000/health`
4. Check database connection

### Smoke Tests Fail
1. Run smoke tests manually with verbose output
2. Check specific endpoint that failed
3. Review application logs for errors
4. Verify environment configuration

## Best Practices

1. **Always run tests locally before pushing**
   ```bash
   pytest tests/ -v
   ruff check app/
   ```

2. **Use feature branches**
   - Create branch from `develop`
   - Submit PR to `develop`
   - Merge to `main` for production release

3. **Keep migrations forward-compatible**
   - Don't drop columns immediately
   - Use multi-step migrations for breaking changes

4. **Monitor deployments**
   - Watch logs during deployment
   - Check error rates
   - Verify functionality manually

5. **Test rollbacks**
   - Periodically test rollback procedures
   - Ensure backups are working
   - Document rollback steps

## Future Enhancements

- [ ] Add canary deployments
- [ ] Implement feature flags
- [ ] Add performance testing
- [ ] Integrate with monitoring tools (Datadog, New Relic)
- [ ] Add automated rollback on error threshold
- [ ] Implement deployment metrics dashboard

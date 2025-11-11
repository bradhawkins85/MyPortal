# GitHub Actions Workflows

This directory contains GitHub Actions workflows for CI/CD automation.

## Workflows

### `ci.yml` - Continuous Integration
Runs on every pull request and push to main/develop branches.

**Jobs:**
- `lint` - Code style checking with ruff
- `type-check` - Type checking with mypy
- `test` - Run test suite with pytest (Python 3.10, 3.11, 3.12)
- `build` - Build and test Docker image
- `security-scan` - Security analysis with safety and bandit
- `status-check` - Final status verification

### `deploy.yml` - Deployment
Deploys to staging/production environments.

**Jobs:**
- `build` - Build and push Docker image to GHCR
- `deploy-staging` - Deploy to staging (develop branch)
- `deploy-production` - Deploy to production (main branch, requires approval)
- `rollback` - Automatic rollback on failure

**Deployment Strategy:**
- Staging: Automatic deployment on merge to `develop`
- Production: Manual approval required for `main` branch

### `security.yml` - Security Scanning
Daily security scans and vulnerability checks.

**Jobs:**
- `dependency-scan` - Check dependencies for vulnerabilities
- `code-security` - Security linting with bandit
- `docker-security` - Container image scanning with Trivy

**Schedule:** Daily at 2 AM UTC

## Configuration

### Required Secrets

Set these in GitHub Settings → Secrets and variables → Actions:

**Staging:**
- `STAGING_DB_HOST` - Database host
- `STAGING_URL` - Application URL (optional, for smoke tests)

**Production:**
- `PRODUCTION_DB_HOST` - Database host
- `PRODUCTION_URL` - Application URL (optional, for smoke tests)

### Environment Protection

Set up in GitHub Settings → Environments:

**staging:**
- No required reviewers
- Optional: Branch restriction to `develop`

**production:**
- Required reviewers: 1-2 team members
- Branch restriction: `main` only
- Optional: Deployment delay

## Local Testing

### Lint
```bash
ruff check app/
```

### Type Check
```bash
mypy app/
```

### Run Tests
```bash
pytest tests/ -v
```

### Build Docker Image
```bash
docker build -t myportal:test .
```

### Run Pre-deployment Check
```bash
bash scripts/pre_deploy_check.sh
```

### Run Smoke Tests
```bash
bash scripts/smoke_tests.sh http://localhost:8000
```

## Workflow Status

View workflow runs:
- GitHub Actions tab in repository
- Pull request checks
- Branch deployment status

## Troubleshooting

### CI Fails
1. Check error logs in GitHub Actions
2. Run tests locally
3. Fix issues and push

### Deployment Fails
1. Check deployment logs
2. Verify secrets are set correctly
3. Check environment configuration
4. Review rollback if needed

## Documentation

See `docs/cicd-pipeline.md` for comprehensive documentation.

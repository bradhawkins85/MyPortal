# CI/CD Quick Setup Guide

This guide walks you through setting up the CI/CD pipeline for MyPortal.

## Prerequisites

- GitHub repository with admin access
- Docker (for local testing)
- Python 3.10+ (for local testing)

## Step 1: Configure GitHub Environments

### Create Staging Environment
1. Go to **Settings** → **Environments**
2. Click **New environment**
3. Name: `staging`
4. Click **Configure environment**
5. (Optional) Add environment variables if needed
6. Save

### Create Production Environment
1. Go to **Settings** → **Environments**
2. Click **New environment**
3. Name: `production`
4. Click **Configure environment**
5. Under **Deployment protection rules**:
   - Check **Required reviewers**
   - Add 1-2 team members as reviewers
6. Under **Deployment branches and tags**:
   - Select **Selected branches and tags**
   - Add rule: `main`
7. Save

## Step 2: Add Required Secrets

### For Staging
1. Go to **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret**
3. Add:
   - Name: `STAGING_DB_HOST`
   - Value: Your staging database host
4. (Optional) Add:
   - Name: `STAGING_URL`
   - Value: https://staging.yourapp.com

### For Production
1. Add:
   - Name: `PRODUCTION_DB_HOST`
   - Value: Your production database host
2. (Optional) Add:
   - Name: `PRODUCTION_URL`
   - Value: https://yourapp.com

## Step 3: Enable GitHub Container Registry

1. Go to **Settings** → **Actions** → **General**
2. Under **Workflow permissions**:
   - Select **Read and write permissions**
   - Check **Allow GitHub Actions to create and approve pull requests**
3. Save

## Step 4: Test the CI Pipeline

### Create a Test Pull Request
```bash
# Create a new branch
git checkout -b test-ci-pipeline

# Make a small change (e.g., update README)
echo "Testing CI" >> README.md

# Commit and push
git add README.md
git commit -m "Test CI pipeline"
git push origin test-ci-pipeline
```

### Monitor the CI Workflow
1. Go to your repository on GitHub
2. Click **Pull requests** tab
3. Open your test PR
4. Scroll to checks section at the bottom
5. Watch the CI workflow run:
   - ✓ Lint
   - ✓ Type Check
   - ✓ Test
   - ✓ Build
   - ✓ Security Scan

## Step 5: Test Staging Deployment

### Merge to Develop
```bash
# Switch to develop
git checkout develop
git pull origin develop

# Merge your test branch
git merge test-ci-pipeline

# Push to trigger staging deployment
git push origin develop
```

### Monitor Deployment
1. Go to **Actions** tab
2. Click on the **Deploy** workflow
3. Watch the deployment progress:
   - Build image
   - Push to registry
   - Deploy to staging
   - Run smoke tests

## Step 6: Test Production Deployment

### Merge to Main
```bash
# Switch to main
git checkout main
git pull origin main

# Merge develop
git merge develop

# Push to trigger production deployment
git push origin main
```

### Approve Deployment
1. Go to **Actions** tab
2. Click on the **Deploy** workflow run
3. Click **Review deployments**
4. Check **production**
5. Click **Approve and deploy**

## Step 7: Verify Deployments

### Test Health Endpoints
```bash
# Staging
curl https://staging.yourapp.com/health

# Production
curl https://yourapp.com/health
```

### Run Smoke Tests
```bash
# Staging
bash scripts/smoke_tests.sh https://staging.yourapp.com

# Production
bash scripts/smoke_tests.sh https://yourapp.com
```

## Local Development Testing

### Before Pushing Code

1. **Run linter:**
   ```bash
   ruff check app/
   ```

2. **Run type checker:**
   ```bash
   mypy app/
   ```

3. **Run tests:**
   ```bash
   pytest tests/ -v
   ```

4. **Check test coverage:**
   ```bash
   pytest tests/ --cov=app --cov-report=term-missing
   ```

5. **Test pre-deployment checks:**
   ```bash
   SESSION_SECRET=test \
   TOTP_ENCRYPTION_KEY=AAAA... \
   bash scripts/pre_deploy_check.sh
   ```

## Common Issues and Solutions

### Issue: CI fails with linting errors
**Solution:** Run `ruff check app/ --fix` to auto-fix issues

### Issue: Tests fail locally but pass in CI
**Solution:** Ensure you have all dependencies installed: `pip install -e ".[dev]"`

### Issue: Docker build fails
**Solution:** 
- Check Docker is running: `docker info`
- Try building locally: `docker build -t myportal:test .`
- Check `.dockerignore` is not excluding required files

### Issue: Deployment stuck waiting for approval
**Solution:** Check Environments settings and ensure reviewers are configured

### Issue: Smoke tests fail
**Solution:**
- Verify application is running
- Check health endpoint manually
- Review application logs
- Verify environment variables are set

## Monitoring and Maintenance

### View Workflow Runs
- **Actions** tab → Click on workflow → View run details

### View Deployment History
- **Environments** section shows deployment history per environment

### Security Alerts
- Security scanning runs daily
- Check **Security** tab for vulnerability reports
- Review **Actions** tab for security workflow runs

## Next Steps

1. **Set up notifications:**
   - Configure Slack/email notifications in workflows
   - See `docs/cicd-pipeline.md` for examples

2. **Customize workflows:**
   - Adjust test timeouts if needed
   - Add additional smoke tests
   - Configure deployment strategies

3. **Add monitoring:**
   - Integrate with monitoring tools
   - Set up alerts for deployment failures
   - Track deployment metrics

## Resources

- Full documentation: `docs/cicd-pipeline.md`
- Workflow definitions: `.github/workflows/`
- Helper scripts: `scripts/`

## Support

For issues or questions:
1. Check workflow logs in Actions tab
2. Review documentation
3. Check deployment logs
4. Contact DevOps team

---

**Important:** Always test changes in staging before deploying to production!

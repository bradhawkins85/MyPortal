# GitHub Security and Code Scanning

This document describes the security and code scanning features configured for the MyPortal repository.

## Overview

MyPortal uses GitHub's built-in security features and additional scanning tools to identify vulnerabilities in code and dependencies. All security scanning is automated through GitHub Actions workflows and Dependabot.

## Features Enabled

### 1. CodeQL Code Scanning

**File:** `.github/workflows/codeql.yml`

CodeQL is GitHub's semantic code analysis engine that identifies security vulnerabilities and coding errors in the codebase.

**Features:**
- Analyzes Python code for security issues, bugs, and code quality problems
- Uses `security-extended` and `security-and-quality` query suites for comprehensive coverage
- Detects common vulnerabilities like SQL injection, XSS, path traversal, etc.
- Results appear in the GitHub Security tab

**Triggers:**
- On push to `main` or `develop` branches
- On pull requests targeting `main` or `develop`
- Weekly scheduled scan every Monday at 9:00 AM UTC
- Manual workflow dispatch

**Documentation:** https://docs.github.com/en/code-security/code-scanning

### 2. Dependency Review

**File:** `.github/workflows/dependency-review.yml`

Scans pull requests for vulnerable or non-compliant dependencies before they are merged.

**Features:**
- Reviews all dependency changes in pull requests
- Fails builds if moderate or higher severity vulnerabilities are detected
- Validates licenses against approved list (MIT, Apache-2.0, BSD, ISC, GPL-3.0, LGPL-3.0)
- Automatically comments on PRs with vulnerability information

**Triggers:**
- On pull requests targeting `main` or `develop` branches

**Documentation:** https://docs.github.com/en/code-security/supply-chain-security/understanding-your-software-supply-chain/about-dependency-review

### 3. Python Security Scanning

**File:** `.github/workflows/security.yml`

Runs Python-specific security tools to detect vulnerabilities and insecure code patterns.

#### Bandit
Security linter that finds common security issues in Python code.

**Features:**
- Scans the `app/` directory recursively
- Detects medium+ severity security issues
- Generates SARIF reports uploaded to GitHub Security tab
- Checks for hardcoded passwords, SQL injection risks, weak crypto, etc.

#### pip-audit
Scans installed dependencies for known security vulnerabilities.

**Features:**
- Checks all project dependencies against PyPI vulnerability database
- Generates detailed JSON reports with vulnerability descriptions
- Reports uploaded as workflow artifacts for review

**Triggers:**
- On push to `main` or `develop` branches
- On pull requests targeting `main` or `develop`
- Weekly scheduled scan every Tuesday at 9:00 AM UTC
- Manual workflow dispatch

**Documentation:**
- Bandit: https://bandit.readthedocs.io/
- pip-audit: https://pypi.org/project/pip-audit/

### 4. Dependabot

**File:** `.github/dependabot.yml`

Automatically creates pull requests to update dependencies when new versions are available or vulnerabilities are discovered.

**Features:**
- Monitors Python (pip) dependencies weekly
- Monitors GitHub Actions workflow dependencies weekly
- Opens up to 10 PRs for Python dependencies
- Opens up to 5 PRs for GitHub Actions
- Auto-labels PRs with `dependencies` and ecosystem tags

**Documentation:** https://docs.github.com/en/code-security/dependabot

### 5. Secret Scanning

GitHub automatically scans all repositories for known secret patterns.

**Features:**
- Detects API keys, tokens, passwords, and other secrets in commits
- Alerts repository administrators when secrets are found
- Provides remediation guidance
- Can be configured with custom patterns (requires GitHub Advanced Security)

**Enable:** Settings → Code security and analysis → Secret scanning

**Documentation:** https://docs.github.com/en/code-security/secret-scanning

## GitHub Security Tab

All security scanning results are aggregated in the Security tab:

1. Navigate to the repository on GitHub
2. Click the "Security" tab
3. Review:
   - **Code scanning alerts** - CodeQL and Bandit findings
   - **Dependabot alerts** - Vulnerable dependencies
   - **Secret scanning alerts** - Exposed secrets

## Viewing Scan Results

### In Pull Requests
- CodeQL, Bandit, and Dependency Review run automatically
- Results appear as checks at the bottom of the PR
- Click "Details" to see specific findings

### In the Security Tab
1. Go to Security → Code scanning
2. Filter by tool (CodeQL, Bandit)
3. View alert details, affected code, and remediation guidance

### In Workflow Runs
1. Go to Actions tab
2. Select the workflow (CodeQL Analysis, Security Scan, Dependency Review)
3. View detailed logs and artifacts

## Responding to Alerts

### Code Scanning Alerts
1. Review the alert in the Security tab
2. Examine the affected code and data flow
3. Apply the suggested fix or dismiss if false positive
4. Document dismissal reason for audit trail

### Dependency Alerts
1. Review vulnerable dependency in Dependabot alerts
2. Check if Dependabot has opened a PR to update it
3. Review and merge the PR if tests pass
4. If no safe version exists, consider alternative packages

### Secret Alerts
1. **Immediately** revoke the exposed secret
2. Generate a new secret
3. Update all systems using the old secret
4. Commit a fix that removes the secret from code
5. Use environment variables or secret management instead

## Best Practices

1. **Review alerts promptly** - Security issues should be triaged within 24 hours
2. **Don't ignore alerts** - Even false positives should be documented as dismissed
3. **Keep dependencies updated** - Merge Dependabot PRs regularly
4. **Test security fixes** - Run full test suite before merging security updates
5. **Use environment variables** - Never commit secrets to the repository
6. **Monitor the Security tab** - Check weekly for new vulnerabilities

## Maintenance

### Weekly Tasks
- Review new security alerts in the Security tab
- Merge approved Dependabot PRs
- Review scheduled CodeQL and Bandit scan results

### Monthly Tasks
- Review dismissed alerts to ensure they're still valid
- Update allowed licenses list if needed
- Review security policy and update as needed

### Quarterly Tasks
- Run penetration testing
- Review access controls and permissions
- Update security documentation

## Configuration Changes

To modify security scanning behavior, edit these files:

- **CodeQL queries:** `.github/workflows/codeql.yml` → `queries` parameter
- **Dependency severity:** `.github/workflows/dependency-review.yml` → `fail-on-severity`
- **Bandit rules:** `.github/workflows/security.yml` → `bandit` command parameters
- **Dependabot schedule:** `.github/dependabot.yml` → `schedule.interval`

## Troubleshooting

### CodeQL fails on large repositories
- Increase `timeout-minutes` in codeql.yml
- Reduce query suite from `security-and-quality` to just `security-extended`

### Dependency Review fails with license errors
- Add missing licenses to `allow-licenses` in dependency-review.yml
- Review license compatibility before adding

### Bandit reports false positives
- Add `.bandit` config file to exclude specific tests or paths
- Use `# nosec` comments for individual false positives (with justification)

### pip-audit reports unavoidable vulnerabilities
- Document the issue and mitigation plan
- Consider vendoring and patching the dependency
- Look for alternative packages

## Additional Resources

- [GitHub Security Documentation](https://docs.github.com/en/code-security)
- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [Python Security Best Practices](https://python.readthedocs.io/en/latest/library/security_warnings.html)
- MyPortal Security Policy: [SECURITY.md](../SECURITY.md)

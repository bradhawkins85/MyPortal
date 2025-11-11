#!/usr/bin/env bash
# Pre-deployment validation script
# Ensures environment is ready for deployment

set -euo pipefail

echo "Running pre-deployment validation..."
echo "===================================="

ERRORS=0

# Check required environment variables
check_env_var() {
    local var_name=$1
    local required=${2:-true}
    
    if [ -z "${!var_name:-}" ]; then
        if [ "$required" == "true" ]; then
            echo "✗ ERROR: Required environment variable $var_name is not set"
            ((ERRORS++))
        else
            echo "⚠ WARNING: Optional environment variable $var_name is not set"
        fi
    else
        echo "✓ $var_name is set"
    fi
}

echo ""
echo "Checking required environment variables:"
echo "----------------------------------------"

# Required variables
check_env_var "SESSION_SECRET"
check_env_var "TOTP_ENCRYPTION_KEY"

# Optional but recommended
check_env_var "DB_HOST" false
check_env_var "DB_USER" false
check_env_var "DB_PASSWORD" false
check_env_var "DB_NAME" false

echo ""
echo "Checking Python environment:"
echo "----------------------------"

# Check Python version
PYTHON_VERSION=$(python --version 2>&1 | cut -d' ' -f2)
PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d'.' -f1)
PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d'.' -f2)

if [ "$PYTHON_MAJOR" -ge 3 ] && [ "$PYTHON_MINOR" -ge 10 ]; then
    echo "✓ Python version: $PYTHON_VERSION (>= 3.10)"
else
    echo "✗ ERROR: Python version $PYTHON_VERSION is too old (requires >= 3.10)"
    ((ERRORS++))
fi

# Check if required packages are installed
echo ""
echo "Checking required packages:"
echo "---------------------------"

check_package() {
    local package=$1
    if python -c "import $package" 2>/dev/null; then
        echo "✓ $package is installed"
    else
        echo "✗ ERROR: $package is not installed"
        ((ERRORS++))
    fi
}

check_package "fastapi"
check_package "uvicorn"
check_package "sqlalchemy"

# Check migrations directory exists
echo ""
echo "Checking migrations:"
echo "--------------------"

if [ -d "migrations" ]; then
    MIGRATION_COUNT=$(ls -1 migrations/*.sql 2>/dev/null | wc -l)
    echo "✓ Migrations directory exists ($MIGRATION_COUNT migrations found)"
else
    echo "✗ ERROR: Migrations directory not found"
    ((ERRORS++))
fi

# Summary
echo ""
echo "===================================="
if [ $ERRORS -eq 0 ]; then
    echo "✅ Pre-deployment validation PASSED"
    echo "Environment is ready for deployment"
    exit 0
else
    echo "❌ Pre-deployment validation FAILED"
    echo "Found $ERRORS error(s)"
    echo "Please fix the errors before deploying"
    exit 1
fi

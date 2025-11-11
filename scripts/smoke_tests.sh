#!/usr/bin/env bash
# Smoke tests to verify basic functionality after deployment

set -euo pipefail

BASE_URL="${1:-http://localhost:8000}"
TIMEOUT=30

echo "Running smoke tests against: $BASE_URL"
echo "=================================="

# Function to test an endpoint
test_endpoint() {
    local endpoint=$1
    local expected_status=${2:-200}
    local description=$3
    
    echo -n "Testing: $description... "
    
    response=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL$endpoint" || echo "000")
    
    if [ "$response" == "$expected_status" ]; then
        echo "✓ PASS (HTTP $response)"
        return 0
    else
        echo "✗ FAIL (Expected HTTP $expected_status, got HTTP $response)"
        return 1
    fi
}

# Track failures
FAILED=0

# Test health endpoint
test_endpoint "/health" 200 "Health check endpoint" || ((FAILED++))

# Test static files
test_endpoint "/static/manifest.webmanifest" 200 "Static file serving" || ((FAILED++))

# Test login page (should redirect or show page)
test_endpoint "/" 200 "Login/Home page" || ((FAILED++))

# Test API documentation
test_endpoint "/docs" 200 "API documentation (Swagger)" || ((FAILED++))

echo "=================================="
echo "Smoke tests completed"
echo "Total tests: 4"
echo "Failed: $FAILED"

if [ $FAILED -gt 0 ]; then
    echo "❌ Smoke tests FAILED"
    exit 1
else
    echo "✅ All smoke tests PASSED"
    exit 0
fi

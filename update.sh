#!/usr/bin/env bash
set -e

# Navigate to the project root directory
cd "$(dirname "$0")"

# Load GitHub credentials from .env if present
if [ -f .env ]; then
  GITHUB_USERNAME=$(grep -E '^GITHUB_USERNAME=' .env | head -n 1 | cut -d '=' -f2- | sed 's/#.*//' | tr -d '"')
  GITHUB_PASSWORD=$(grep -E '^GITHUB_PASSWORD=' .env | head -n 1 | cut -d '=' -f2- | sed 's/#.*//' | tr -d '"')
fi

# Optional credentials for authenticated pulls
USERNAME="${GITHUB_USERNAME:-$1}"
PASSWORD="${GITHUB_PASSWORD:-$2}"
REMOTE_URL=$(git config --get remote.origin.url)

if [[ -n "$USERNAME" && -n "$PASSWORD" && "$REMOTE_URL" == https://* ]]; then
  AUTH_REMOTE_URL="https://${USERNAME}:${PASSWORD}@${REMOTE_URL#https://}"
  git_output=$(git pull "$AUTH_REMOTE_URL" main 2>&1)
else
  git_output=$(git pull origin main 2>&1)
fi
git_status=$?
echo "$git_output"

# Exit if git pull failed
if [ $git_status -ne 0 ]; then
  exit $git_status
fi

# If repository already up to date, skip further steps
if echo "$git_output" | grep -q "Already up to date."; then
  echo "No updates found. Exiting."
  exit 0
fi

npm install
npm run build
# Update application version and build time
current_date=$(date +%Y%m%d)
current_time=$(date +%H%M)
echo "$current_date" > version.txt
echo "$current_time" > build.txt

pm2 restart myportal

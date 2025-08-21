#!/usr/bin/env bash
set -e

# Navigate to the project root directory
cd "$(dirname "$0")"

# Pull latest changes and rebuild the project
git_output=$(git pull origin main 2>&1)
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
# Increment application version
if [ -f version.txt ]; then
  current_version=$(cat version.txt)
else
  current_version=0
fi
new_version=$((current_version + 1))
echo "$new_version" > version.txt

pm2 restart myportal

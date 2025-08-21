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
# Update application version and build time
current_date=$(date +%Y%m%d)
current_time=$(date +%H:%M)
echo "$current_date" > version.txt
echo "$current_time" > build.txt

pm2 restart myportal

#!/usr/bin/env bash
set -e

# Navigate to the project root directory
cd "$(dirname "$0")"

# Pull latest changes and rebuild the project
git pull origin main
npm install
npm run build
pm2 restart myportal

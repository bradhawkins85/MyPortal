# MyPortal

MyPortal is a simple customer portal built with Node.js, TypeScript and MySQL. It allows users from a company to log in and view company information and allocated software licenses.

There are no default login credentials; the first visit will prompt you to register.

## Features

- Session-based authentication for multiple users
- Business information summary tab to confirm the logged-in company
- Licenses tab showing license name, platform, count, expiry date and contract term
- First-time visit redirects to a registration page when no users exist

## Setup

1. Install dependencies:
   ```bash
   npm install
   ```
2. Copy `.env.example` to `.env` and update the MySQL credentials and session secret.
3. Create the database and tables using `schema.sql`.
4. On first run, visiting `/login` will redirect to a registration page to create the initial user and company.
5. Run in development mode:
   ```bash
   npm run dev
   ```
6. Build and start the application:
   ```bash
   npm run build
   npm start
   ```
7. Run type checks:
   ```bash
   npm test
   ```

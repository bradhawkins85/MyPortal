# MyPortal

MyPortal is a simple customer portal built with Node.js, TypeScript and MySQL. It allows users from a company to log in and view company information and allocated software licenses.

## Features

- Session-based authentication for multiple users
- Business information summary tab to confirm the logged-in company
- Licenses tab showing license name, platform, count, expiry date and contract term

## Setup

1. Install dependencies:
   ```bash
   npm install
   ```
2. Copy `.env.example` to `.env` and update the MySQL credentials and session secret.
3. Create the database and tables using `schema.sql`.
4. Run in development mode:
   ```bash
   npm run dev
   ```
5. Build and start the application:
   ```bash
   npm run build
   npm start
   ```
6. Run type checks:
   ```bash
   npm test
   ```

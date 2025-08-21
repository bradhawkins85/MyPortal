import mysql from 'mysql2/promise';
import { RowDataPacket } from 'mysql2';
import dotenv from 'dotenv';
import fs from 'fs';
import path from 'path';

dotenv.config();

export const pool = mysql.createPool({
  host: process.env.DB_HOST,
  user: process.env.DB_USER,
  password: process.env.DB_PASSWORD,
  database: process.env.DB_NAME,
  multipleStatements:
    process.env.DB_ALLOW_MULTIPLE_STATEMENTS === 'true',
});

export async function runMigrations(): Promise<void> {
  const dbName = process.env.DB_NAME;
  if (!dbName) {
    throw new Error('DB_NAME not set');
  }

  // Ensure that the target database exists before running migrations.
  // When the application is launched for the first time the database may
  // not have been created yet which causes the initial queries to fail.
  const connection = await mysql.createConnection({
    host: process.env.DB_HOST,
    user: process.env.DB_USER,
    password: process.env.DB_PASSWORD,
  });
  await connection.query(`CREATE DATABASE IF NOT EXISTS \`${dbName}\``);
  await connection.end();

  await pool.query(
    'CREATE TABLE IF NOT EXISTS migrations (name VARCHAR(255) PRIMARY KEY)'
  );

  const [rows] = await pool.query<RowDataPacket[]>(
    'SELECT name FROM migrations'
  );
  const applied = new Set((rows as { name: string }[]).map((r) => r.name));

  const migrationsDir = path.resolve(__dirname, '..', 'migrations');
  if (!fs.existsSync(migrationsDir)) {
    return;
  }

  const files = fs
    .readdirSync(migrationsDir)
    .filter((f) => f.endsWith('.sql'))
    .sort();

  for (const file of files) {
    if (applied.has(file)) {
      continue;
    }
    const sql = fs.readFileSync(path.join(migrationsDir, file), 'utf8');
    const statements = sql
      .split(/;\s*(?:\r?\n|$)/)
      .map((s) => s.trim())
      .filter(Boolean);
    for (const statement of statements) {
      await pool.query(statement);
    }
    await pool.query('INSERT INTO migrations (name) VALUES (?)', [file]);
  }
}

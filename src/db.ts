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
  multipleStatements: true,
});

export async function runMigrations(): Promise<void> {
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
    await pool.query(sql);
    await pool.query('INSERT INTO migrations (name) VALUES (?)', [file]);
  }
}

-- Global account registry (SPEC §7.10). The only state readable before an
-- account's Durable Object is addressable. No content-adjacent data here.
CREATE TABLE IF NOT EXISTS accounts (
  account_id TEXT PRIMARY KEY,   -- ULID; also the DO name
  email      TEXT NOT NULL UNIQUE,
  salt       TEXT NOT NULL,      -- base64, per-account, generated at registration
  auth_hash  TEXT,              -- SHA-256 of the client auth_credential; NULL until set
  is_pro     INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

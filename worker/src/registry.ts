/**
 * Global account registry, backed by D1 (SPEC §7.9, §7.10).
 *
 * This is the only state that must be readable BEFORE an account's Durable
 * Object is addressable: the email -> account_id + salt + auth_hash + is_pro
 * lookup that login needs. Nothing content-adjacent lives here (SPEC §7.6).
 *
 * The accounts table:
 *   account_id  TEXT PRIMARY KEY   -- ULID; also the DO name
 *   email       TEXT UNIQUE
 *   salt        TEXT               -- base64, per-account, generated at register
 *   auth_hash   TEXT               -- SHA-256 of the client auth_credential
 *   is_pro      INTEGER            -- 0/1 entitlement gate (§7.13)
 *   created_at  TEXT
 */

import { constantTimeEqual, hashAuthCredential, randomSaltB64, ulid } from "./auth";

export interface AccountRow {
  account_id: string;
  email: string;
  salt: string;
  auth_hash: string | null;
  is_pro: number;
  created_at: string;
}

/** A typed failure the edge maps to an HTTP status. */
export class RegistryError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function byEmail(db: D1Database, email: string): Promise<AccountRow | null> {
  return db.prepare("SELECT * FROM accounts WHERE email = ?").bind(email).first<AccountRow>();
}

/** Create an account for an email; returns its account_id and fresh salt (§7.11). */
export async function createAccount(
  db: D1Database,
  email: string,
): Promise<{ account_id: string; salt: string }> {
  if (await byEmail(db, email)) throw new RegistryError(409, "email already registered");
  const account_id = ulid();
  const salt = randomSaltB64();
  await db
    .prepare(
      "INSERT INTO accounts (account_id, email, salt, auth_hash, is_pro, created_at) VALUES (?, ?, ?, ?, ?, ?)",
    )
    .bind(account_id, email, salt, null, 0, new Date().toISOString())
    .run();
  return { account_id, salt };
}

/** Set auth_hash from the client-derived auth_credential, completing registration. */
export async function setAuth(db: D1Database, email: string, authCredentialB64: string): Promise<void> {
  const acct = await byEmail(db, email);
  if (!acct) throw new RegistryError(404, "unknown account");
  const hash = await hashAuthCredential(authCredentialB64);
  await db.prepare("UPDATE accounts SET auth_hash = ? WHERE account_id = ?").bind(hash, acct.account_id).run();
}

/** Return the per-account salt so the client can derive auth_credential and K. */
export async function getSalt(db: D1Database, email: string): Promise<string> {
  const acct = await byEmail(db, email);
  if (!acct) throw new RegistryError(404, "unknown account");
  return acct.salt;
}

/**
 * Verify a presented auth_credential against the stored hash and return the
 * account. This is the password gate for device enrollment (SPEC §7.11).
 */
export async function verifyAuth(
  db: D1Database,
  email: string,
  authCredentialB64: string,
): Promise<AccountRow> {
  const acct = await byEmail(db, email);
  if (!acct || !acct.auth_hash) throw new RegistryError(403, "bad credentials");
  const presented = await hashAuthCredential(authCredentialB64);
  if (!constantTimeEqual(presented, acct.auth_hash)) throw new RegistryError(403, "bad credentials");
  return acct;
}

/**
 * Account-credential helpers (SPEC §7.3, §7.11).
 *
 * The client uploads `auth_credential` = PBKDF2-SHA256(password, salt), a 32-byte
 * high-entropy value, base64-encoded, over TLS. The server stores only a hash of
 * it (never the credential, never the password, never the encryption key K). A
 * single SHA-256 is sufficient here precisely because the input is already a
 * 256-bit PBKDF2 output — there is no low-entropy password to rainbow-table; an
 * attacker must break PBKDF2 first (sync-crypto §2, the two-KDF split).
 */

import { b64ToBytes } from "./crypto";

function toHex(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += b.toString(16).padStart(2, "0");
  return s;
}

/** Server-side hash of the client's base64 auth_credential (SPEC §7.10 auth_hash). */
export async function hashAuthCredential(authCredentialB64: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", b64ToBytes(authCredentialB64));
  return toHex(new Uint8Array(digest));
}

/** Length-independent constant-time string compare (avoids auth-hash timing leaks). */
export function constantTimeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

const CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";

/**
 * A ULID: 48-bit timestamp + 80-bit randomness, Crockford base32, sortable by
 * creation time (matches the note-id scheme the client uses via python-ulid).
 */
export function ulid(now = Date.now()): string {
  let ts = "";
  let t = now;
  for (let i = 9; i >= 0; i--) {
    ts = CROCKFORD[t % 32] + ts;
    t = Math.floor(t / 32);
  }
  const rand = crypto.getRandomValues(new Uint8Array(16));
  let r = "";
  for (let i = 0; i < 16; i++) r += CROCKFORD[rand[i] % 32];
  return ts + r;
}

/** Base64 of N fresh random bytes (per-account salt, §7.3). */
export function randomSaltB64(len = 16): string {
  const bytes = crypto.getRandomValues(new Uint8Array(len));
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

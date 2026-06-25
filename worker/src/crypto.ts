/**
 * Server-side verification primitives (SPEC §7.5).
 *
 * The server's only cryptographic job is to verify that a relayed op was signed
 * by a registered device. It never decrypts: `blob` is opaque ciphertext
 * (nonce || AES-256-GCM(ct) || tag) the server cannot read (SPEC §7.6).
 *
 * The bytes a device signs are defined by the shipped client in
 * `core/wire.py::_signing_input`:
 *
 *     signing_input = utf8(device_id) || utf8(client_ts) || blob_raw
 *
 * This module reproduces that construction byte-for-byte. The cross-language
 * golden vector in test/golden.json (signed by the Python client) guards the
 * compatibility.
 */

/** Decode standard base64 (the wire encoding for `blob` and `signature`). */
export function b64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

/** Concatenate the exact bytes a device signs (matches core/wire._signing_input). */
export function signingInput(deviceId: string, clientTs: string, blob: Uint8Array): Uint8Array {
  const enc = new TextEncoder();
  const a = enc.encode(deviceId);
  const b = enc.encode(clientTs);
  const out = new Uint8Array(a.length + b.length + blob.length);
  out.set(a, 0);
  out.set(b, a.length);
  out.set(blob, a.length + b.length);
  return out;
}

/** Import a raw 32-byte Ed25519 public key for verification. */
async function importPublicKey(rawPubkey: Uint8Array): Promise<CryptoKey> {
  return crypto.subtle.importKey("raw", rawPubkey, { name: "Ed25519" }, false, ["verify"]);
}

/**
 * Verify an Ed25519 signature over `device_id || client_ts || blob`.
 *
 * Returns false (never throws) on any failure, so the caller can map it to a
 * clean 403 without leaking which step failed.
 */
export async function verifySignature(
  pubkeyB64: string,
  deviceId: string,
  clientTs: string,
  blob: Uint8Array,
  signatureB64: string,
): Promise<boolean> {
  try {
    const key = await importPublicKey(b64ToBytes(pubkeyB64));
    const sig = b64ToBytes(signatureB64);
    const msg = signingInput(deviceId, clientTs, blob);
    return await crypto.subtle.verify("Ed25519", key, sig, msg);
  } catch {
    return false;
  }
}

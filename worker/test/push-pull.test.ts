/**
 * Push / pull integration tests against the real workerd runtime (Miniflare).
 *
 * The golden-vector test feeds an envelope SIGNED BY THE PYTHON CLIENT to the
 * worker and asserts it verifies — proving the server's signing-input bytes
 * match core/wire._signing_input across languages. The rest generate keypairs
 * in-test to exercise ordering, skew, tamper, and revocation.
 *
 * Golden vector regeneration (Python, in the repo venv):
 *   seed = bytes(range(32)); from core import crypto, wire
 *   blob = bytes(range(60)); si = wire._signing_input(device_id, client_ts, blob)
 *   sig = crypto.sign(seed, si)   # base64 it; pubkey = Ed25519(seed).public raw
 */

import { SELF, env, applyD1Migrations } from "cloudflare:test";
import { beforeAll, describe, it, expect } from "vitest";
import { signingInput } from "../src/crypto";
import golden from "./golden.json";

beforeAll(async () => {
  // Apply the accounts schema to the test D1 (SPEC §7.10).
  const e = env as {
    DB: D1Database;
    TEST_MIGRATIONS: Parameters<typeof applyD1Migrations>[1];
  };
  await applyD1Migrations(e.DB, e.TEST_MIGRATIONS);
});

// Each test registers its own account (unique email -> unique account_id ->
// fresh per-account DO), which is the natural isolation here.
const AUTH_CRED = btoa("test-auth-credential-32bytes...."); // any base64; opaque to server

async function setupAccount(): Promise<{ email: string; accountId: string }> {
  const email = `u-${crypto.randomUUID()}@test`;
  const created = (await (
    await SELF.fetch("https://x/account", { method: "POST", body: JSON.stringify({ email }) })
  ).json()) as { account_id: string };
  await SELF.fetch("https://x/account/auth", {
    method: "PUT",
    body: JSON.stringify({ email, auth_credential: AUTH_CRED }),
  });
  return { email, accountId: created.account_id };
}

async function enroll(email: string, deviceId: string, pubkeyB64: string) {
  return SELF.fetch("https://x/devices", {
    method: "POST",
    body: JSON.stringify({ email, auth_credential: AUTH_CRED, device_id: deviceId, pubkey: pubkeyB64 }),
  });
}

function bytesToB64(b: Uint8Array): string {
  let s = "";
  for (const byte of b) s += String.fromCharCode(byte);
  return btoa(s);
}

async function makeDevice(deviceId: string) {
  const kp = (await crypto.subtle.generateKey({ name: "Ed25519" }, true, [
    "sign",
    "verify",
  ])) as CryptoKeyPair;
  const rawPub = new Uint8Array(
    (await crypto.subtle.exportKey("raw", kp.publicKey)) as ArrayBuffer,
  );
  return { deviceId, kp, pubkeyB64: bytesToB64(rawPub) };
}

async function signEnvelope(
  dev: { deviceId: string; kp: CryptoKeyPair },
  clientTs: string,
  blob: Uint8Array,
) {
  const msg = signingInput(dev.deviceId, clientTs, blob);
  const sig = new Uint8Array(await crypto.subtle.sign("Ed25519", dev.kp.privateKey, msg));
  return {
    device_id: dev.deviceId,
    client_ts: clientTs,
    blob: bytesToB64(blob),
    signature: bytesToB64(sig),
  };
}

async function push(account: string, envelope: object) {
  return SELF.fetch("https://x/push", {
    method: "POST",
    body: JSON.stringify({ account_id: account, ...envelope }),
  });
}

async function pull(account: string, after = 0) {
  const res = await SELF.fetch(`https://x/ops?account=${account}&after=${after}`);
  return (await res.json()) as { ops: Array<Record<string, unknown>> };
}

const nowTs = () => new Date().toISOString();

describe("cross-language compatibility", () => {
  it("verifies an envelope signed by the Python client (golden vector)", async () => {
    const { email, accountId } = await setupAccount();
    await enroll(email, golden.device_id, golden.device_pubkey_b64);
    const res = await push(accountId, {
      device_id: golden.device_id,
      client_ts: golden.client_ts, // late ts is accepted (SPEC §7.2)
      blob: golden.blob_b64,
      signature: golden.signature_b64,
    });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ op_id: 1 });
  });
});

describe("push assigns gapless op_id and pull returns in order", () => {
  it("op_ids are 1,2,3 and pull respects the cursor", async () => {
    const { email, accountId } = await setupAccount();
    const dev = await makeDevice("dev_seq");
    await enroll(email, dev.deviceId, dev.pubkeyB64);

    for (let i = 1; i <= 3; i++) {
      const env = await signEnvelope(dev, nowTs(), new Uint8Array([i, i, i]));
      const res = await push(accountId, env);
      expect(await res.json()).toEqual({ op_id: i });
    }

    const all = await pull(accountId, 0);
    expect(all.ops.map((o) => o.op_id)).toEqual([1, 2, 3]);
    expect(all.ops[0]).toMatchObject({ device_id: "dev_seq", op_id: 1 });
    expect(all.ops[0]).toHaveProperty("server_ts");

    const tail = await pull(accountId, 1);
    expect(tail.ops.map((o) => o.op_id)).toEqual([2, 3]);
  });
});

describe("rejections", () => {
  it("rejects a push from an unenrolled device (403)", async () => {
    const { accountId } = await setupAccount();
    const dev = await makeDevice("dev_ghost"); // never enrolled
    const env = await signEnvelope(dev, nowTs(), new Uint8Array([9]));
    const res = await push(accountId, env);
    expect(res.status).toBe(403);
  });

  it("rejects a tampered signature (403)", async () => {
    const { email, accountId } = await setupAccount();
    const dev = await makeDevice("dev_tamper");
    await enroll(email, dev.deviceId, dev.pubkeyB64);
    const env = await signEnvelope(dev, nowTs(), new Uint8Array([1, 2, 3]));
    env.blob = bytesToB64(new Uint8Array([9, 9, 9])); // swap blob, keep old signature
    const res = await push(accountId, env);
    expect(res.status).toBe(403);
  });

  it("rejects client_ts too far in the future (409)", async () => {
    const { email, accountId } = await setupAccount();
    const dev = await makeDevice("dev_skew");
    await enroll(email, dev.deviceId, dev.pubkeyB64);
    const future = new Date(Date.now() + 10 * 60 * 1000).toISOString(); // +10 min
    const env = await signEnvelope(dev, future, new Uint8Array([1]));
    const res = await push(accountId, env);
    expect(res.status).toBe(409);
  });

  it("rejects a revoked device (403)", async () => {
    const { email, accountId } = await setupAccount();
    const dev = await makeDevice("dev_revoked");
    await enroll(email, dev.deviceId, dev.pubkeyB64);
    await SELF.fetch("https://x/devices/revoke", {
      method: "POST",
      body: JSON.stringify({ account_id: accountId, device_id: dev.deviceId }),
    });
    const env = await signEnvelope(dev, nowTs(), new Uint8Array([1]));
    const res = await push(accountId, env);
    expect(res.status).toBe(403);
  });
});

// --- Account lifecycle + device enrollment (SPEC §7.11) ---
// The password (auth_credential) gates registration and enrollment only; K and
// the password never reach the server, which stores only the auth_credential hash.

const AUTH = btoa("correct-horse-battery-staple-32b");
const WRONG = btoa("wrong-credential-................");
const freshEmail = () => `u-${crypto.randomUUID()}@test`;

const rawCreate = (email: string) =>
  SELF.fetch("https://x/account", { method: "POST", body: JSON.stringify({ email }) });
const rawSetAuth = (email: string, auth_credential: string) =>
  SELF.fetch("https://x/account/auth", { method: "PUT", body: JSON.stringify({ email, auth_credential }) });
const rawSalt = (email: string) =>
  SELF.fetch(`https://x/account/salt?email=${encodeURIComponent(email)}`);
const rawEnroll = (email: string, auth_credential: string, device_id: string, pubkey = "AAAA") =>
  SELF.fetch("https://x/devices", {
    method: "POST",
    body: JSON.stringify({ email, auth_credential, device_id, pubkey }),
  });

describe("account lifecycle", () => {
  it("creates an account and returns account_id + salt", async () => {
    const res = await rawCreate(freshEmail());
    expect(res.status).toBe(200);
    const body = (await res.json()) as { account_id: string; salt: string };
    expect(body.account_id).toBeTruthy();
    expect(body.salt).toBeTruthy();
  });

  it("rejects a duplicate email (409)", async () => {
    const email = freshEmail();
    expect((await rawCreate(email)).status).toBe(200);
    expect((await rawCreate(email)).status).toBe(409);
  });

  it("returns the same salt on lookup, 404 for unknown email", async () => {
    const email = freshEmail();
    const created = (await (await rawCreate(email)).json()) as { salt: string };
    const looked = (await (await rawSalt(email)).json()) as { salt: string };
    expect(looked.salt).toBe(created.salt);
    expect((await rawSalt("nobody@test")).status).toBe(404);
  });
});

describe("device enrollment (password gate)", () => {
  it("enrolls a device when auth_credential is correct", async () => {
    const email = freshEmail();
    await rawCreate(email);
    await rawSetAuth(email, AUTH);
    const res = await rawEnroll(email, AUTH, "dev_ok");
    expect(res.status).toBe(200);
    const body = (await res.json()) as { account_id: string; device_id: string };
    expect(body.device_id).toBe("dev_ok");
    expect(body.account_id).toBeTruthy();
  });

  it("rejects enrollment with a wrong auth_credential (403)", async () => {
    const email = freshEmail();
    await rawCreate(email);
    await rawSetAuth(email, AUTH);
    expect((await rawEnroll(email, WRONG, "dev_bad")).status).toBe(403);
  });

  it("rejects enrollment before auth is set (403)", async () => {
    const email = freshEmail();
    await rawCreate(email); // no setAuth -> auth_hash is null
    expect((await rawEnroll(email, AUTH, "dev_premature")).status).toBe(403);
  });
});

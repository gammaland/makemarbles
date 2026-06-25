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

import { SELF } from "cloudflare:test";
import { describe, it, expect } from "vitest";
import { signingInput } from "../src/crypto";
import golden from "./golden.json";

// Each test uses a unique account so its per-account DO is fresh (the topology
// is the isolation; see vitest.config.ts).
const freshAccount = () => "acc_" + crypto.randomUUID();

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

async function register(account: string, deviceId: string, pubkeyB64: string) {
  return SELF.fetch("https://x/devices", {
    method: "POST",
    body: JSON.stringify({ account_id: account, device_id: deviceId, pubkey: pubkeyB64 }),
  });
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
    await register(golden.account_id, golden.device_id, golden.device_pubkey_b64);
    const res = await push(golden.account_id, {
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
    const account = freshAccount();
    const dev = await makeDevice("dev_seq");
    await register(account, dev.deviceId, dev.pubkeyB64);

    for (let i = 1; i <= 3; i++) {
      const env = await signEnvelope(dev, nowTs(), new Uint8Array([i, i, i]));
      const res = await push(account, env);
      expect(await res.json()).toEqual({ op_id: i });
    }

    const all = await pull(account, 0);
    expect(all.ops.map((o) => o.op_id)).toEqual([1, 2, 3]);
    expect(all.ops[0]).toMatchObject({ device_id: "dev_seq", op_id: 1 });
    expect(all.ops[0]).toHaveProperty("server_ts");

    const tail = await pull(account, 1);
    expect(tail.ops.map((o) => o.op_id)).toEqual([2, 3]);
  });
});

describe("rejections", () => {
  it("rejects a push from an unregistered device (403)", async () => {
    const account = freshAccount();
    const dev = await makeDevice("dev_ghost");
    const env = await signEnvelope(dev, nowTs(), new Uint8Array([9]));
    const res = await push(account, env); // never registered
    expect(res.status).toBe(403);
  });

  it("rejects a tampered signature (403)", async () => {
    const account = freshAccount();
    const dev = await makeDevice("dev_tamper");
    await register(account, dev.deviceId, dev.pubkeyB64);
    const env = await signEnvelope(dev, nowTs(), new Uint8Array([1, 2, 3]));
    env.blob = bytesToB64(new Uint8Array([9, 9, 9])); // swap blob, keep old signature
    const res = await push(account, env);
    expect(res.status).toBe(403);
  });

  it("rejects client_ts too far in the future (409)", async () => {
    const account = freshAccount();
    const dev = await makeDevice("dev_skew");
    await register(account, dev.deviceId, dev.pubkeyB64);
    const future = new Date(Date.now() + 10 * 60 * 1000).toISOString(); // +10 min
    const env = await signEnvelope(dev, future, new Uint8Array([1]));
    const res = await push(account, env);
    expect(res.status).toBe(409);
  });

  it("rejects a revoked device (403)", async () => {
    const account = freshAccount();
    const dev = await makeDevice("dev_revoked");
    await register(account, dev.deviceId, dev.pubkeyB64);
    await SELF.fetch("https://x/devices/revoke", {
      method: "POST",
      body: JSON.stringify({ account_id: account, device_id: dev.deviceId }),
    });
    const env = await signEnvelope(dev, nowTs(), new Uint8Array([1]));
    const res = await push(account, env);
    expect(res.status).toBe(403);
  });
});

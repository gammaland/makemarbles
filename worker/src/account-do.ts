/**
 * AccountDO — one Durable Object per account (SPEC §7.9, ADR 2026-06-24).
 *
 * A DO serializes every request to it, so the monotonic `op_id` counter is
 * gapless without locks (SPEC §7.2). This object owns, in its transactional
 * storage:
 *
 *   next_op_id          integer counter, starts at 1
 *   op:{padded op_id}   { device_id, client_ts, server_ts, blob, signature }
 *   device:{device_id}  { pubkey, created_at, revoked }
 *
 * The server is a sealed relay (SPEC §7.6): `blob` and `signature` are opaque
 * base64; no content, note_id, or op type is ever visible here.
 *
 * This first slice implements push, pull, and the minimal device registry that
 * push verification needs. The login handshake / JWT gating (SPEC §7.11) and
 * the live WebSocket fan-out (SPEC §7.9) land in later slices; the endpoints
 * here take `account_id` from the request until the edge derives it from a
 * validated session token.
 */

import { verifySignature, b64ToBytes } from "./crypto";

/** Max seconds a client_ts may lead the server clock before rejection (SPEC §7.2). */
const SKEW_LIMIT_SECONDS = 300;
/** Max envelope blob size (SPEC §7.13). */
const MAX_BLOB_BYTES = 1024 * 1024;
/** Zero-pad width so `op:` keys sort lexicographically by op_id. */
const OP_ID_WIDTH = 16;

const opKey = (opId: number) => `op:${String(opId).padStart(OP_ID_WIDTH, "0")}`;
const deviceKey = (deviceId: string) => `device:${deviceId}`;

interface StoredDevice {
  pubkey: string; // base64 raw Ed25519 public key
  created_at: string;
  revoked: boolean;
}

interface StoredOp {
  op_id: number;
  device_id: string;
  client_ts: string;
  server_ts: string;
  blob: string; // base64, opaque
  signature: string; // base64
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

export class AccountDO {
  constructor(private state: DurableObjectState) {}

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    try {
      if (request.method === "POST" && url.pathname === "/devices") {
        return await this.registerDevice(request);
      }
      if (request.method === "POST" && url.pathname === "/devices/revoke") {
        return await this.revokeDevice(request);
      }
      if (request.method === "POST" && url.pathname === "/push") {
        return await this.push(request);
      }
      if (request.method === "GET" && url.pathname === "/ops") {
        return await this.pull(url);
      }
      return json({ error: "not found" }, 404);
    } catch (err) {
      return json({ error: "internal", detail: String(err) }, 500);
    }
  }

  /** Register (or re-assert) a device's Ed25519 public key. Idempotent per device. */
  private async registerDevice(request: Request): Promise<Response> {
    const body = (await request.json()) as { device_id?: string; pubkey?: string };
    if (!body.device_id || !body.pubkey) {
      return json({ error: "device_id and pubkey required" }, 400);
    }
    const existing = await this.state.storage.get<StoredDevice>(deviceKey(body.device_id));
    if (existing && !existing.revoked) {
      // Re-login on a known device: keep the registration as-is.
      return json({ device_id: body.device_id, status: "exists" });
    }
    const device: StoredDevice = {
      pubkey: body.pubkey,
      created_at: new Date().toISOString(),
      revoked: false,
    };
    await this.state.storage.put(deviceKey(body.device_id), device);
    return json({ device_id: body.device_id, status: "registered" });
  }

  /** Mark a device revoked; its future ops are rejected at push (SPEC §7.3). */
  private async revokeDevice(request: Request): Promise<Response> {
    const body = (await request.json()) as { device_id?: string };
    if (!body.device_id) return json({ error: "device_id required" }, 400);
    const device = await this.state.storage.get<StoredDevice>(deviceKey(body.device_id));
    if (!device) return json({ error: "unknown device" }, 404);
    device.revoked = true;
    await this.state.storage.put(deviceKey(body.device_id), device);
    return json({ device_id: body.device_id, status: "revoked" });
  }

  /**
   * Accept one push envelope (SPEC §7.5 / §7.12). Serial by virtue of the DO:
   * verify device -> verify signature -> skew check -> assign op_id -> store.
   * Returns the assigned op_id, which the client records as its high-water mark.
   */
  private async push(request: Request): Promise<Response> {
    const env = (await request.json()) as {
      device_id?: string;
      client_ts?: string;
      blob?: string;
      signature?: string;
    };
    if (!env.device_id || !env.client_ts || !env.blob || !env.signature) {
      return json({ error: "malformed envelope" }, 400);
    }

    const device = await this.state.storage.get<StoredDevice>(deviceKey(env.device_id));
    if (!device) return json({ error: "unknown device" }, 403);
    if (device.revoked) return json({ error: "device revoked" }, 403);

    const blob = b64ToBytes(env.blob);
    if (blob.length > MAX_BLOB_BYTES) return json({ error: "blob too large" }, 413);

    const ok = await verifySignature(device.pubkey, env.device_id, env.client_ts, blob, env.signature);
    if (!ok) return json({ error: "bad signature" }, 403);

    // Clock-skew guard (SPEC §7.2): reject far-future client_ts; accept late.
    const clientMs = Date.parse(env.client_ts);
    if (Number.isNaN(clientMs)) return json({ error: "bad client_ts" }, 400);
    const nowMs = Date.now();
    if (clientMs - nowMs > SKEW_LIMIT_SECONDS * 1000) {
      return json({ error: "client_ts too far ahead" }, 409);
    }

    // Assign the next gapless op_id and persist atomically.
    const nextOpId = ((await this.state.storage.get<number>("next_op_id")) ?? 1);
    const serverTs = new Date(nowMs).toISOString();
    const op: StoredOp = {
      op_id: nextOpId,
      device_id: env.device_id,
      client_ts: env.client_ts,
      server_ts: serverTs,
      blob: env.blob,
      signature: env.signature,
    };
    await this.state.storage.put(opKey(nextOpId), op);
    await this.state.storage.put("next_op_id", nextOpId + 1);

    // (Live WebSocket fan-out lands in a later slice — SPEC §7.9.)
    return json({ op_id: nextOpId });
  }

  /** Return ops with op_id > after, in order, in the pull wire format (SPEC §7.5). */
  private async pull(url: URL): Promise<Response> {
    const after = Number(url.searchParams.get("after") ?? "0");
    if (!Number.isInteger(after) || after < 0) return json({ error: "bad after" }, 400);

    const map = await this.state.storage.list<StoredOp>({
      prefix: "op:",
      start: opKey(after + 1),
    });
    const ops = [...map.values()].map((op) => ({
      op_id: op.op_id,
      server_ts: op.server_ts,
      device_id: op.device_id,
      client_ts: op.client_ts,
      blob: op.blob,
      signature: op.signature,
    }));
    return json({ ops });
  }
}

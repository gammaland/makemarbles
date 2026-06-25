/**
 * Worker entry — account registry at the edge (D1), op relay in per-account
 * Durable Objects (SPEC §7.9–§7.11, ADR 2026-06-24).
 *
 * Account lifecycle and the password-gated device enrollment run here against
 * D1. Push / pull / revoke are forwarded to the account's DO (named by
 * `account_id`), where the gapless `op_id` and op store live.
 *
 * Authentication (scheme B, SPEC §7.11): the master password (`auth_credential`)
 * gates registration and device enrollment only; the device Ed25519 key
 * authenticates everything ongoing. Push self-authenticates via its op
 * signature. The device-request-signature check for pull / revoke is the next
 * slice; those routes still take `account_id` from the request for now.
 */

import { AccountDO } from "./account-do";
import * as registry from "./registry";
import { RegistryError } from "./registry";

export { AccountDO };

interface Env {
  ACCOUNT_DO: DurableObjectNamespace;
  DB: D1Database;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function fromRegistryError(err: unknown): Response {
  if (err instanceof RegistryError) return json({ error: err.message }, err.status);
  throw err;
}

/** Forward a request to an account's Durable Object, preserving path + query + body. */
function toAccountDO(env: Env, accountId: string, url: URL, method: string, bodyText: string | null): Promise<Response> {
  const stub = env.ACCOUNT_DO.get(env.ACCOUNT_DO.idFromName(accountId));
  return stub.fetch(new Request(url.toString(), { method, headers: { "content-type": "application/json" }, body: bodyText }));
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const { pathname } = url;
    const method = request.method;

    try {
      // --- Account lifecycle (D1, SPEC §7.11) ---

      if (method === "POST" && pathname === "/account") {
        const { email } = (await request.json()) as { email?: string };
        if (!email) return json({ error: "email required" }, 400);
        try {
          return json(await registry.createAccount(env.DB, email));
        } catch (e) {
          return fromRegistryError(e);
        }
      }

      if (method === "PUT" && pathname === "/account/auth") {
        const { email, auth_credential } = (await request.json()) as {
          email?: string;
          auth_credential?: string;
        };
        if (!email || !auth_credential) return json({ error: "email and auth_credential required" }, 400);
        try {
          await registry.setAuth(env.DB, email, auth_credential);
          return json({ status: "ok" });
        } catch (e) {
          return fromRegistryError(e);
        }
      }

      if (method === "GET" && pathname === "/account/salt") {
        const email = url.searchParams.get("email");
        if (!email) return json({ error: "email required" }, 400);
        try {
          return json({ salt: await registry.getSalt(env.DB, email) });
        } catch (e) {
          return fromRegistryError(e);
        }
      }

      // --- Device enrollment: gated by the password (SPEC §7.11) ---

      if (method === "POST" && pathname === "/devices") {
        const { email, auth_credential, device_id, pubkey } = (await request.json()) as {
          email?: string;
          auth_credential?: string;
          device_id?: string;
          pubkey?: string;
        };
        if (!email || !auth_credential || !device_id || !pubkey) {
          return json({ error: "email, auth_credential, device_id, pubkey required" }, 400);
        }
        let account;
        try {
          account = await registry.verifyAuth(env.DB, email, auth_credential);
        } catch (e) {
          return fromRegistryError(e);
        }
        // Password proven: store the client-generated device_id + pubkey in the
        // account DO. The DO rejects a duplicate id that is already active.
        const res = await toAccountDO(
          env,
          account.account_id,
          new URL("/devices", url),
          "POST",
          JSON.stringify({ device_id, pubkey }),
        );
        if (!res.ok) return res;
        return json({ account_id: account.account_id, device_id });
      }

      // --- Op relay, forwarded to the account DO by account_id ---

      if (method === "POST" && pathname === "/push") {
        const bodyText = await request.text();
        let accountId: string | undefined;
        try {
          accountId = (JSON.parse(bodyText) as { account_id?: string }).account_id;
        } catch {
          return json({ error: "invalid JSON" }, 400);
        }
        if (!accountId) return json({ error: "account_id required" }, 400);
        return toAccountDO(env, accountId, url, "POST", bodyText);
      }

      if (method === "GET" && pathname === "/ops") {
        const accountId = url.searchParams.get("account");
        if (!accountId) return json({ error: "account required" }, 400);
        return toAccountDO(env, accountId, url, "GET", null);
      }

      if (method === "POST" && pathname === "/devices/revoke") {
        const bodyText = await request.text();
        let accountId: string | undefined;
        try {
          accountId = (JSON.parse(bodyText) as { account_id?: string }).account_id;
        } catch {
          return json({ error: "invalid JSON" }, 400);
        }
        if (!accountId) return json({ error: "account_id required" }, 400);
        return toAccountDO(env, accountId, url, "POST", bodyText);
      }

      return json({ error: "not found" }, 404);
    } catch (err) {
      return json({ error: "internal", detail: String(err) }, 500);
    }
  },
};

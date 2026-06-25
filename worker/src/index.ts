/**
 * Worker entry — routes each request to its per-account Durable Object
 * (SPEC §7.9, ADR 2026-06-24).
 *
 * The DO name is the `account_id`, so all of one account's ops are serialized
 * through a single object and `op_id` stays gapless (SPEC §7.2). This first
 * slice derives `account_id` from the request body (POST) or query (GET). The
 * next slice replaces that with a session token validated here at the edge
 * before the request is forwarded (SPEC §7.11), plus the `is_pro` entitlement
 * gate (SPEC §7.13).
 */

import { AccountDO } from "./account-do";

export { AccountDO };

interface Env {
  ACCOUNT_DO: DurableObjectNamespace;
}

function bad(msg: string, status = 400): Response {
  return new Response(JSON.stringify({ error: msg }), {
    status,
    headers: { "content-type": "application/json" },
  });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    let accountId: string | null = null;
    let bodyText: string | null = null;

    if (request.method === "POST") {
      bodyText = await request.text();
      try {
        accountId = (JSON.parse(bodyText) as { account_id?: string }).account_id ?? null;
      } catch {
        return bad("invalid JSON body");
      }
    } else if (request.method === "GET") {
      accountId = url.searchParams.get("account");
    }

    if (!accountId) return bad("account_id required");

    // Forward to the account's DO, preserving path + query and the read body.
    const stub = env.ACCOUNT_DO.get(env.ACCOUNT_DO.idFromName(accountId));
    const forwarded = new Request(url.toString(), {
      method: request.method,
      headers: request.headers,
      body: bodyText,
    });
    return stub.fetch(forwarded);
  },
};

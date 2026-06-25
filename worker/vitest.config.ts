import { defineWorkersConfig } from "@cloudflare/vitest-pool-workers/config";

// Runs tests inside the real workerd runtime (via Miniflare) so Durable Objects,
// SQLite storage, and Web Crypto Ed25519 behave exactly as in production.
export default defineWorkersConfig({
  test: {
    poolOptions: {
      workers: {
        // SQLite-backed DOs don't play well with the pool's isolated-storage
        // stacking; instead each test uses a unique account_id, so its
        // per-account DO is naturally fresh (ADR 2026-06-24 topology).
        isolatedStorage: false,
        wrangler: { configPath: "./wrangler.toml" },
      },
    },
  },
});

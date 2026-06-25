import { defineWorkersConfig, readD1Migrations } from "@cloudflare/vitest-pool-workers/config";

// Read the D1 migrations once and expose them to tests as TEST_MIGRATIONS so
// each suite can apply the accounts schema to its local D1 (SPEC §7.10).
const migrations = await readD1Migrations("./migrations");

// Runs tests inside the real workerd runtime (via Miniflare) so Durable Objects,
// SQLite storage, D1, and Web Crypto Ed25519 behave exactly as in production.
export default defineWorkersConfig({
  test: {
    poolOptions: {
      workers: {
        // Isolated storage can't cleanly track DO storage across the edge->DO
        // RPC boundary in this pool version, so we disable it and isolate by
        // construction instead: every test registers its own account (unique
        // email -> unique account_id -> fresh per-account DO, op_id from 1).
        // This requires all suites in one file (one project worker => the D1 is
        // registered once).
        isolatedStorage: false,
        miniflare: {
          bindings: { TEST_MIGRATIONS: migrations },
        },
        wrangler: { configPath: "./wrangler.toml" },
      },
    },
  },
});

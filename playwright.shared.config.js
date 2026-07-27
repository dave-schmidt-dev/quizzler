// @ts-check
const { defineConfig } = require("@playwright/test");
const crypto = require("crypto");
const os = require("os");
const path = require("path");

// This config is loaded before global setup and inherited by test workers.  A
// per-run path prevents a stale state file from a previous interrupted run
// from making real-server tests target the wrong server.
if (!process.env.QUIZZLER_SHARED_STATE_FILE) {
  process.env.QUIZZLER_SHARED_STATE_FILE = path.join(
    os.tmpdir(),
    "quizzler-shared-state-" + process.pid + "-" + crypto.randomUUID() + ".json"
  );
}

module.exports = defineConfig({
  testDir: "./tests",
  testMatch: ["**/shared-progress-real.spec.js"],
  timeout: 20000,
  retries: 0,
  fullyParallel: false,
  workers: 1,

  use: {
    headless: true,
  },

  globalSetup: require.resolve("./scripts/playwright-shared-setup.js"),
  globalTeardown: require.resolve("./scripts/playwright-shared-teardown.js"),

  projects: [
    { name: "desktop", use: { viewport: { width: 1280, height: 720 } } },
    { name: "mobile", use: { viewport: { width: 390, height: 844 } } },
  ],
});

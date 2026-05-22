const { defineConfig } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "./tests",
  timeout: 15000,
  retries: 0,
  fullyParallel: true,
  use: {
    headless: true,
    baseURL: "http://localhost:8787",
  },
  webServer: {
    // Rebuild the manifest before serving so tests run against the current
    // folder layout, not a stale committed manifest.
    command: "python3 scripts/build_manifest.py && python3 -m http.server 8787",
    port: 8787,
    reuseExistingServer: true,
  },
});

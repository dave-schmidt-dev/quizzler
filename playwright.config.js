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
    // folder layout, not a stale committed manifest. Use --no-strict so a
    // Layer-A pack critical can't exit the build non-zero and stop the app
    // server from starting — the E2E suite tests the app, not pack content
    // (pack quality has its own coverage in pack-quality.spec.js + the Python
    // suites).
    command: "python3 scripts/build_manifest.py --no-strict && python3 -m http.server 8787 --bind 127.0.0.1",
    port: 8787,
    reuseExistingServer: true,
  },
});

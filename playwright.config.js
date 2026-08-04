const { defineConfig } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "./tests",
  testIgnore: ["**/settings-toggle.spec.js"],
  timeout: 15000,
  retries: 0,
  fullyParallel: true,
  workers: 16,
  use: {
    headless: true,
    baseURL: "http://localhost:8787",
  },
  webServer: {
    // Rebuild the manifest before serving so tests run against the current
    // folder layout, not a stale committed manifest. Use --no-strict so a
    // Layer-A pack critical can't exit the build non-zero and stop the app
    // server from starting. The explicit course-size preview flag is test-only;
    // normal installation cannot bypass that hard workload ceiling. The E2E
    // suite tests the app, not pack content (pack quality has its own coverage
    // in pack-quality.spec.js + the Python suites).
    command: "python3 scripts/build_manifest.py --no-strict --allow-course-size-preview && python3 -m http.server 8787 --bind 127.0.0.1",
    port: 8787,
    // Reuse a server the dev already has running on this port instead of
    // restarting it; set the CI env var to force a fresh rebuild instead.
    reuseExistingServer: !process.env.CI,
  },
});

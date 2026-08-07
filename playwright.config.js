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
    // folder layout, not a stale committed manifest. Exit 1 means nothing
    // usable was installed; exit 2 is a documented partial install and still
    // leaves the browser suite with the passing packs to exercise.
    command: "python3 scripts/build_manifest.py; build_status=$?; if [ \"$build_status\" -ne 0 ] && [ \"$build_status\" -ne 2 ]; then exit \"$build_status\"; fi; exec python3 -m http.server 8787 --bind 127.0.0.1",
    env: {
      ...process.env,
      QUIZZLER_LINT_STRICT: "1",
    },
    port: 8787,
    // A running server may have a stale manifest, so every Playwright run must
    // execute the rebuild above before serving.
    reuseExistingServer: false,
  },
});

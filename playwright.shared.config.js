// @ts-check
const { defineConfig } = require("@playwright/test");

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

// @ts-check
//
// Sanity-check that each Playwright run starts from a freshly built server.

const { test, expect } = require("@playwright/test");
const config = require("../playwright.config.js");

test("webServer always rebuilds before the test run", () => {
  const { webServer } = config;
  expect(webServer).toBeDefined();
  expect(webServer.reuseExistingServer).toBe(false);
});

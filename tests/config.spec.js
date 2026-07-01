// @ts-check
//
// Sanity-check that playwright.config.js keeps reuseExistingServer wired to
// the CI environment variable rather than a hardcoded literal.  The pattern
// `!process.env.CI` means: always rebuild in CI, reuse a running server
// locally.

const { test, expect } = require("@playwright/test");
const config = require("../playwright.config.js");

test("webServer.reuseExistingServer equals !process.env.CI", () => {
  const { webServer } = config;
  expect(webServer).toBeDefined();
  // In the test runner environment process.env.CI may or may not be set, but
  // the value must equal the expression — not be a hardcoded true/false.
  expect(webServer.reuseExistingServer).toBe(!process.env.CI);
});

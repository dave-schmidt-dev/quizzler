// @ts-check
var { test, expect } = require("@playwright/test");
var fs = require("fs");
var PORTS = require("./browser-port.js");

var STATE_FILE = process.env.QUIZZLER_SHARED_STATE_FILE;
var REAL_SERVER = false;
try { REAL_SERVER = Boolean(STATE_FILE && fs.existsSync(STATE_FILE)); } catch (_) {}

test.beforeEach(async function () {
  if (!REAL_SERVER) test.skip();
});

function getBaseURL() {
  try {
    var state = JSON.parse(fs.readFileSync(STATE_FILE, "utf-8"));
    if (state.baseURL) return state.baseURL;
  } catch (_) {}
  return PORTS.loopbackURL;
}

async function pairDevice(page) {
  await page.goto(getBaseURL() + "/pair");
  await page.waitForLoadState("domcontentloaded");

  var pairLocalResp = await page.evaluate(async function () {
    var r = await fetch("/api/v1/auth/pair-local", { method: "POST" });
    return r.json();
  });
  expect(pairLocalResp.pairing_code).toBeTruthy();

  var pairResp = await page.evaluate(async function () {
    var r = await fetch("/api/v1/auth/pair-self", {
      method: "POST",
    });
    return { status: r.status, body: await r.json() };
  });
  expect(pairResp.status).toBe(200);
  return { csrfToken: pairResp.body.csrf_token };
}

async function routeAppWithAuthStatus(page, authStatus, csrfToken) {
  var appURL = getBaseURL() + "/app/";
  var response = await page.request.get(appURL);
  var html = await response.text();
  html = html.replace(
    /<meta name="quizzler-auth-status" content="[^"]*">/,
    '<meta name="quizzler-auth-status" content="' + authStatus + '">'
  );
  var headHtml = html.slice(0, html.indexOf("</head>"));
  if (csrfToken && !/<meta name="csrf-token"/.test(headHtml)) {
    html = html.replace("</head>", '<meta name="csrf-token" content="' + csrfToken + '">\n</head>');
  }
  await page.route(appURL, function (route) {
    return route.fulfill({status: 200, contentType: "text/html; charset=utf-8", body: html});
  });
}

/* ─── Settings Panel Toggle ─── */

test.describe("[UI] Settings Panel — Toggle", function () {
  test("settings panel opens and closes via gear icon", async function ({ page }) {
    await page.goto(getBaseURL() + "/app/");
    await page.waitForLoadState("domcontentloaded");

    await page.click("#settingsGear");

    var visible = await page.evaluate(function () {
      var panel = document.getElementById("settingsPanel");
      return panel.style.display !== "none";
    });
    expect(visible).toBe(true);

    await page.click("#settingsCloseBtn");

    var hidden = await page.evaluate(function () {
      var panel = document.getElementById("settingsPanel");
      return panel.style.display === "none";
    });
    expect(hidden).toBe(true);
  });

  test("settings panel shows local mode by default", async function ({ page }) {
    await page.goto(getBaseURL() + "/app/");
    await page.waitForLoadState("domcontentloaded");

    await page.click("#settingsGear");

    var modeText = await page.evaluate(function () {
      var el = document.getElementById("settingsModeText");
      return el ? el.textContent : null;
    });
    expect(modeText).toContain("Local Storage");

    var dotClass = await page.evaluate(function () {
      var el = document.getElementById("settingsModeDot");
      return el ? el.className : null;
    });
    expect(dotClass).toContain("local");
  });
});

/* ─── Boot Detection ─── */

test.describe("[UI] Settings Panel — Boot Detection", function () {
  test("auth-status meta tag is active when paired", async function ({ page }) {
    await pairDevice(page);

    await page.goto(getBaseURL() + "/app/");
    await page.waitForLoadState("domcontentloaded");

    var meta = await page.evaluate(function () {
      var authMeta = document.querySelector('meta[name="quizzler-auth-status"]');
      var csrfMeta = document.querySelector('meta[name="csrf-token"]');
      return {
        authStatus: authMeta ? authMeta.getAttribute("content") : null,
        csrfToken: csrfMeta ? csrfMeta.getAttribute("content") : null,
      };
    });
    expect(meta.authStatus).toBe("active");
    expect(meta.csrfToken).toBeTruthy();
    await expect(page.locator("#storageModeSurface")).toHaveAttribute("data-state", "shared");
    await expect(page.locator("#storageModeMessage")).toContainText("Shared progress");
  });

  test("unauthenticated page shows auth-status none", async function ({ page }) {
    await page.goto(getBaseURL() + "/app/");
    await page.waitForLoadState("domcontentloaded");

    var meta = await page.evaluate(function () {
      var authMeta = document.querySelector('meta[name="quizzler-auth-status"]');
      var csrfMeta = document.querySelector('meta[name="csrf-token"]');
      return {
        authStatus: authMeta ? authMeta.getAttribute("content") : null,
        csrfToken: csrfMeta ? csrfMeta.getAttribute("content") : null,
      };
    });
    expect(meta.authStatus).toBe("none");
    expect(meta.csrfToken).toBeNull();
  });

  // The other half of the absent-tag vs content="none" distinction (see
  // tests/quizzler.spec.js "Boot without a shared-progress server"): here the
  // shared server IS running and the device is simply unpaired, which is the
  // one case that should see the gate.
  test("unpaired device against a live shared server sees the boot pairing gate", async function ({ page }) {
    await page.goto(getBaseURL() + "/app/");
    await page.waitForLoadState("domcontentloaded");

    await expect(page.locator("#bootPairingGate")).toBeVisible();
    await expect(page.locator("#bootPairingInput")).toBeVisible();
    await expect(page.locator("#bootPairSkipBtn")).toBeVisible();
  });

  test("Use Local Storage dismisses the gate and boots the course list", async function ({ page }) {
    await page.goto(getBaseURL() + "/app/");
    await page.waitForLoadState("domcontentloaded");

    await page.locator("#bootPairSkipBtn").click();

    await expect(page.locator("#bootPairingGate")).toHaveCount(0);
    await expect(page.locator(".course-card").first()).toBeVisible();
    var storeReady = await page.evaluate(function () {
      return typeof progressStore === "object" && progressStore !== null;
    });
    expect(storeReady).toBe(true);
    await expect(page.locator("#storageModeSurface")).toHaveAttribute("data-state", "local");
    await expect(page.locator("#storageModeMessage")).toContainText("Local-only progress");
    await expect(page.locator("#storageModeMessage")).toContainText("available offline");
  });

  test("expired session shows local-only state and re-pair recovery outside Settings", async function ({ page }) {
    await routeAppWithAuthStatus(page, "expired");
    await page.goto(getBaseURL() + "/app/");

    await expect(page.locator("#storageModeSurface")).toHaveAttribute("data-state", "expired");
    await expect(page.locator("#storageModeMessage")).toContainText("local-only");
    await expect(page.locator("#storageModeAction")).toHaveText("Re-pair in Settings");
    await page.locator("#storageModeAction").click();
    await expect(page.locator("#settingsPanel")).toBeVisible();
    await expect(page.locator("#settingsToggleBtn")).toHaveText("Enable Shared Progress");
  });

  test("shared adapter error shows unavailable state with Settings recovery", async function ({ page }) {
    await routeAppWithAuthStatus(page, "active", "invalid-test-csrf");
    await page.route("**/api/v1/progress", function (route) {
      return route.fulfill({status: 503, contentType: "application/json", body: '{"error":"unavailable"}'});
    });
    await page.goto(getBaseURL() + "/app/");

    await expect(page.locator("#storageModeSurface")).toHaveAttribute("data-state", "unavailable");
    await expect(page.locator("#storageModeMessage")).toContainText("Shared progress unavailable");
    await expect(page.locator("#storageModeAction")).toHaveText("Settings");
    await page.locator("#storageModeAction").click();
    await expect(page.locator("#settingsPanel")).toBeVisible();
  });
});

/* ─── Settings Panel — Pairing UI ─── */

test.describe("[UI] Settings Panel — Pairing", function () {
  test("Enable Shared Progress button shows pairing UI", async function ({ page }) {
    await page.goto(getBaseURL() + "/app/");
    await page.waitForLoadState("domcontentloaded");

    await page.click("#settingsGear");

    await page.click("#settingsToggleBtn");

    await page.waitForSelector("#settingsPairing", { timeout: 5000 });

    var localBtnText = await page.evaluate(function () {
      var el = document.getElementById("pairLocalBtn");
      return el ? el.textContent : null;
    });
    expect(localBtnText).toBe("Generate pairing code");
  });
});

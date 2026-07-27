// @ts-check
var { test, expect } = require("@playwright/test");
var fs = require("fs");
var http = require("http");
var os = require("os");
var path = require("path");

var STATE_FILE = path.join(os.tmpdir(), "quizzler-shared-state.json");
var REAL_SERVER = false;
try { REAL_SERVER = fs.existsSync(STATE_FILE); } catch (_) {}

test.beforeEach(async function () {
  if (!REAL_SERVER) test.skip();
});

var STATE_FILE = path.join(os.tmpdir(), "quizzler-shared-state.json");

function getBaseURL() {
  try {
    var state = JSON.parse(fs.readFileSync(STATE_FILE, "utf-8"));
    if (state.baseURL) return state.baseURL;
  } catch (_) {}
  return "http://127.0.0.1:8787";
}

async function pairDevice(page) {
  await page.goto(getBaseURL() + "/pair");
  await page.waitForLoadState("domcontentloaded");

  var pairLocalResp = await page.evaluate(async function () {
    var r = await fetch("/api/v1/auth/pair-local", { method: "POST" });
    return r.json();
  });
  expect(pairLocalResp.pairing_code).toBeTruthy();

  var pairResp = await page.evaluate(async function (code) {
    var r = await fetch("/api/v1/auth/pair", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pairing_code: code }),
    });
    return { status: r.status, body: await r.json() };
  }, pairLocalResp.pairing_code);
  expect(pairResp.status).toBe(200);
  expect(pairResp.body.ok).toBe(true);
  expect(pairResp.body.csrf_token).toBeTruthy();

  var cookies = await page.context().cookies();
  var sessionCookie = cookies.find(function (c) { return c.name === "quizzler_session"; });
  expect(sessionCookie).toBeTruthy();

  return { csrfToken: pairResp.body.csrf_token };
}

async function getRevision(page) {
  var result = await page.evaluate(async function () {
    var r = await fetch("/api/v1/progress", { method: "GET", credentials: "include" });
    return r.json();
  });
  return result.revision;
}

async function setupContextPage(context) {
  var page = await context.newPage();
  await page.goto(getBaseURL() + "/pair");
  await page.waitForLoadState("domcontentloaded");
  return page;
}

/* ────────────────────────────────────────────────────────────────────────── */

test.describe("Real Server — Health", function () {
  test("healthz returns ok", async function () {
    var url = getBaseURL() + "/healthz";
    var result = await new Promise(function (resolve, reject) {
      http.get(url, function (res) {
        var data = "";
        res.on("data", function (chunk) { data += chunk; });
        res.on("end", function () { resolve({ status: res.statusCode, body: data }); });
      }).on("error", reject);
    });
    expect(result.status).toBe(200);
    var body = JSON.parse(result.body);
    expect(body.status).toBe("ok");
  });
});

/* ─── Cross-device Flow ─── */

test.describe("Real Server — Cross-device Flow", function () {
  test.use({ viewport: { width: 1280, height: 720 } });

  test("Mac pairs, phone pairs with same code, phone writes, Mac reads updated state", async function ({ browser }) {
    var desktopCtx = await browser.newContext({ viewport: { width: 1280, height: 720 } });
    var mobileCtx = await browser.newContext({ viewport: { width: 390, height: 844 } });
    var desktopPage = await setupContextPage(desktopCtx);
    var mobilePage = await setupContextPage(mobileCtx);

    var pairLocalResp = await desktopPage.evaluate(async function () {
      var r = await fetch("/api/v1/auth/pair-local", { method: "POST" });
      return r.json();
    });
    expect(pairLocalResp.pairing_code).toBeTruthy();
    var pairingCode = pairLocalResp.pairing_code;

    var macPair = await desktopPage.evaluate(async function (code) {
      var r = await fetch("/api/v1/auth/pair", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pairing_code: code }),
      });
      return { status: r.status, body: await r.json() };
    }, pairingCode);
    expect(macPair.status).toBe(200);
    var macCsrf = macPair.body.csrf_token;

    var phonePair = await mobilePage.evaluate(async function (code) {
      var r = await fetch("/api/v1/auth/pair", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pairing_code: code }),
      });
      return { status: r.status, body: await r.json() };
    }, pairingCode);
    expect(phonePair.status).toBe(200);
    var phoneCsrf = phonePair.body.csrf_token;

    var phoneRev = await getRevision(mobilePage);

    var quizResult = await mobilePage.evaluate(async function (opts) {
      var csrfToken = opts.csrfToken;
      var rev = opts.rev;
      var opId = "cross-device-op-" + Date.now();
      var session = {
        quiz_id: "cross-device-q1",
        course: "cross-course",
        score: { correct: 8, total: 10 },
        started_at: new Date().toISOString(),
        completed_at: new Date().toISOString(),
        pack_id: "cross-pack",
        mode: "normal",
      };
      var masteryDelta = {
        seen: { "q1": true, "q2": true, "q3": true },
        correct: { "q1": true, "q2": true },
        consecutive: { "q1": 1, "q2": 2 },
      };
      var r = await fetch("/api/v1/progress/quiz-completed", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          expected_revision: rev,
          operation_id: opId,
          session: session,
          course_id: "cross-course",
          pack_id: "cross-pack",
          mastery_delta: masteryDelta,
          csrf_token: csrfToken,
        }),
      });
      return { status: r.status, body: await r.json() };
    }, { csrfToken: phoneCsrf, rev: phoneRev });
    expect(quizResult.status).toBe(200);
    var newRev = quizResult.body.revision;
    expect(newRev).toBeGreaterThan(phoneRev);

    var macProgress = await desktopPage.evaluate(async function () {
      var r = await fetch("/api/v1/progress", { method: "GET", credentials: "include" });
      return { status: r.status, body: await r.json() };
    });
    expect(macProgress.status).toBe(200);
    expect(macProgress.body.revision).toBeGreaterThanOrEqual(newRev);
    var allSessions = macProgress.body.document.sessions;
    var crossSessions = allSessions.filter(function (s) {
      return s.course === "cross-course";
    });
    expect(crossSessions.length).toBeGreaterThanOrEqual(1);
    expect(crossSessions[0].quiz_id).toBe("cross-device-q1");

    var crossMastery = macProgress.body.document.mastery["cross-course"];
    expect(crossMastery).toBeTruthy();
    expect(crossMastery["cross-pack"]).toBeTruthy();
    expect(crossMastery["cross-pack"].seen.q1).toBe(true);
    expect(crossMastery["cross-pack"].correct.q2).toBe(true);

    await desktopCtx.close();
    await mobileCtx.close();
  });
});

/* ─── Two-tab CSRF ─── */

test.describe("Real Server — Two-tab CSRF", function () {
  test.use({ viewport: { width: 1280, height: 720 } });

  test("two tabs with same session can both make authenticated requests", async function ({ context }) {
    var page1 = await setupContextPage(context);
    var page2 = await setupContextPage(context);

    var pairLocalResp = await page1.evaluate(async function () {
      var r = await fetch("/api/v1/auth/pair-local", { method: "POST" });
      return r.json();
    });
    var pairResult = await page1.evaluate(async function (code) {
      var r = await fetch("/api/v1/auth/pair", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pairing_code: code }),
      });
      return { status: r.status, body: await r.json() };
    }, pairLocalResp.pairing_code);
    expect(pairResult.status).toBe(200);
    var csrfToken = pairResult.body.csrf_token;

    var rev = await getRevision(page1);

    var page2Write = await page2.evaluate(async function (opts) {
      var c = opts.csrfToken;
      var rev = opts.rev;
      var opId = "two-tab-op-" + Date.now();
      var r = await fetch("/api/v1/progress/quiz-completed", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          expected_revision: rev,
          operation_id: opId,
          session: { quiz_id: "tab2-quiz", course: "tab-course", score: { correct: 5, total: 5 } },
          course_id: "tab-course",
          pack_id: "tab-pack",
          mastery_delta: { seen: { q1: true }, correct: { q1: true }, consecutive: { q1: 1 } },
          csrf_token: c,
        }),
      });
      return { status: r.status, body: await r.json() };
    }, { csrfToken: csrfToken, rev: rev });
    expect(page2Write.status).toBe(200);

    var page1Read = await page1.evaluate(async function () {
      var r = await fetch("/api/v1/progress", { method: "GET", credentials: "include" });
      return { status: r.status, body: await r.json() };
    });
    expect(page1Read.status).toBe(200);
    var tabSessions = page1Read.body.document.sessions.filter(function (s) {
      return s.course === "tab-course";
    });
    expect(tabSessions.length).toBeGreaterThanOrEqual(1);
    expect(tabSessions[0].quiz_id).toBe("tab2-quiz");

    await page1.close();
    await page2.close();
  });
});

/* ─── Mode Detection ─── */

test.describe("Real Server — Mode Detection", function () {
  test("app page serves shared mode meta and CSRF token", async function ({ page }) {
    await page.goto(getBaseURL() + "/pair");
    await page.waitForLoadState("domcontentloaded");

    var pairLocalResp = await page.evaluate(async function () {
      var r = await fetch("/api/v1/auth/pair-local", { method: "POST" });
      return r.json();
    });
    var pairResult = await page.evaluate(async function (code) {
      var r = await fetch("/api/v1/auth/pair", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pairing_code: code }),
      });
      return { status: r.status, body: await r.json() };
    }, pairLocalResp.pairing_code);
    expect(pairResult.status).toBe(200);

    await page.goto(getBaseURL() + "/app/");
    await page.waitForLoadState("domcontentloaded");

    var meta = await page.evaluate(function () {
      var modeMeta = document.querySelector('meta[name="quizzler-mode"]');
      var csrfMeta = document.querySelector('meta[name="csrf-token"]');
      return {
        mode: modeMeta ? modeMeta.getAttribute("content") : null,
        csrfToken: csrfMeta ? csrfMeta.getAttribute("content") : null,
      };
    });

    expect(meta.mode).toBe("shared");
    expect(meta.csrfToken).toBeTruthy();
    expect(meta.csrfToken).toBe(pairResult.body.csrf_token);
  });
});

/* ─── Concurrent Mutation ─── */

test.describe("Real Server — Concurrent Mutation", function () {
  test("two clients writing at same revision: one succeeds, one gets 409", async function ({ browser }) {
    var ctxA = await browser.newContext({ viewport: { width: 1280, height: 720 } });
    var ctxB = await browser.newContext({ viewport: { width: 1280, height: 720 } });
    var pageA = await setupContextPage(ctxA);
    var pageB = await setupContextPage(ctxB);

    var pairLocalResp = await pageA.evaluate(async function () {
      var r = await fetch("/api/v1/auth/pair-local", { method: "POST" });
      return r.json();
    });
    var pairingCode = pairLocalResp.pairing_code;

    var pairA = await pageA.evaluate(async function (code) {
      var r = await fetch("/api/v1/auth/pair", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pairing_code: code }),
      });
      return r.json();
    }, pairingCode);
    expect(pairA.ok).toBe(true);
    var csrfA = pairA.csrf_token;

    var pairB = await pageB.evaluate(async function (code) {
      var r = await fetch("/api/v1/auth/pair", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pairing_code: code }),
      });
      return r.json();
    }, pairingCode);
    expect(pairB.ok).toBe(true);
    var csrfB = pairB.csrf_token;

    var rev = await getRevision(pageA);

    var aResult = await pageA.evaluate(async function (opts) {
      var c = opts.csrfToken;
      var rev = opts.rev;
      var opId = "concurrent-a-" + Date.now();
      var r = await fetch("/api/v1/progress/quiz-completed", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          expected_revision: rev,
          operation_id: opId,
          session: { quiz_id: "client-a-quiz", course: "conc", score: { correct: 1, total: 1 } },
          course_id: "conc",
          pack_id: "p1",
          mastery_delta: { seen: { q1: true }, correct: {}, consecutive: {} },
          csrf_token: c,
        }),
      });
      return { status: r.status, body: await r.json() };
    }, { csrfToken: csrfA, rev: rev });
    expect(aResult.status).toBe(200);
    expect(aResult.body.revision).toBeGreaterThan(rev);

    var bResult = await pageB.evaluate(async function (opts) {
      var c = opts.csrfToken;
      var rev = opts.rev;
      var opId = "concurrent-b-" + Date.now();
      var r = await fetch("/api/v1/progress/quiz-completed", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          expected_revision: rev,
          operation_id: opId,
          session: { quiz_id: "client-b-quiz", course: "conc", score: { correct: 1, total: 1 } },
          course_id: "conc",
          pack_id: "p1",
          mastery_delta: { seen: { q2: true }, correct: {}, consecutive: {} },
          csrf_token: c,
        }),
      });
      return { status: r.status, body: await r.json() };
    }, { csrfToken: csrfB, rev: rev });
    expect(bResult.status).toBe(409);
    expect(bResult.body.error).toBe("conflict");
    expect(bResult.body.current_revision).toBeGreaterThan(rev);

    await ctxA.close();
    await ctxB.close();
  });
});

/* ─── SRS Rating ─── */

test.describe("Real Server — SRS Rating", function () {
  test("srs rating returns tier data from server", async function ({ page }) {
    var pair = await pairDevice(page);
    var rev = await getRevision(page);

    var result = await page.evaluate(async function (opts) {
      var csrfToken = opts.csrfToken;
      var rev = opts.rev;
      var opId = "srs-op-" + Date.now();
      var r = await fetch("/api/v1/progress/srs-rated", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          expected_revision: rev,
          operation_id: opId,
          course_id: "srs-course",
          composite_key: "srs-course::p1::q1",
          rating: "good",
          csrf_token: csrfToken,
        }),
      });
      return { status: r.status, body: await r.json() };
    }, { csrfToken: pair.csrfToken, rev: rev });
    expect(result.status).toBe(200);
    expect(typeof result.body.old_tier).toBe("number");
    expect(typeof result.body.new_tier).toBe("number");
    expect(result.body.new_tier).toBeGreaterThanOrEqual(result.body.old_tier);
  });
});

/* ─── Import Progress ─── */

test.describe("Real Server — Import Progress", function () {
  test("import progress stores and retrieves sessions", async function ({ page }) {
    var pair = await pairDevice(page);
    var rev = await getRevision(page);

    var doc = {
      schema_version: 1,
      sessions: [
        { quiz_id: "imported-q1", course: "import-course", score: { correct: 7, total: 10 } },
      ],
      mastery: {
        "import-course": {
          "p1": { seen: { q1: true }, correct: { q1: true }, consecutive: {} },
        },
      },
      srs: {},
    };

    var importResult = await page.evaluate(async function (opts) {
      var d = opts.doc;
      var csrfToken = opts.csrfToken;
      var rev = opts.rev;
      var r = await fetch("/api/v1/progress/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          expected_revision: rev,
          operation_id: "import-op-" + Date.now(),
          document: d,
          csrf_token: csrfToken,
        }),
      });
      return { status: r.status, body: await r.json() };
    }, { doc: doc, csrfToken: pair.csrfToken, rev: rev });
    expect(importResult.status).toBe(200);
    expect(importResult.body.revision).toBeGreaterThan(rev);

    var progress = await page.evaluate(async function () {
      var r = await fetch("/api/v1/progress", { method: "GET", credentials: "include" });
      return r.json();
    });
    var importedSessions = progress.document.sessions.filter(function (s) {
      return s.course === "import-course";
    });
    expect(importedSessions.length).toBeGreaterThanOrEqual(1);
    expect(importedSessions[0].quiz_id).toBe("imported-q1");
    expect(progress.document.mastery["import-course"]).toBeTruthy();
  });
});

/* ─── Logout ─── */

test.describe("Real Server — Logout", function () {
  test("logout invalidates session, subsequent progress request gets 401", async function ({ page }) {
    var pair = await pairDevice(page);

    var before = await page.evaluate(async function () {
      var r = await fetch("/api/v1/progress", { method: "GET", credentials: "include" });
      return r.status;
    });
    expect(before).toBe(200);

    var logoutResult = await page.evaluate(async function () {
      var r = await fetch("/api/v1/auth/logout", { method: "POST", credentials: "include" });
      return { status: r.status, body: await r.json() };
    });
    expect(logoutResult.status).toBe(200);

    var after = await page.evaluate(async function () {
      var r = await fetch("/api/v1/progress", { method: "GET", credentials: "include" });
      return r.status;
    });
    expect(after).toBe(401);
  });
});

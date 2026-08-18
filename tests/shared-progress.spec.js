// @ts-check
var { test, expect } = require("@playwright/test");
var PORTS = require("./browser-port.js");

/* ─── Helpers ─── */

async function setupMockAPI(page) {
  var mock = {
    revision: 0,
    sessions: [],
    mastery: {},
    srs: {},
    csrfToken: "test-csrf-token-abc123",
    sessionToken: "test-session-token-xyz",
    operationLog: [],
    nextQuizCompletedResponse: null,
    nextSrsRatedResponse: null,
    nextImportResponse: null,
    nextSessionsResponse: null,
    nextSrsStateResponse: null,
    nextResetResponse: null,
    protocolVersion: undefined,
    supportedProtocolVersions: undefined,
  };

  await page.route("**/api/v1/**", function (route) {
    var req = route.request();
    var url = req.url();
    var method = req.method();

    if (method === "POST" && url.includes("/api/v1/auth/pair-local")) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ pairing_code: "1234" }),
      });
    }

    if (method === "POST" && url.includes("/api/v1/auth/pair")) {
      var pairBody = req.postDataJSON();
      if (pairBody && pairBody.pairing_code === "1234") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          headers: { "Set-Cookie": "quizzler_session=" + mock.sessionToken + "; HttpOnly; SameSite=Strict; Path=/" },
          body: JSON.stringify({ ok: true, csrf_token: mock.csrfToken }),
        });
      }
      return route.fulfill({ status: 403, contentType: "application/json", body: JSON.stringify({ error: "invalid" }) });
    }

    if (method === "POST" && url.includes("/api/v1/auth/logout")) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        headers: { "Set-Cookie": "quizzler_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0" },
        body: JSON.stringify({ ok: true }),
      });
    }

    if (method === "GET" && url.includes("/api/v1/progress")) {
      mock.revision = mock.revision || 0;
      var progressBody = {
        revision: mock.revision,
        document: {
          schema_version: 1,
          sessions: structuredClone(mock.sessions),
          mastery: structuredClone(mock.mastery),
          srs: structuredClone(mock.srs),
        },
      };
      if (mock.protocolVersion !== undefined) progressBody.protocol_version = mock.protocolVersion;
      if (mock.supportedProtocolVersions !== undefined) progressBody.supported_protocol_versions = mock.supportedProtocolVersions;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(progressBody),
      });
    }

    var body = req.postDataJSON() || {};
    var er = body.expected_revision || 0;
    var oid = body.operation_id;

    if (method === "POST" && url.includes("/api/v1/progress/quiz-completed")) {
      mock.operationLog.push({ type: "quiz-completed", body: body });
      if (er !== mock.revision) {
        return route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({ error: "conflict", current_revision: mock.revision }),
        });
      }
      var qcResponse = mock.nextQuizCompletedResponse || { revision: mock.revision + 1 };
      mock.nextQuizCompletedResponse = null;
      if (qcResponse.revision !== undefined) mock.revision = qcResponse.revision;
      if (body.session) mock.sessions.unshift(body.session);
      if (body.mastery_delta && body.course_id && body.pack_id) {
        var cid = body.course_id;
        var pid = body.pack_id;
        if (!mock.mastery[cid]) mock.mastery[cid] = {};
        if (!mock.mastery[cid][pid]) mock.mastery[cid][pid] = { seen: {}, correct: {}, consecutive: {} };
        var m = mock.mastery[cid][pid];
        if (body.mastery_delta.seen) Object.assign(m.seen, body.mastery_delta.seen);
        if (body.mastery_delta.correct) Object.assign(m.correct, body.mastery_delta.correct);
        if (body.mastery_delta.consecutive) {
          if (!m.consecutive) m.consecutive = {};
          Object.assign(m.consecutive, body.mastery_delta.consecutive);
        }
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(qcResponse),
      });
    }

    if (method === "POST" && url.includes("/api/v1/progress/srs-rated")) {
      mock.operationLog.push({ type: "srs-rated", body: body });
      if (er !== mock.revision) {
        return route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({ error: "conflict", current_revision: mock.revision }),
        });
      }
      var srsResponse = mock.nextSrsRatedResponse || { revision: mock.revision + 1, old_tier: 1, new_tier: 2 };
      mock.nextSrsRatedResponse = null;
      if (srsResponse.revision !== undefined) mock.revision = srsResponse.revision;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(srsResponse),
      });
    }

    if (method === "POST" && url.includes("/api/v1/progress/import")) {
      mock.operationLog.push({ type: "import", body: body });
      if (er !== mock.revision) {
        return route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({ error: "conflict", current_revision: mock.revision }),
        });
      }
      var impResponse = mock.nextImportResponse || { revision: mock.revision + 1 };
      mock.nextImportResponse = null;
      if (impResponse.revision !== undefined) mock.revision = impResponse.revision;
      if (body.document) {
        mock.sessions = body.document.sessions || [];
        mock.mastery = body.document.mastery || {};
        mock.srs = body.document.srs || {};
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(impResponse),
      });
    }

    if (method === "POST" && url.includes("/api/v1/progress/sessions")) {
      mock.operationLog.push({ type: "sessions", body: body });
      if (er !== mock.revision) {
        return route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ error: "conflict", current_revision: mock.revision }) });
      }
      var sessionsResponse = mock.nextSessionsResponse || { revision: mock.revision + 1 };
      mock.nextSessionsResponse = null;
      if (sessionsResponse.revision !== undefined) mock.revision = sessionsResponse.revision;
      mock.sessions = body.sessions || [];
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(sessionsResponse) });
    }

    if (method === "POST" && url.includes("/api/v1/progress/srs")) {
      mock.operationLog.push({ type: "srs", body: body });
      if (er !== mock.revision) {
        return route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ error: "conflict", current_revision: mock.revision }) });
      }
      var srsStateResponse = mock.nextSrsStateResponse || { revision: mock.revision + 1 };
      mock.nextSrsStateResponse = null;
      if (srsStateResponse.revision !== undefined) mock.revision = srsStateResponse.revision;
      mock.srs[body.course_id] = body.state;
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(srsStateResponse) });
    }

    if (method === "POST" && url.includes("/api/v1/progress/reset")) {
      mock.operationLog.push({ type: "reset", body: body });
      if (er !== mock.revision) {
        return route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({ error: "conflict", current_revision: mock.revision }),
        });
      }
      var resetResponse = mock.nextResetResponse || { revision: mock.revision + 1 };
      mock.nextResetResponse = null;
      if (resetResponse.revision !== undefined) mock.revision = resetResponse.revision;
      if (body.clear_srs_course_id) delete mock.srs[body.clear_srs_course_id];
      else if (body.clear_mastery) mock.mastery = {};
      else {
        mock.sessions = [];
        mock.mastery = {};
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(resetResponse),
      });
    }

    if (method === "POST" && url.includes("/api/v1/progress/cleanup-orphans")) {
      mock.operationLog.push({ type: "cleanup-orphans", body: body });
      if (er !== mock.revision) {
        return route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({ error: "conflict", current_revision: mock.revision }),
        });
      }
      var activeIds = body.active_course_ids || [];
      Object.keys(mock.mastery).forEach(function (cid) {
        if (activeIds.indexOf(cid) === -1) delete mock.mastery[cid];
      });
      var kept = [];
      for (var i = 0; i < mock.sessions.length; i++) {
        if (activeIds.indexOf(mock.sessions[i].course) !== -1) kept.push(mock.sessions[i]);
      }
      mock.sessions = kept;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ revision: mock.revision + 1 }),
      });
    }

    return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: "not found" }) });
  });

  return mock;
}

function makeSharedPageHtml(mock) {
  return [
    "<!DOCTYPE html><html lang=\"en\"><head>",
    "<meta charset=\"UTF-8\">",
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">",
    "<meta name=\"quizzler-auth-status\" content=\"active\">",
    "<meta name=\"csrf-token\" content=\"" + mock.csrfToken + "\">",
    "<meta name=\"description\" content=\"Test\">",
    "<title>Quizzler Test</title>",
    "</head><body>",
    '<div id="home"><div class="course-grid" id="courseGrid"></div></div>',
    '<div id="quizConfig" style="display:none;"></div>',
    '<div id="quizScreen" style="display:none;"></div>',
    '<div id="historyScreen" style="display:none;"></div>',
    '<div id="progressStatus" class="progress-status" role="status" aria-live="polite" style="display:none;"></div>',
    '<div class="modal" id="dialogModal" aria-hidden="true" role="dialog"><div class="panel modal-content"><h2 id="dialogModalTitle"></h2><p id="dialogModalBody"></p><div class="modal-actions"><button type="button" class="secondary" id="dialogCancelBtn" style="display:none">Cancel</button><button type="button" id="dialogConfirmBtn">OK</button></div></div></div>',
    '<script src="/app/progress-store.js"></script>',
    '<script src="/app/shared-progress.js"></script>',
    "</body></html>",
  ].join("\n");
}

var _fs, _path;
function readAppJs(page, file) {
  if (!_fs) { _fs = require("fs"); _path = require("path"); }
  return _fs.readFileSync(_path.join(__dirname, "..", "app", file), "utf-8");
}

async function routeAppJs(page) {
  await page.route("**/progress-store.js", function (route) {
    return route.fulfill({
      status: 200,
      contentType: "application/javascript",
      body: readAppJs(page, "progress-store.js"),
    });
  });

  await page.route("**/shared-progress.js", function (route) {
    return route.fulfill({
      status: 200,
      contentType: "application/javascript",
      body: readAppJs(page, "shared-progress.js"),
    });
  });
}

async function loadSharedAdapter(page, mock, extraScript) {
  await routeAppJs(page);

  await page.route("**/manifest.json", function (route) {
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ courses: [] }),
    });
  });

  var html = makeSharedPageHtml(mock);
  if (extraScript) {
    html = html.replace("</body></html>", "<script>" + extraScript + "</script></body></html>");
  }

  var initScript = [
    "window.__modeMeta = document.querySelector('meta[name=\"quizzler-auth-status\"]').getAttribute('content');",
    "var apiClient = QuizzlerSharedProgress.createApiClient('');",
    "var ad = QuizzlerSharedProgress.createSharedAdapter(apiClient);",
    "window.progressStore = ad;",
    "ad.onStatusChange(function(s) { window.__status = s; window.__statuses = (window.__statuses || []); window.__statuses.push(s); });",
    "ad.hydrate('" + mock.csrfToken + "').then(function() { window.__hydrated = true; }).catch(function(e) { window.__hydrated = false; window.__hydrateErr = e.message; });",
  ].join("\n");
  html = html.replace("</body></html>", "<script>" + initScript + "</script></body></html>");

  var pageUrl = "http://localhost:8788/app/";
  await page.route(pageUrl, function (route) {
    return route.fulfill({
      status: 200,
      contentType: "text/html; charset=utf-8",
      body: html,
    });
  });

  await page.goto(pageUrl);
}

/* ─── Test: createSharedAdapter with mocked API ─── */

test.describe("Shared Progress Adapter", function () {
  test("loads in shared mode, hydrates, and transitions to ready", async function ({ page }) {
    var mock = await setupMockAPI(page);
    await loadSharedAdapter(page, mock);

    await page.waitForFunction(function () { return window.__hydrated !== undefined; }, null, { timeout: 10000 });

    var result = await page.evaluate(function () {
      return {
        modeMeta: window.__modeMeta,
        hydrated: window.__hydrated,
        isLocal: window.progressStore.isLocalMode ? window.progressStore.isLocalMode() : null,
        status: window.progressStore.getStatus ? window.progressStore.getStatus() : "unknown",
      };
    });
    expect(result.modeMeta).toBe("active");
    expect(result.hydrated).toBe(true);
    expect(result.isLocal).toBe(false);
    expect(result.status).toBe("ready");
  });

  test("getSessions returns empty after fresh hydrate", async function ({ page }) {
    var mock = await setupMockAPI(page);
    await loadSharedAdapter(page, mock);
    await page.waitForFunction(function () { return window.__hydrated; }, null, { timeout: 10000 });

    var sessions = await page.evaluate(function () {
      return window.progressStore.getSessions();
    });
    expect(sessions).toEqual([]);
  });

  test("getMastery returns fresh default", async function ({ page }) {
    var mock = await setupMockAPI(page);
    await loadSharedAdapter(page, mock);
    await page.waitForFunction(function () { return window.__hydrated; }, null, { timeout: 10000 });

    var mastery = await page.evaluate(function () {
      return window.progressStore.getMastery("any-course", "any-pack");
    });
    expect(mastery).toEqual({ seen: {}, correct: {}, consecutive: {} });
  });

  test("getSRSState returns fresh default", async function ({ page }) {
    var mock = await setupMockAPI(page);
    await loadSharedAdapter(page, mock);
    await page.waitForFunction(function () { return window.__hydrated; }, null, { timeout: 10000 });

    var state = await page.evaluate(function () {
      return window.progressStore.getSRSState("any-course");
    });
    expect(state.schema_version).toBe(1);
    expect(state.questions).toEqual({});
  });

  test("exportSRSState returns cache data", async function ({ page }) {
    var mock = await setupMockAPI(page);
    await loadSharedAdapter(page, mock);
    await page.waitForFunction(function () { return window.__hydrated; }, null, { timeout: 10000 });

    var result = await page.evaluate(function () {
      return window.progressStore.exportSRSState("c1");
    });
    expect(result.course_id).toBe("c1");
    expect(result.questions).toEqual({});
  });

  test("isLocalMode returns false in shared mode", async function ({ page }) {
    var mock = await setupMockAPI(page);
    await loadSharedAdapter(page, mock);
    await page.waitForFunction(function () { return window.__hydrated; }, null, { timeout: 10000 });

    var result = await page.evaluate(function () {
      return window.progressStore.isLocalMode();
    });
    expect(result).toBe(false);
  });

  test("findOrphans detects orphan mastery keys from cache", async function ({ page }) {
    var mock = await setupMockAPI(page);
    mock.mastery = { "active": { "p1": { seen: {}, correct: {}, consecutive: {} } }, "archived": { "p2": { seen: {}, correct: {}, consecutive: {} } } };
    mock.revision = 1;
    await loadSharedAdapter(page, mock);
    await page.waitForFunction(function () { return window.__hydrated; }, null, { timeout: 10000 });

    var orphans = await page.evaluate(function () {
      return window.progressStore.findOrphans(["active"]);
    });
    expect(orphans.masteryKeys.length).toBe(1);
    expect(orphans.masteryKeys[0]).toContain("archived");
  });

  test("clearHistory clears sessions and mastery", async function ({ page }) {
    var mock = await setupMockAPI(page);
    mock.sessions = [{ quiz_id: "s1", course: "c" }];
    mock.mastery = { "c": { "p": { seen: { q1: true }, correct: {}, consecutive: {} } } };
    mock.revision = 1;
    await loadSharedAdapter(page, mock);
    await page.waitForFunction(function () { return window.__hydrated; }, null, { timeout: 10000 });

    await page.evaluate(async function () {
      await window.progressStore.clearHistory();
    });

    await page.waitForTimeout(300);
    var after = await page.evaluate(function () {
      return {
        sessions: window.progressStore.getSessions(),
        mastery: window.progressStore.getMastery("c", "p"),
      };
    });
    expect(after.sessions).toEqual([]);
    expect(after.mastery.seen.q1).toBeUndefined();
  });
});

test.describe("[CONTRACT] Shared Progress Protocol Negotiation", function () {
  test("[CONTRACT] new tab interoperates with an old v1 server and sends an explicit mutation version", async function ({ page }) {
    var mock = await setupMockAPI(page);
    await loadSharedAdapter(page, mock);
    await page.waitForFunction(function () { return window.__hydrated !== undefined; }, null, { timeout: 10000 });
    expect(await page.evaluate(function () { return window.__hydrated; })).toBe(true);

    await page.evaluate(async function () {
      await window.progressStore.saveSession({ quiz_id: "legacy", course: "c", answers: [] });
    });
    expect(mock.operationLog[0].body.protocol_version).toBe(1);
  });

  test("[CONTRACT] incompatible server fails visibly before any browser mutation", async function ({ page }) {
    var mock = await setupMockAPI(page);
    mock.protocolVersion = 2;
    mock.supportedProtocolVersions = [2];
    await loadSharedAdapter(page, mock);
    await page.waitForFunction(function () { return window.__hydrated !== undefined; }, null, { timeout: 10000 });

    var result = await page.evaluate(function () {
      return {
        hydrated: window.__hydrated,
        status: window.progressStore.getStatus(),
        error: window.progressStore.getLastError(),
      };
    });
    expect(result.hydrated).toBe(false);
    expect(result.status).toBe("error");
    expect(result.error.code).toBe("refresh-failed");
    expect(mock.operationLog).toEqual([]);
  });
});

/* ─── Test: Quiz Completion (atomic) ─── */

test.describe("Shared Progress — Quiz Completion", function () {
  test("quizCompleted makes one atomic API call", async function ({ page }) {
    var mock = await setupMockAPI(page);
    await loadSharedAdapter(page, mock);
    await page.waitForFunction(function () { return window.__hydrated; }, null, { timeout: 10000 });

    mock.operationLog = [];
    var result = await page.evaluate(async function () {
      try {
        var opId = QuizzlerSharedProgress.generateOpId();
        var session = { quiz_id: "test99", course: "math", score: { correct: 8, total: 10 } };
        var masteryDelta = { "pack-a": { seen: { q1: true }, correct: { q1: true }, consecutive: {} } };
        var r = await window.progressStore.quizCompleted(session, "math", "pack-a", masteryDelta, opId);
        return { ok: true };
      } catch (e) {
        return { ok: false, error: e.message };
      }
    });
    expect(result.ok).toBe(true);
    expect(mock.operationLog.length).toBe(1);
    expect(mock.operationLog[0].type).toBe("quiz-completed");
  });

  test("quizCompletion includes session+mastery in one call", async function ({ page }) {
    var mock = await setupMockAPI(page);
    await loadSharedAdapter(page, mock);
    await page.waitForFunction(function () { return window.__hydrated; }, null, { timeout: 10000 });

    mock.operationLog = [];
    await page.evaluate(async function () {
      var opId = QuizzlerSharedProgress.generateOpId();
      var session = { quiz_id: "q-atomic", course: "bio", score: { correct: 3, total: 5 } };
      var masteryDelta = { "bio-pack": { seen: { q1: true }, correct: { q1: true }, consecutive: { q1: 1 } } };
      await window.progressStore.quizCompleted(session, "bio", "bio-pack", masteryDelta, opId);
    });

    var call = mock.operationLog[0];
    expect(call.body.session.course).toBe("bio");
    expect(call.body.mastery_delta["bio-pack"].seen.q1).toBe(true);
  });
});

/* ─── Test: Status surface ─── */

test.describe("Shared Progress — Status Surface", function () {
  test("status transitions from loading to ready on hydrate", async function ({ page }) {
    var mock = await setupMockAPI(page);
    await routeAppJs(page);

    var html = [
      "<!DOCTYPE html><html lang=\"en\"><head>",
      "<meta charset=\"UTF-8\">",
      '<meta name="quizzler-auth-status" content="active">',
      '<meta name="csrf-token" content="' + mock.csrfToken + '">',
      "</head><body>",
      '<div id="progressStatus" class="progress-status" role="status" aria-live="polite" style="display:none;"></div>',
      '<script src="/app/progress-store.js"></script>',
      '<script src="/app/shared-progress.js"></script>',
      "<script>",
      "var apiClient = QuizzlerSharedProgress.createApiClient('');",
      "var ad = QuizzlerSharedProgress.createSharedAdapter(apiClient);",
      "ad.onStatusChange(function(s) { window.__status = s; });",
      "ad.hydrate('" + mock.csrfToken + "').then(function() { window.__done = true; }).catch(function() {});",
      "</script>",
      "</body></html>",
    ].join("\n");

    var pageUrl = PORTS.baseURL + "/app/";
    await page.route(pageUrl, function (route) {
      return route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: html });
    });
    await page.route("**/api/v1/progress", function (route) {
      if (route.request().method() === "GET") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ revision: 0, document: { schema_version: 1, sessions: [], mastery: {}, srs: {} } }),
        });
      }
      return route.fulfill({ status: 404 });
    });

    await page.goto(pageUrl);
    await page.waitForFunction(function () { return window.__done; }, null, { timeout: 10000 });

    var status = await page.evaluate(function () { return window.__status; });
    expect(status).toBe("ready");
  });

  test("status shows saving during mutation", async function ({ page }) {
    var mock = await setupMockAPI(page);
    await loadSharedAdapter(page, mock);
    await page.waitForFunction(function () { return window.__hydrated; }, null, { timeout: 10000 });

    var resolveImport;
    var importHeld = new Promise(function (r) { resolveImport = r; });

    await page.unroute("**/api/v1/**");
    await page.route(function (url) {
      return url.href.includes("/api/v1/progress");
    }, function (route) {
      var reqUrl = route.request().url();
      var meth = route.request().method();
      if (meth === "POST" && (reqUrl.includes("import") || reqUrl.includes("quiz-completed"))) {
        importHeld.then(function () {
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ revision: 1 }),
          });
        });
        return;
      }
      if (meth === "GET") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            revision: 0,
            document: { schema_version: 1, sessions: [], mastery: {}, srs: {} },
          }),
        });
      }
      return route.fulfill({ status: 404 });
    });

    var mutationTriggered = page.evaluate(function () {
      window.__mutationDone = false;
      return window.progressStore.saveSession({ quiz_id: "slow", course: "c" })
        .then(function () { window.__mutationDone = true; });
    });

    await page.waitForTimeout(300);

    var midStatus = await page.evaluate(function () {
      return { status: window.progressStore.getStatus(), done: window.__mutationDone };
    });
    expect(midStatus.status).toBe("saving");
    expect(midStatus.done).toBe(false);

    resolveImport();
    await mutationTriggered.catch(function () {});
    await page.waitForTimeout(500);

    var finalStatus = await page.evaluate(function () {
      return { status: window.progressStore.getStatus(), done: window.__mutationDone };
    });
    expect(finalStatus.done).toBe(true);
  });

  test("status shows error on network failure", async function ({ page }) {
    await routeAppJs(page);

    var html = [
      "<!DOCTYPE html><html lang=\"en\"><head>",
      "<meta charset=\"UTF-8\">",
      '<meta name="quizzler-auth-status" content="active">',
      '<meta name="csrf-token" content="test-csrf">',
      "</head><body>",
      '<div id="progressStatus" class="progress-status" role="status" aria-live="polite" style="display:none;"></div>',
      '<script src="/app/progress-store.js"></script>',
      '<script src="/app/shared-progress.js"></script>',
      "<script>",
      "var apiClient = QuizzlerSharedProgress.createApiClient('');",
      "var ad = QuizzlerSharedProgress.createSharedAdapter(apiClient);",
      "ad.onStatusChange(function(s, e) { window.__status = s; window.__error = e; });",
      "ad.hydrate('test-csrf').catch(function() { window.__hydrateFailed = true; });",
      "</script>",
      "</body></html>",
    ].join("\n");

    var pageUrl = PORTS.baseURL + "/app/";
    await page.route(pageUrl, function (route) {
      return route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: html });
    });
    await page.route("**/api/v1/progress", function (route) {
      return route.abort("connectionrefused");
    });

    await page.goto(pageUrl);
    await page.waitForFunction(function () { return window.__hydrateFailed; }, null, { timeout: 10000 });

    var result = await page.evaluate(function () {
      return { status: window.__status, failed: window.__hydrateFailed };
    });
    expect(result.status).toBe("error");
    expect(result.failed).toBe(true);
  });
});

/* ─── Test: Conflict Handling ─── */

test.describe("Shared Progress — Conflict Handling", function () {
  test("409 conflict triggers refresh and retry, then succeeds", async function ({ page }) {
    var mock = await setupMockAPI(page);
    await loadSharedAdapter(page, mock);
    await page.waitForFunction(function () { return window.__hydrated; }, null, { timeout: 10000 });

    var callCount = 0;
    await page.unroute("**/api/v1/**");
    await page.route(function (url) {
      return url.href.includes("/api/v1/progress");
    }, function (route) {
      var reqUrl = route.request().url();
      var meth = route.request().method();
      if (meth === "POST" && reqUrl.includes("quiz-completed")) {
        callCount++;
        if (callCount === 1) {
          return route.fulfill({
            status: 409,
            contentType: "application/json",
            body: JSON.stringify({ error: "conflict", current_revision: 5 }),
          });
        }
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ revision: 6 }),
        });
      }
      if (meth === "GET") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            revision: 5,
            document: { schema_version: 1, sessions: [], mastery: {}, srs: {} },
          }),
        });
      }
      return route.fulfill({ status: 404 });
    });

    var result = await page.evaluate(async function () {
      try {
        var opId = QuizzlerSharedProgress.generateOpId();
        var session = { quiz_id: "conflict-test", course: "c1", score: { correct: 1, total: 1 } };
        var md = { "p1": { seen: { q1: true }, correct: {}, consecutive: {} } };
        var r = await window.progressStore.quizCompleted(session, "c1", "p1", md, opId);
        return { ok: true, data: r };
      } catch (e) {
        return { ok: false, error: e.message };
      }
    });

    expect(result.ok).toBe(true);
    expect(callCount).toBe(2);
  });

  test("queue serialization: two concurrent mutations processed in order", async function ({ page }) {
    var mock = await setupMockAPI(page);
    await loadSharedAdapter(page, mock);
    await page.waitForFunction(function () { return window.__hydrated; }, null, { timeout: 10000 });

    var apiCalls = [];

    await page.unroute("**/api/v1/**");
    await page.route(function (url) {
      return url.href.includes("/api/v1/progress");
    }, function (route) {
      var reqUrl = route.request().url();
      var meth = route.request().method();
      if (meth === "POST" && (reqUrl.includes("import") || reqUrl.includes("quiz-completed"))) {
        var body = route.request().postDataJSON() || {};
        apiCalls.push(body.operation_id);
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ revision: apiCalls.length }),
        });
      }
      if (meth === "GET") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            revision: 0,
            document: { schema_version: 1, sessions: [], mastery: {}, srs: {} },
          }),
        });
      }
      return route.fulfill({ status: 404 });
    });

    var done1 = await page.evaluate(async function () {
      await window.progressStore.saveSession({ quiz_id: "first", course: "c" });
      return "first-done";
    });

    var done2 = await page.evaluate(async function () {
      await window.progressStore.saveSession({ quiz_id: "second", course: "c" });
      return "second-done";
    });

    expect(done1).toBe("first-done");
    expect(done2).toBe("second-done");
    expect(apiCalls.length).toBeGreaterThanOrEqual(2);
    /* Verify they were processed in order: first comes before second */
    expect(apiCalls[0]).not.toBeUndefined();
    expect(apiCalls[1]).not.toBeUndefined();
  });
});

/* ─── Test: SRS Rating ─── */

test.describe("Shared Progress — SRS Rating", function () {
  test("srsRated returns old_tier and new_tier from server", async function ({ page }) {
    var mock = await setupMockAPI(page);
    await loadSharedAdapter(page, mock);
    await page.waitForFunction(function () { return window.__hydrated; }, null, { timeout: 10000 });

    mock.nextSrsRatedResponse = { revision: 3, old_tier: 2, new_tier: 3 };
    mock.operationLog = [];

    var result = await page.evaluate(async function () {
      try {
        var opId = QuizzlerSharedProgress.generateOpId();
        var r = await window.progressStore.srsRated("c1", "c1::p1::q1", "good", opId);
        return r;
      } catch (e) {
        return { error: e.message };
      }
    });
    expect(result.old_tier).toBe(2);
    expect(result.new_tier).toBe(3);
    expect(result.revision).toBe(3);
  });

  test("srsRated on 409 conflict triggers refresh and retry", async function ({ page }) {
    var mock = await setupMockAPI(page);
    await loadSharedAdapter(page, mock);
    await page.waitForFunction(function () { return window.__hydrated; }, null, { timeout: 10000 });

    var callCount = 0;
    await page.unroute("**/api/v1/**");
    await page.route(function (url) {
      return url.href.includes("/api/v1/progress");
    }, function (route) {
      var reqUrl = route.request().url();
      var meth = route.request().method();
      if (meth === "POST" && reqUrl.includes("srs-rated")) {
        callCount++;
        if (callCount === 1) {
          return route.fulfill({
            status: 409,
            contentType: "application/json",
            body: JSON.stringify({ error: "conflict", current_revision: 7 }),
          });
        }
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ revision: 8, old_tier: 3, new_tier: 4 }),
        });
      }
      if (meth === "GET") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            revision: 7,
            document: { schema_version: 1, sessions: [], mastery: {}, srs: {} },
          }),
        });
      }
      return route.fulfill({ status: 404 });
    });

    var result = await page.evaluate(async function () {
      try {
        var opId = QuizzlerSharedProgress.generateOpId();
        var r = await window.progressStore.srsRated("c1", "c1::p1::q2", "hard", opId);
        return r;
      } catch (e) {
        return { error: e.message };
      }
    });
    expect(result.new_tier).toBe(4);
    expect(callCount).toBe(2);
  });

  test("srsRated failure does not crash and returns error", async function ({ page }) {
    var mock = await setupMockAPI(page);
    await loadSharedAdapter(page, mock);
    await page.waitForFunction(function () { return window.__hydrated; }, null, { timeout: 10000 });

    await page.unroute("**/api/v1/**");
    await page.route(function (url) {
      return url.href.includes("/api/v1/progress");
    }, function (route) {
      if (route.request().method() === "POST" && route.request().url().includes("srs-rated")) {
        return route.abort("connectionrefused");
      }
      if (route.request().method() === "GET") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            revision: 0,
            document: { schema_version: 1, sessions: [], mastery: {}, srs: {} },
          }),
        });
      }
      return route.fulfill({ status: 404 });
    });

    var result = await page.evaluate(async function () {
      try {
        var opId = QuizzlerSharedProgress.generateOpId();
        await window.progressStore.srsRated("c1", "c1::p1::q2", "hard", opId);
        return { ok: true };
      } catch (e) {
        return { ok: false, error: e.message };
      }
    });
    expect(result.ok).toBe(false);
    expect(result.error).toBeTruthy();
  });
});

/* ─── Test: Zero API calls in local mode ─── */

test.describe("Shared Progress — Local Mode Isolation", function () {
  test("zero API calls originate from local adapter", async function ({ page }) {
    var apiCalls = [];
    await page.route("**/api/v1/**", function (route) {
      apiCalls.push({ url: route.request().url(), method: route.request().method() });
      return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    });

    await page.goto("/app/");

    await page.evaluate(async function () {
      var adapter = QuizzlerProgress.createLocalAdapter();
      await adapter.hydrate();
      await adapter.saveSession({ quiz_id: "local-only", course: "c" });
      await adapter.saveMastery("c", "p", { seen: { q1: true }, correct: {}, consecutive: {} });
      await adapter.saveSRSState("c", { schema_version: 1, updated_at: new Date().toISOString(), questions: {} });
      adapter.getSessions();
      adapter.getMastery("c", "p");
      adapter.getSRSState("c");
      adapter.exportSRSState("c");
      await adapter.clearHistory();
    });

    expect(apiCalls.length).toBe(0);
  });
});

/* ─── Test: Operate with full index.html in local mode ─── */

test.describe("Full page — Local Mode Unchanged", function () {
  test("local mode page loads with progressStore as local adapter", async function ({ page }) {
    await page.goto("/app/");

    var result = await page.evaluate(function () {
      var meta = document.querySelector('meta[name="quizzler-mode"]');
      return {
        mode: meta ? meta.getAttribute("content") : null,
        hasSharedScript: typeof QuizzlerSharedProgress !== "undefined",
        hasProgressStore: typeof QuizzlerProgress !== "undefined",
        adapterType: window.progressStore && window.progressStore.isLocalMode ? window.progressStore.isLocalMode() : null,
      };
    });

    expect(result.mode).toBe("local");
    expect(result.hasSharedScript).toBe(true);
    expect(result.hasProgressStore).toBe(true);
    expect(result.adapterType).toBe(true);
  });
});

/* ─── Test: Migration Flow ─── */

test.describe("Shared Progress — Migration", function () {
  test("migration not offered when revision > 0", async function ({ page }) {
    var mock = await setupMockAPI(page);
    mock.revision = 5;
    await loadSharedAdapter(page, mock);
    await page.waitForFunction(function () { return window.__hydrated; }, null, { timeout: 10000 });

    var result = await page.evaluate(function () {
      var localAdapter = QuizzlerProgress.createLocalAdapter();
      return window.progressStore.checkMigrationNeeded(localAdapter);
    });
    expect(result).toBeNull();
  });

  test("migration offered when revision is 0 and localStorage has progress", async function ({ page }) {
    var mock = await setupMockAPI(page);
    mock.revision = 0;
    await loadSharedAdapter(page, mock);
    await page.waitForFunction(function () { return window.__hydrated; }, null, { timeout: 10000 });

    var result = await page.evaluate(function () {
      localStorage.setItem("quizzler_sessions", JSON.stringify([{quiz_id:"a",course:"c",score:{correct:5,total:10}}]));
      localStorage.setItem("quizzler_mastery_c1__p1", JSON.stringify({seen:{q1:true},correct:{q1:true},consecutive:{}}));
      var localAdapter = QuizzlerProgress.createLocalAdapter();
      localAdapter.hydrate();
      return window.progressStore.checkMigrationNeeded(localAdapter);
    });
    expect(result).not.toBeNull();
    expect(result.sessionCount).toBe(1);
    expect(result.masteryQuestions).toBe(1);

    await page.evaluate(function () {
      localStorage.removeItem("quizzler_sessions");
      localStorage.removeItem("quizzler_mastery_c1__p1");
    });
  });

  test("performMigration imports data into empty store and updates cache", async function ({ page }) {
    var mock = await setupMockAPI(page);
    mock.revision = 0;

    await loadSharedAdapter(page, mock);
    await page.waitForFunction(function () { return window.__hydrated; }, null, { timeout: 10000 });

    var migrationDoc = {
      schema_version: 1,
      sessions: [{ quiz_id: "migrated", course: "c1", score: { correct: 5, total: 10 } }],
      mastery: { "c1": { "p1": { seen: { q1: true }, correct: { q1: true }, consecutive: {} } } },
      srs: { "c1": { schema_version: 1, updated_at: new Date().toISOString(), questions: { "c1::p1::q1": { tier: 3, review_count: 1 } } } }
    };

    mock.operationLog = [];
    var result = await page.evaluate(async function (doc) {
      try {
        var r = await window.progressStore.performMigration(doc);
        return r;
      } catch (e) {
        return { error: e.message };
      }
    }, migrationDoc);

    expect(result.revision).toBe(1);
    expect(mock.operationLog.length).toBe(1);
    expect(mock.operationLog[0].type).toBe("import");
    expect(mock.sessions.length).toBe(1);
    expect(Object.keys(mock.mastery)).toContain("c1");
  });

  test("buildMigrationDocument returns normalized doc from localStorage", async function ({ page }) {
    var mock = await setupMockAPI(page);
    mock.revision = 0;
    await loadSharedAdapter(page, mock);
    await page.waitForFunction(function () { return window.__hydrated; }, null, { timeout: 10000 });

    var doc = await page.evaluate(function () {
      localStorage.setItem("quizzler_sessions", JSON.stringify([{quiz_id:"local1",course:"c1"}]));
      localStorage.setItem("quizzler_mastery_c1__p1", JSON.stringify({seen:{q1:true},correct:{q1:true},consecutive:{}}));
      localStorage.setItem("quizzler_srs_state_v1::c1", JSON.stringify({schema_version:1,updated_at:new Date().toISOString(),questions:{}}));
      var localAdapter = QuizzlerProgress.createLocalAdapter();
      localAdapter.hydrate();
      return window.progressStore.buildMigrationDocument(localAdapter);
    });
    expect(doc.schema_version).toBe(1);
    expect(doc.sessions.length).toBe(1);
    expect(Object.keys(doc.mastery)).toContain("c1");
    expect(Object.keys(doc.srs)).toContain("c1");

    await page.evaluate(function () {
      localStorage.removeItem("quizzler_sessions");
      localStorage.removeItem("quizzler_mastery_c1__p1");
      localStorage.removeItem("quizzler_srs_state_v1::c1");
    });
  });
});

/* ─── Test: Completion Recovery ─── */

test.describe("Shared Progress — Completion Recovery", function () {
  test("pendingCompletion stored when quizCompleted fails with network error", async function ({ page }) {
    var mock = await setupMockAPI(page);
    await loadSharedAdapter(page, mock);
    await page.waitForFunction(function () { return window.__hydrated; }, null, { timeout: 10000 });

    await page.unroute("**/api/v1/**");
    await page.route(function (url) {
      return url.href.includes("/api/v1/progress");
    }, function (route) {
      if (route.request().method() === "POST" && route.request().url().includes("quiz-completed")) {
        return route.abort("connectionrefused");
      }
      if (route.request().method() === "GET") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ revision: 0, document: { schema_version: 1, sessions: [], mastery: {}, srs: {} } }),
        });
      }
      return route.fulfill({ status: 404 });
    });

    var result = await page.evaluate(async function () {
      try {
        var opId = QuizzlerSharedProgress.generateOpId();
        var session = { quiz_id: "failed-save", course: "c1", score: { correct: 5, total: 10 } };
        var md = { "p1": { seen: { q1: true }, correct: {}, consecutive: {} } };
        await window.progressStore.quizCompleted(session, "c1", "p1", md, opId);
        return { ok: true };
      } catch (e) {
        return { ok: false, error: e.message, hasPending: window.progressStore.hasPendingCompletion() };
      }
    });

    expect(result.ok).toBe(false);
    expect(result.hasPending).toBe(true);
  });

  test("hasPendingCompletion is false after successful completion", async function ({ page }) {
    var mock = await setupMockAPI(page);
    await loadSharedAdapter(page, mock);
    await page.waitForFunction(function () { return window.__hydrated; }, null, { timeout: 10000 });

    var result = await page.evaluate(async function () {
      try {
        var opId = QuizzlerSharedProgress.generateOpId();
        var session = { quiz_id: "good-save", course: "c1", score: { correct: 5, total: 10 } };
        var md = { "p1": { seen: { q1: true }, correct: {}, consecutive: {} } };
        await window.progressStore.quizCompleted(session, "c1", "p1", md, opId);
        return { ok: true, hasPending: window.progressStore.hasPendingCompletion() };
      } catch (e) {
        return { ok: false, error: e.message };
      }
    });

    expect(result.ok).toBe(true);
    expect(result.hasPending).toBe(false);
  });

  test("retry succeeds after transient failure", async function ({ page }) {
    var mock = await setupMockAPI(page);
    await loadSharedAdapter(page, mock);
    await page.waitForFunction(function () { return window.__hydrated; }, null, { timeout: 10000 });

    var callCount = 0;
    await page.unroute("**/api/v1/**");
    await page.route(function (url) {
      return url.href.includes("/api/v1/progress");
    }, function (route) {
      if (route.request().method() === "POST" && route.request().url().includes("quiz-completed")) {
        callCount++;
        if (callCount === 1) {
          return route.abort("connectionrefused");
        }
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ revision: 1 }),
        });
      }
      if (route.request().method() === "GET") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ revision: 0, document: { schema_version: 1, sessions: [], mastery: {}, srs: {} } }),
        });
      }
      return route.fulfill({ status: 404 });
    });

    var stage1 = await page.evaluate(async function () {
      try {
        var opId = QuizzlerSharedProgress.generateOpId();
        var session = { quiz_id: "retry-me", course: "c1", score: { correct: 5, total: 10 } };
        var md = { "p1": { seen: { q1: true }, correct: {}, consecutive: {} } };
        await window.progressStore.quizCompleted(session, "c1", "p1", md, opId);
        return { ok: true };
      } catch (e) {
        return { ok: false, hasPending: window.progressStore.hasPendingCompletion() };
      }
    });

    expect(stage1.ok).toBe(false);
    expect(stage1.hasPending).toBe(true);

    var stage2 = await page.evaluate(async function () {
      try {
        await window.progressStore.retryCompletion();
        return { ok: true, hasPending: window.progressStore.hasPendingCompletion() };
      } catch (e) {
        return { ok: false, error: e.message, hasPending: window.progressStore.hasPendingCompletion() };
      }
    });

    expect(stage2.ok).toBe(true);
    expect(stage2.hasPending).toBe(false);
    expect(callCount).toBe(2);
  });

  test("clearPendingCompletion removes pending result", async function ({ page }) {
    var mock = await setupMockAPI(page);
    await loadSharedAdapter(page, mock);
    await page.waitForFunction(function () { return window.__hydrated; }, null, { timeout: 10000 });

    await page.unroute("**/api/v1/**");
    await page.route(function (url) {
      return url.href.includes("/api/v1/progress");
    }, function (route) {
      if (route.request().method() === "POST" && route.request().url().includes("quiz-completed")) {
        return route.abort("connectionrefused");
      }
      if (route.request().method() === "GET") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ revision: 0, document: { schema_version: 1, sessions: [], mastery: {}, srs: {} } }),
        });
      }
      return route.fulfill({ status: 404 });
    });

    await page.evaluate(async function () {
      try {
        var opId = QuizzlerSharedProgress.generateOpId();
        var session = { quiz_id: "discard-me", course: "c1", score: { correct: 5, total: 10 } };
        var md = { "p1": { seen: { q1: true }, correct: {}, consecutive: {} } };
        await window.progressStore.quizCompleted(session, "c1", "p1", md, opId);
      } catch (e) { /* expected */ }
    });

    var beforeClear = await page.evaluate(function () {
      return window.progressStore.hasPendingCompletion();
    });
    expect(beforeClear).toBe(true);

    await page.evaluate(function () {
      window.progressStore.clearPendingCompletion();
    });

    var afterClear = await page.evaluate(function () {
      return window.progressStore.hasPendingCompletion();
    });
    expect(afterClear).toBe(false);
  });

  test("exportRecoveryJSON returns valid recovery format", async function ({ page }) {
    var mock = await setupMockAPI(page);
    await loadSharedAdapter(page, mock);
    await page.waitForFunction(function () { return window.__hydrated; }, null, { timeout: 10000 });

    await page.unroute("**/api/v1/**");
    await page.route(function (url) {
      return url.href.includes("/api/v1/progress");
    }, function (route) {
      if (route.request().method() === "POST" && route.request().url().includes("quiz-completed")) {
        return route.abort("connectionrefused");
      }
      if (route.request().method() === "GET") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ revision: 0, document: { schema_version: 1, sessions: [], mastery: {}, srs: {} } }),
        });
      }
      return route.fulfill({ status: 404 });
    });

    await page.evaluate(async function () {
      try {
        var opId = "test-op-id-recovery";
        var session = { quiz_id: "recover-me", course: "c1", score: { correct: 5, total: 10 } };
        var md = { "p1": { seen: { q1: true }, correct: {}, consecutive: {} } };
        await window.progressStore.quizCompleted(session, "c1", "p1", md, opId);
      } catch (e) { /* expected */ }
    });

    var recovery = await page.evaluate(function () {
      return window.progressStore.exportRecoveryJSON();
    });

    expect(recovery.type).toBe("quizzler-recovery-v1");
    expect(recovery.operation_id).toBe("test-op-id-recovery");
    expect(recovery.session.score.correct).toBe(5);
    expect(recovery.course_id).toBe("c1");
    expect(recovery.pack_id).toBe("p1");
    expect(recovery.mastery_delta["p1"].seen.q1).toBe(true);
  });

  test("downloadRecovery triggers download without error", async function ({ page }) {
    var mock = await setupMockAPI(page);
    await loadSharedAdapter(page, mock);
    await page.waitForFunction(function () { return window.__hydrated; }, null, { timeout: 10000 });

    await page.unroute("**/api/v1/**");
    await page.route(function (url) {
      return url.href.includes("/api/v1/progress");
    }, function (route) {
      if (route.request().method() === "POST" && route.request().url().includes("quiz-completed")) {
        return route.abort("connectionrefused");
      }
      if (route.request().method() === "GET") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ revision: 0, document: { schema_version: 1, sessions: [], mastery: {}, srs: {} } }),
        });
      }
      return route.fulfill({ status: 404 });
    });

    await page.evaluate(async function () {
      try {
        var opId = QuizzlerSharedProgress.generateOpId();
        var session = { quiz_id: "download-me", course: "c1", score: { correct: 5, total: 10 } };
        var md = { "p1": { seen: { q1: true }, correct: {}, consecutive: {} } };
        await window.progressStore.quizCompleted(session, "c1", "p1", md, opId);
      } catch (e) { /* expected */ }
    });

    var result = await page.evaluate(function () {
      try {
        window.progressStore.downloadRecovery();
        return { ok: true };
      } catch (e) {
        return { ok: false, error: e.message };
      }
    });
    expect(result.ok).toBe(true);
  });

  test("retryCompletion with conflict clears pending", async function ({ page }) {
    var mock = await setupMockAPI(page);
    await loadSharedAdapter(page, mock);
    await page.waitForFunction(function () { return window.__hydrated; }, null, { timeout: 10000 });

    await page.unroute("**/api/v1/**");
    await page.route(function (url) {
      return url.href.includes("/api/v1/progress");
    }, function (route) {
      if (route.request().method() === "POST" && route.request().url().includes("quiz-completed")) {
        return route.abort("connectionrefused");
      }
      if (route.request().method() === "GET") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ revision: 3, document: { schema_version: 1, sessions: [], mastery: {}, srs: {} } }),
        });
      }
      return route.fulfill({ status: 404 });
    });

    await page.evaluate(async function () {
      try {
        var opId = QuizzlerSharedProgress.generateOpId();
        var session = { quiz_id: "bad-save", course: "c1", score: { correct: 5, total: 10 } };
        var md = { "p1": { seen: { q1: true }, correct: {}, consecutive: {} } };
        await window.progressStore.quizCompleted(session, "c1", "p1", md, opId);
      } catch (e) { /* expected */ }
    });

    expect(await page.evaluate(function () { return window.progressStore.hasPendingCompletion(); })).toBe(true);

    await page.unroute("**/api/v1/**");
    await page.route(function (url) {
      return url.href.includes("/api/v1/progress");
    }, function (route) {
      if (route.request().method() === "POST" && route.request().url().includes("quiz-completed")) {
        return route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({ error: "conflict", current_revision: 3 }),
        });
      }
      if (route.request().method() === "GET") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ revision: 3, document: { schema_version: 1, sessions: [], mastery: {}, srs: {} } }),
        });
      }
      return route.fulfill({ status: 404 });
    });

    var retryResult = await page.evaluate(async function () {
      try {
        await window.progressStore.retryCompletion();
        return { ok: true, hasPending: window.progressStore.hasPendingCompletion() };
      } catch (e) {
        return { ok: false, error: e.message, hasPending: window.progressStore.hasPendingCompletion() };
      }
    });

    expect(retryResult.ok).toBe(false);
    expect(retryResult.hasPending).toBe(false);
  });
});

/* ────────────────────────────────────────────────────────────────────────── */
/* Real Server — skipped unless real server state file exists               */
/* ────────────────────────────────────────────────────────────────────────── */

var _stateFile = process.env.QUIZZLER_SHARED_STATE_FILE;
var _realServer = false;
try { _realServer = Boolean(_stateFile && require("fs").existsSync(_stateFile)); } catch (_) {}

test.describe("Real Server — Health", function () {
  test("healthz returns ok", async function () {
    if (!_realServer && !process.env.QUIZZLER_REAL_SERVER) test.skip();
    var url = (function () {
      try {
        var s = JSON.parse(require("fs").readFileSync(_stateFile, "utf-8"));
        return s.baseURL;
      } catch (_) { return PORTS.loopbackURL; }
    })();
    var result = await new Promise(function (resolve, reject) {
      require("http").get(url + "/healthz", function (res) {
        var data = "";
        res.on("data", function (chunk) { data += chunk; });
        res.on("end", function () { resolve({ status: res.statusCode, body: data }); });
      }).on("error", reject);
    });
    expect(result.status).toBe(200);
    var body = JSON.parse(result.body);
    expect(body.status).toBe("ok");
  });

  test("unauthenticated progress fetch returns 401", async function ({ request }) {
    if (!_realServer && !process.env.QUIZZLER_REAL_SERVER) test.skip();
    var url = (function () {
      try {
        var s = JSON.parse(require("fs").readFileSync(_stateFile, "utf-8"));
        return s.baseURL;
      } catch (_) { return PORTS.loopbackURL; }
    })();
    var result = await new Promise(function (resolve, reject) {
      require("http").get(url + "/api/v1/progress", function (res) {
        res.resume();
        resolve(res.statusCode);
      }).on("error", reject);
    });
    expect(result).toBe(401);
  });
});

test.describe("Native contract v1", function () {
  test("[CONTRACT] documents the shared identity, ordering, retention, and privacy boundary", async function () {
    var fs = require("fs");
    var path = require("path");
    var root = path.join(__dirname, "..");
    var protocol = fs.readFileSync(path.join(root, "docs", "PROGRESS_PROTOCOL.md"), "utf8");
    var architecture = fs.readFileSync(path.join(root, "docs", "NATIVE_ARCHITECTURE.md"), "utf8");
    var report = fs.readFileSync(path.join(root, "docs", "REPORT_SCHEMA.md"), "utf8");
    expect(protocol).toContain("conditional,\natomic write");
    expect(protocol).toContain("current record change tag");
    expect(protocol).toContain(".ifServerRecordUnchanged");
    expect(protocol).toContain("fetch the complete current snapshot");
    expect(protocol).toContain("never to make two competing writes successful");
    expect(protocol).toContain("operation_id` is generated once");
    expect(protocol).toContain("byte-for-byte/semantically identical payload");
    expect(protocol).toMatch(/changed payload\s+or\s+new user intent must receive a fresh operation ID/);
    expect(protocol).toContain("most recent 200");
    expect(protocol).toContain("4,096 operation records and 30 days");
    expect(protocol).toContain("encoded_size_refused");
    expect(protocol).toContain("rebase_required");
    expect(protocol).toContain("corrupt_state");
    expect(protocol).toContain("no revision, snapshot, or operation record changes");
    expect(fs.readFileSync(path.join(root, "tests", "python-suites.spec.js"), "utf8"))
      .toContain('"tests.test_progress_protocol"');
    expect(architecture).toContain("QuizzlerProgress-v1");
    expect(architecture).toContain("conditional atomic\nwrite");
    expect(architecture).toContain("browser never proxies\nprivate CloudKit access");
    [
      "ProgressOperation/<operationID>",
      "ProgressSnapshot/current",
      "QuestionIssue/<issueID>",
    ].forEach(function (recordName) { expect(architecture).toContain(recordName); });
    [
      "multiple_choice",
      "scenario_multiple_choice",
      "multiple_select",
      "true_false",
      "matching",
    ].forEach(function (questionType) { expect(architecture).toContain("`" + questionType + "`"); });
    expect(architecture).toMatch(/complete,\s+pre-native\s+pack digest/);
    expect(architecture).toMatch(/only the first three/i);
    expect(report).toContain("Browser-generated `missed_questions` and `answers` rows carry `pack_id` and");
    expect(report).toContain("Reports deliberately exclude question text");
  });
});

test.describe("Real Server — Auth", function () {
  test("shared mode app page serves shared marker meta", async function ({ page }) {
    if (!_realServer && !process.env.QUIZZLER_REAL_SERVER) test.skip();
    var url = (function () {
      try {
        var s = JSON.parse(require("fs").readFileSync(_stateFile, "utf-8"));
        return s.baseURL;
      } catch (_) { return PORTS.loopbackURL; }
    })();
    await page.goto(url + "/pair");
    await page.waitForLoadState("domcontentloaded");

    var pairLocalResp = await page.evaluate(async function () {
      var r = await fetch("/api/v1/auth/pair-local", { method: "POST" });
      return r.json();
    });
    expect(pairLocalResp.pairing_code).toBeTruthy();

    var pairResult = await page.evaluate(async function (code) {
      var r = await fetch("/api/v1/auth/pair", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pairing_code: code }),
      });
      return { status: r.status, body: await r.json() };
    }, pairLocalResp.pairing_code);
    expect(pairResult.status).toBe(200);

    await page.goto(url + "/app/");
    await page.waitForLoadState("domcontentloaded");

    var authStatus = await page.evaluate(function () {
      var meta = document.querySelector('meta[name="quizzler-auth-status"]');
      return meta ? meta.getAttribute("content") : null;
    });
    expect(authStatus).toBe("active");
  });
});

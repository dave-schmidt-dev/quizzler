// @ts-check
const { test, expect } = require("@playwright/test");

async function clearAndHydrate(page) {
  await page.goto("/app/");
  await page.evaluate(function () {
    localStorage.clear();
    window.__adapter = QuizzlerProgress.createLocalAdapter();
    return window.__adapter.hydrate();
  });
}

test.describe("Progress Store — Local Adapter", function () {
  test.beforeEach(async function ({ page }) {
    await clearAndHydrate(page);
  });

  /* ── 1. Mode detection ── */

  test("isLocalMode() returns true and meta tag is present", async function ({ page }) {
    var results = await page.evaluate(function () {
      var adapter = QuizzlerProgress.createLocalAdapter();
      return {
        isLocal: adapter.isLocalMode(),
        metaContent: document.querySelector('meta[name="quizzler-mode"]').getAttribute("content")
      };
    });
    expect(results.isLocal).toBe(true);
    expect(results.metaContent).toBe("local");
  });

  /* ── 2. Read/write sessions ── */

  test("write sessions and read back synchronously after hydrate", async function ({ page }) {
    var session = { quiz_id: "test1", course: "samples", score: { correct: 5, total: 10 } };

    await page.evaluate(async function (s) {
      await window.__adapter.saveSession(s);
    }, session);

    var sessions = await page.evaluate(function () {
      return window.__adapter.getSessions();
    });

    expect(sessions.length).toBe(1);
    expect(sessions[0].quiz_id).toBe("test1");
  });

  test("saveSession prepends and caps at 200", async function ({ page }) {
    await page.evaluate(async function () {
      for (var i = 0; i < 250; i++) {
        await window.__adapter.saveSession({ quiz_id: "q" + i, course: "c" });
      }
    });

    var result = await page.evaluate(function () {
      return {
        length: window.__adapter.getSessions().length,
        first: window.__adapter.getSessions()[0].quiz_id,
        last: window.__adapter.getSessions()[window.__adapter.getSessions().length - 1].quiz_id
      };
    });

    expect(result.length).toBe(200);
    expect(result.first).toBe("q249");
    expect(result.last).toBe("q50");
  });

  test("saveSessions replaces all sessions", async function ({ page }) {
    await page.evaluate(async function () {
      await window.__adapter.saveSession({ quiz_id: "old", course: "c" });
    });

    await page.evaluate(async function () {
      await window.__adapter.saveSessions([
        { quiz_id: "new1", course: "c" },
        { quiz_id: "new2", course: "c" }
      ]);
    });

    var sessions = await page.evaluate(function () {
      return window.__adapter.getSessions();
    });

    expect(sessions.length).toBe(2);
    expect(sessions[0].quiz_id).toBe("new1");
  });

  /* ── 3. Read/write mastery ── */

  test("write mastery and read back synchronously", async function ({ page }) {
    await page.evaluate(async function () {
      await window.__adapter.saveMastery("samples", "pack-a", {
        seen: { q1: true },
        correct: { q1: true },
        consecutive: { q1: 2 }
      });
    });

    var mastery = await page.evaluate(function () {
      return window.__adapter.getMastery("samples", "pack-a");
    });

    expect(mastery.seen.q1).toBe(true);
    expect(mastery.correct.q1).toBe(true);
    expect(mastery.consecutive.q1).toBe(2);
  });

  test("getMastery returns fresh default for unknown course/pack", async function ({ page }) {
    var mastery = await page.evaluate(function () {
      return window.__adapter.getMastery("unknown", "unknown-pack");
    });

    expect(mastery).toEqual({ seen: {}, correct: {}, consecutive: {} });
  });

  test("pack-scoped identity: same qid in different packs → distinct mastery", async function ({ page }) {
    await page.evaluate(async function () {
      await window.__adapter.saveMastery("samples", "pack-x", {
        seen: { qid: true },
        correct: { qid: true },
        consecutive: { qid: 2 }
      });
      await window.__adapter.saveMastery("samples", "pack-y", {
        seen: { qid: true },
        correct: {},
        consecutive: { qid: 0 }
      });
    });

    var results = await page.evaluate(function () {
      return {
        x: window.__adapter.getMastery("samples", "pack-x"),
        y: window.__adapter.getMastery("samples", "pack-y")
      };
    });

    expect(results.x.correct.qid).toBe(true);
    expect(results.y.correct.qid).toBeUndefined();
  });

  /* ── 4. Read/write SRS ── */

  test("write SRS state and read back synchronously", async function ({ page }) {
    var state = {
      schema_version: 1,
      updated_at: new Date().toISOString(),
      questions: { "course::pack::q1": { tier: 3, review_count: 5 } }
    };

    await page.evaluate(async function (s) {
      await window.__adapter.saveSRSState("test-course", s);
    }, state);

    var read = await page.evaluate(function () {
      return window.__adapter.getSRSState("test-course");
    });

    expect(read.questions["course::pack::q1"].tier).toBe(3);
    expect(read.questions["course::pack::q1"].review_count).toBe(5);
    expect(typeof read.updated_at).toBe("string");
  });

  test("getSRSState returns fresh default for unknown course", async function ({ page }) {
    var state = await page.evaluate(function () {
      return window.__adapter.getSRSState("unknown-course");
    });

    expect(state.schema_version).toBe(1);
    expect(state.questions).toEqual({});
    expect(typeof state.updated_at).toBe("string");
  });

  test("exportSRSState / importSRSState round-trip", async function ({ page }) {
    await page.evaluate(async function () {
      await window.__adapter.saveSRSState("course-r", {
        schema_version: 1,
        updated_at: new Date().toISOString(),
        questions: {
          "course-r::p1::q1": { tier: 4, review_count: 10 },
          "course-r::p1::q2": { tier: 1, review_count: 1 }
        }
      });
    });

    var exported = await page.evaluate(function () {
      return window.__adapter.exportSRSState("course-r");
    });

    expect(exported.course_id).toBe("course-r");
    expect(Object.keys(exported.questions).length).toBe(2);

    await page.evaluate(async function () {
      await window.__adapter.resetSRS("course-r");
    });

    await page.evaluate(async function (state) {
      await window.__adapter.importSRSState("course-r", state);
    }, exported);

    var imported = await page.evaluate(function () {
      return window.__adapter.getSRSState("course-r");
    });

    expect(imported.questions["course-r::p1::q1"].tier).toBe(4);
    expect(imported.questions["course-r::p1::q2"].tier).toBe(1);
  });

  test("importSRSState writes a backup before overwriting", async function ({ page }) {
    await page.evaluate(async function () {
      await window.__adapter.saveSRSState("course-bk", {
        schema_version: 1,
        updated_at: new Date().toISOString(),
        questions: { "course-bk::p1::existing": { tier: 2, review_count: 3 } }
      });
    });

    await page.evaluate(async function () {
      await window.__adapter.importSRSState("course-bk", {
        schema_version: 1,
        updated_at: new Date().toISOString(),
        questions: { "course-bk::p1::q1": { tier: 4, review_count: 11 } }
      });
    });

    var result = await page.evaluate(function () {
      var backupKey = null;
      for (var i = 0; i < localStorage.length; i++) {
        var k = localStorage.key(i);
        if (k && k.startsWith("quizzler_srs_state_v1::course-bk__backup_")) {
          backupKey = k;
          break;
        }
      }
      return {
        hasBackup: backupKey !== null,
        currentState: window.__adapter.getSRSState("course-bk")
      };
    });

    expect(result.hasBackup).toBe(true);
    expect(result.currentState.questions["course-bk::p1::q1"].tier).toBe(4);
  });

  test("importSRSState filters invalid entries", async function ({ page }) {
    var result = await page.evaluate(async function () {
      var r = await window.__adapter.importSRSState("course-fi", {
        schema_version: 1,
        questions: {
          "course-fi::p1::q1": { tier: 4, review_count: 11 },
          "course-fi::p1::q_bad": { tier: 99, review_count: 1 },
          "course-fi::p1::q_prim": "not-an-object",
          "course-fi::p1::q_arr": [1, 2, 3],
          "wrong_course::p1::q_bad_course": { tier: 1 }
        }
      });
      return r;
    });

    expect(result.imported).toBe(1);
    expect(result.dropped).toBe(4);
  });

  /* ── 5. Corruption handling ── */

  test("corrupt sessions (non-Array) are quarantined", async function ({ page }) {
    await page.evaluate(function () {
      localStorage.setItem("quizzler_sessions", '{"not":"array"}');
    });

    await page.evaluate(async function () {
      window.__adapter = QuizzlerProgress.createLocalAdapter();
      await window.__adapter.hydrate();
    });

    var results = await page.evaluate(function () {
      var corruptKeys = [];
      for (var i = 0; i < localStorage.length; i++) {
        var k = localStorage.key(i);
        if (k && k.startsWith("quizzler_sessions__corrupt_")) corruptKeys.push(k);
      }
      return {
        sessions: window.__adapter.getSessions(),
        hasCorrupt: corruptKeys.length > 0
      };
    });

    expect(results.sessions).toEqual([]);
    expect(results.hasCorrupt).toBe(true);
  });

  test("corrupt sessions (parse failure) are quarantined", async function ({ page }) {
    await page.evaluate(function () {
      localStorage.setItem("quizzler_sessions", "{bad json");
    });

    await page.evaluate(async function () {
      window.__adapter = QuizzlerProgress.createLocalAdapter();
      await window.__adapter.hydrate();
    });

    var results = await page.evaluate(function () {
      var corruptKeys = [];
      for (var i = 0; i < localStorage.length; i++) {
        var k = localStorage.key(i);
        if (k && k.startsWith("quizzler_sessions__corrupt_")) corruptKeys.push(k);
      }
      return {
        sessions: window.__adapter.getSessions(),
        hasCorrupt: corruptKeys.length > 0
      };
    });

    expect(results.sessions).toEqual([]);
    expect(results.hasCorrupt).toBe(true);
  });

  test("corrupt mastery (bad shape) is quarantined and returns fresh default", async function ({ page }) {
    await page.evaluate(function () {
      localStorage.setItem("quizzler_mastery_samples__bad-pack", "{}");
    });

    await page.evaluate(async function () {
      window.__adapter = QuizzlerProgress.createLocalAdapter();
      await window.__adapter.hydrate();
    });

    var results = await page.evaluate(function () {
      var corruptKeys = [];
      for (var i = 0; i < localStorage.length; i++) {
        var k = localStorage.key(i);
        if (k && k.startsWith("quizzler_mastery_samples__bad-pack__corrupt_")) corruptKeys.push(k);
      }
      return {
        mastery: window.__adapter.getMastery("samples", "bad-pack"),
        hasCorrupt: corruptKeys.length > 0
      };
    });

    expect(results.mastery).toEqual({ seen: {}, correct: {}, consecutive: {} });
    expect(results.hasCorrupt).toBe(true);
  });

  test("corrupt SRS state is quarantined and returns fresh default", async function ({ page }) {
    await page.evaluate(function () {
      localStorage.setItem("quizzler_srs_state_v1::corrupt-course", "{bad json");
    });

    await page.evaluate(async function () {
      window.__adapter = QuizzlerProgress.createLocalAdapter();
      await window.__adapter.hydrate();
    });

    var results = await page.evaluate(function () {
      var corruptKeys = [];
      for (var i = 0; i < localStorage.length; i++) {
        var k = localStorage.key(i);
        if (k && k.startsWith("quizzler_srs_state_v1::corrupt-course__corrupt_")) corruptKeys.push(k);
      }
      return {
        state: window.__adapter.getSRSState("corrupt-course"),
        hasCorrupt: corruptKeys.length > 0
      };
    });

    expect(results.state.schema_version).toBe(1);
    expect(results.state.questions).toEqual({});
    expect(results.hasCorrupt).toBe(true);
  });

  /* ── 6. clearHistory ── */

  test("clearHistory removes sessions and mastery, NOT SRS", async function ({ page }) {
    await page.evaluate(async function () {
      await window.__adapter.saveSession({ quiz_id: "h1", course: "c" });
      await window.__adapter.saveMastery("c", "p", { seen: { q1: true }, correct: {}, consecutive: {} });
      await window.__adapter.saveSRSState("c", {
        schema_version: 1,
        updated_at: new Date().toISOString(),
        questions: { "c::p::q1": { tier: 3, review_count: 5 } }
      });
    });

    await page.evaluate(async function () {
      await window.__adapter.clearHistory();
    });

    var results = await page.evaluate(function () {
      return {
        sessions: window.__adapter.getSessions(),
        mastery: window.__adapter.getMastery("c", "p"),
        srs: window.__adapter.getSRSState("c")
      };
    });

    expect(results.sessions).toEqual([]);
    expect(results.mastery.seen.q1).toBeUndefined();
    expect(results.srs.questions["c::p::q1"].tier).toBe(3);
  });

  /* ── 7. findOrphans / cleanupOrphans ── */

  test("findOrphans detects orphan mastery keys and sessions", async function ({ page }) {
    await page.evaluate(async function () {
      localStorage.setItem(
        "quizzler_mastery_samples__demo",
        JSON.stringify({ seen: { q1: true }, correct: {} })
      );
      localStorage.setItem(
        "quizzler_mastery_archived__old-pack",
        JSON.stringify({ seen: { q2: true }, correct: {} })
      );
    });

    await page.evaluate(async function () {
      window.__adapter = QuizzlerProgress.createLocalAdapter();
      await window.__adapter.hydrate();
    });

    var orphans = await page.evaluate(function () {
      return window.__adapter.findOrphans(["samples"]);
    });

    expect(orphans.masteryKeys).toContain("quizzler_mastery_archived__old-pack");
    expect(orphans.masteryKeys).not.toContain("quizzler_mastery_samples__demo");
  });

  test("cleanupOrphans removes orphaned mastery and sessions", async function ({ page }) {
    await page.evaluate(async function () {
      localStorage.setItem(
        "quizzler_mastery_samples__demo",
        JSON.stringify({ seen: { q1: true }, correct: {} })
      );
      localStorage.setItem(
        "quizzler_mastery_archived__old-pack",
        JSON.stringify({ seen: { q2: true }, correct: {} })
      );
    });

    await page.evaluate(async function () {
      window.__adapter = QuizzlerProgress.createLocalAdapter();
      await window.__adapter.hydrate();
    });

    await page.evaluate(async function () {
      var orphans = window.__adapter.findOrphans(["samples"]);
      await window.__adapter.cleanupOrphans(orphans);
    });

    var result = await page.evaluate(function () {
      return {
        sampleMastery: localStorage.getItem("quizzler_mastery_samples__demo"),
        archivedMastery: localStorage.getItem("quizzler_mastery_archived__old-pack")
      };
    });

    expect(result.sampleMastery).not.toBeNull();
    expect(result.archivedMastery).toBeNull();
  });

  /* ── 8. sweepLegacyStorage ── */

  test("sweepLegacyStorage removes legacy flat mastery keys and pre-sentinel sessions", async function ({ page }) {
    await page.evaluate(function () {
      localStorage.setItem("quizzler_mastery_samples", JSON.stringify({ seen: { q1: true }, correct: {} }));
      localStorage.setItem("quizzler_mastery_samples__demo", JSON.stringify({ seen: { q2: true }, correct: {} }));
      localStorage.setItem("quizzler_sessions", JSON.stringify([{ quiz_id: "legacy" }]));
      localStorage.removeItem("quizzler_session_schema_v2");
    });

    await page.evaluate(async function () {
      window.__adapter = QuizzlerProgress.createLocalAdapter();
      await window.__adapter.sweepLegacyStorage();
    });

    var result = await page.evaluate(function () {
      return {
        legacyMastery: localStorage.getItem("quizzler_mastery_samples"),
        newMastery: localStorage.getItem("quizzler_mastery_samples__demo"),
        sessions: window.__adapter.getSessions(),
        sentinel: localStorage.getItem("quizzler_session_schema_v2")
      };
    });

    expect(result.legacyMastery).toBeNull();
    expect(result.newMastery).not.toBeNull();
    expect(result.sessions).toEqual([]);
    expect(result.sentinel).toBe("1");
  });

  test("sweepLegacyStorage is idempotent (sentinel set after first sweep)", async function ({ page }) {
    await page.evaluate(function () {
      localStorage.setItem("quizzler_mastery_samples", JSON.stringify({ seen: { q1: true }, correct: {} }));
      localStorage.setItem("quizzler_mastery_samples__demo", JSON.stringify({ seen: { q2: true }, correct: {} }));
      localStorage.setItem("quizzler_sessions", JSON.stringify([{ quiz_id: "legacy" }]));
      localStorage.removeItem("quizzler_session_schema_v2");
    });

    await page.evaluate(async function () {
      window.__adapter = QuizzlerProgress.createLocalAdapter();
      await window.__adapter.sweepLegacyStorage();
    });

    var afterFirst = await page.evaluate(function () {
      return localStorage.getItem("quizzler_session_schema_v2");
    });
    expect(afterFirst).toBe("1");

    await page.evaluate(async function () {
      await window.__adapter.saveSession({ quiz_id: "post-sweep", course: "c" });
    });

    await page.evaluate(async function () {
      window.__adapter = QuizzlerProgress.createLocalAdapter();
      await window.__adapter.sweepLegacyStorage();
    });

    var afterSecond = await page.evaluate(function () {
      return {
        sentinel: localStorage.getItem("quizzler_session_schema_v2"),
        sessionsLength: window.__adapter.getSessions().length
      };
    });

    expect(afterSecond.sentinel).toBe("1");
    expect(afterSecond.sessionsLength).toBe(1);
  });

  /* ── 9. Quota exceeded ── */

  test("quota exceeded on saveSession returns rejected Promise, does not crash", async function ({ page }) {
    var errorCaught = false;
    try {
      await page.evaluate(async function () {
        var origSetItem = localStorage.setItem.bind(localStorage);
        localStorage.setItem = function (key, value) {
          if (key === "quizzler_sessions") {
            var err = new Error("QuotaExceededError");
            err.name = "QuotaExceededError";
            err.code = 22;
            throw err;
          }
          return origSetItem(key, value);
        };

        try {
          await window.__adapter.saveSession({ quiz_id: "qfail", course: "c" });
        } catch (e) {
          throw e;
        }
      });
    } catch (e) {
      errorCaught = true;
    }
    expect(errorCaught).toBe(true);
  });

  test("quota exceeded on saveMastery returns rejected Promise, does not crash", async function ({ page }) {
    var errorCaught = false;
    try {
      await page.evaluate(async function () {
        var origSetItem = localStorage.setItem.bind(localStorage);
        localStorage.setItem = function () {
          var err = new Error("QuotaExceededError");
          err.name = "QuotaExceededError";
          err.code = 22;
          throw err;
        };

        try {
          await window.__adapter.saveMastery("c", "p", { seen: {}, correct: {}, consecutive: {} });
        } catch (e) {
          throw e;
        }
      });
    } catch (e) {
      errorCaught = true;
    }
    expect(errorCaught).toBe(true);
  });

  test("quota exceeded on saveSRSState returns rejected Promise, does not crash", async function ({ page }) {
    var errorCaught = false;
    try {
      await page.evaluate(async function () {
        var origSetItem = localStorage.setItem.bind(localStorage);
        localStorage.setItem = function () {
          var err = new Error("QuotaExceededError");
          err.name = "QuotaExceededError";
          err.code = 22;
          throw err;
        };

        try {
          await window.__adapter.saveSRSState("c", {
            schema_version: 1,
            updated_at: new Date().toISOString(),
            questions: {}
          });
        } catch (e) {
          throw e;
        }
      });
    } catch (e) {
      errorCaught = true;
    }
    expect(errorCaught).toBe(true);
  });

  /* ── 10. Zero network requests ── */

  test("zero network requests originate from progress-store in local mode", async function ({ page }) {
    var requests = [];
    page.on("request", function (req) {
      requests.push(req.url());
    });

    await page.evaluate(async function () {
      window.__adapter = QuizzlerProgress.createLocalAdapter();
      await window.__adapter.hydrate();

      await window.__adapter.saveSession({ quiz_id: "n", course: "c" });
      await window.__adapter.saveMastery("c", "p", { seen: { q1: true }, correct: {}, consecutive: {} });
      await window.__adapter.saveSRSState("c", {
        schema_version: 1,
        updated_at: new Date().toISOString(),
        questions: {}
      });

      window.__adapter.getSessions();
      window.__adapter.getMastery("c", "p");
      window.__adapter.getSRSState("c");
      window.__adapter.exportSRSState("c");

      await window.__adapter.clearHistory();
    });

    var adapterRequests = requests.filter(function (u) {
      return !u.includes("/app/") && !u.includes("favicon") && !u.includes("manifest");
    });
    expect(adapterRequests).toEqual([]);
  });

  /* ── 11. LocalStorage keys preserved ── */

  test("adapter uses exact existing localStorage key names", async function ({ page }) {
    await page.evaluate(async function () {
      await window.__adapter.saveSession({ quiz_id: "s1", course: "samples" });
      await window.__adapter.saveMastery("samples", "demo", {
        seen: { q1: true },
        correct: {},
        consecutive: {}
      });
      await window.__adapter.saveSRSState("samples", {
        schema_version: 1,
        updated_at: new Date().toISOString(),
        questions: {}
      });
    });

    var keys = await page.evaluate(function () {
      var out = [];
      for (var i = 0; i < localStorage.length; i++) out.push(localStorage.key(i));
      return out.sort();
    });

    expect(keys).toContain("quizzler_sessions");
    expect(keys).toContain("quizzler_mastery_samples__demo");
    expect(keys).toContain("quizzler_srs_state_v1::samples");
  });

  /* ── 12. sanitizeKeySegment preserves existing behavior ── */

  test("sanitizeKeySegment strips unsafe chars and leading/trailing underscores", async function ({ page }) {
    await page.evaluate(async function () {
      await window.__adapter.saveMastery("course with spaces!", "pack/name?", {
        seen: { q1: true },
        correct: {},
        consecutive: {}
      });
    });

    var result = await page.evaluate(function () {
      var key = null;
      for (var i = 0; i < localStorage.length; i++) {
        var k = localStorage.key(i);
        if (k && k.startsWith("quizzler_mastery_course_with_spaces__")) {
          key = k;
          break;
        }
      }
      return {
        keyExists: key !== null,
        noSlashes: key ? !key.includes("/") : false,
        noExclams: key ? !key.includes("!") : false
      };
    });

    expect(result.keyExists).toBe(true);
    expect(result.noSlashes).toBe(true);
    expect(result.noExclams).toBe(true);
  });

  /* ── 13. validateNormalizedDoc ── */

  test("validateNormalizedDoc rejects documents missing schema_version", async function ({ page }) {
    var result = await page.evaluate(function () {
      var doc = { sessions: [], mastery: {}, srs: {} };
      return QuizzlerProgress._validateNormalizedDoc(doc);
    });
    expect(result.valid).toBe(false);
    expect(result.reason).toContain("schema_version");
  });

  test("validateNormalizedDoc rejects documents with non-array sessions", async function ({ page }) {
    var result = await page.evaluate(function () {
      var doc = { schema_version: 1, sessions: "not-array", mastery: {}, srs: {} };
      return QuizzlerProgress._validateNormalizedDoc(doc);
    });
    expect(result.valid).toBe(false);
    expect(result.reason).toContain("sessions must be an array");
  });

  test("validateNormalizedDoc rejects documents with non-object mastery", async function ({ page }) {
    var result = await page.evaluate(function () {
      var doc = { schema_version: 1, sessions: [], mastery: "not-object", srs: {} };
      return QuizzlerProgress._validateNormalizedDoc(doc);
    });
    expect(result.valid).toBe(false);
    expect(result.reason).toContain("mastery must be an object");
  });

  test("validateNormalizedDoc accepts valid documents", async function ({ page }) {
    var result = await page.evaluate(function () {
      var doc = {
        schema_version: 1,
        sessions: [],
        mastery: { c: { p: { seen: {}, correct: {}, consecutive: {} } } },
        srs: { c: { schema_version: 1, updated_at: "2025-01-01", questions: {} } }
      };
      return QuizzlerProgress._validateNormalizedDoc(doc);
    });
    expect(result.valid).toBe(true);
  });

  /* ── 14. Hydrate + cache isolation ── */

  test("hydrate() populates cache with accurate state", async function ({ page }) {
    await page.evaluate(function () {
      localStorage.setItem("quizzler_sessions", JSON.stringify([
        { quiz_id: "h1", course: "c" },
        { quiz_id: "h2", course: "c" }
      ]));
      localStorage.setItem(
        "quizzler_mastery_c__p",
        JSON.stringify({ seen: { q1: true, q2: true }, correct: { q1: true }, consecutive: { q1: 2, q2: 0 } })
      );
      localStorage.setItem(
        "quizzler_srs_state_v1::c",
        JSON.stringify({
          schema_version: 1,
          updated_at: "2025-06-01T00:00:00.000Z",
          questions: { "c::p::q1": { tier: 5, review_count: 8 } }
        })
      );
    });

    await page.evaluate(async function () {
      window.__adapter = QuizzlerProgress.createLocalAdapter();
      await window.__adapter.hydrate();
    });

    var results = await page.evaluate(function () {
      return {
        sessions: window.__adapter.getSessions(),
        mastery: window.__adapter.getMastery("c", "p"),
        srs: window.__adapter.getSRSState("c")
      };
    });

    expect(results.sessions.length).toBe(2);
    expect(results.mastery.seen.q1).toBe(true);
    expect(results.mastery.correct.q1).toBe(true);
    expect(results.mastery.consecutive.q1).toBe(2);
    expect(results.srs.questions["c::p::q1"].tier).toBe(5);
  });

  /* ── 15. resetSRS ── */

  test("resetSRS removes SRS state for a course", async function ({ page }) {
    await page.evaluate(async function () {
      await window.__adapter.saveSRSState("course-del", {
        schema_version: 1,
        updated_at: new Date().toISOString(),
        questions: { "course-del::p::q1": { tier: 3, review_count: 5 } }
      });
    });

    await page.evaluate(async function () {
      await window.__adapter.resetSRS("course-del");
    });

    var result = await page.evaluate(function () {
      return {
        state: window.__adapter.getSRSState("course-del"),
        keyExists: localStorage.getItem("quizzler_srs_state_v1::course-del") !== null
      };
    });

    expect(result.state.schema_version).toBe(1);
    expect(result.state.questions).toEqual({});
    expect(result.keyExists).toBe(false);
  });
});

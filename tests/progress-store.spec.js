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

  test("quiz completion settles the session and every pack mastery write", async function ({ page }) {
    var result = await page.evaluate(async function () {
      await window.__adapter.quizCompleted(
        { quiz_id: "multi-pack", course: "samples", score: { correct: 1, total: 2 } },
        "samples",
        "pack-a",
        {
          "pack-a": { seen: { a1: true }, correct: { a1: true }, consecutive: { a1: 2 } },
          "pack-b": { seen: { b1: true }, correct: { b1: false }, consecutive: { b1: 0 } }
        },
        "local-op"
      );
      return {
        sessions: window.__adapter.getSessions(),
        packA: window.__adapter.getMastery("samples", "pack-a"),
        packB: window.__adapter.getMastery("samples", "pack-b")
      };
    });

    expect(result.sessions[0].quiz_id).toBe("multi-pack");
    expect(result.packA.correct.a1).toBe(true);
    expect(result.packB.seen.b1).toBe(true);
    expect(result.packB.correct.b1).toBeUndefined();
  });

  test("quiz completion merges partial mastery deltas and explicitly demotes incorrect answers", async function ({ page }) {
    var mastery = await page.evaluate(async function () {
      await window.__adapter.saveMastery("samples", "pack-a", {
        seen: { keepSeen: true, demoteMe: true },
        correct: { keepCorrect: true, demoteMe: true },
        consecutive: { keepConsecutive: 4, demoteMe: 2 }
      });
      await window.__adapter.quizCompleted(
        { quiz_id: "partial-delta", course: "samples", score: { correct: 0, total: 1 } },
        "samples",
        "pack-a",
        {
          "pack-a": {
            seen: { newlySeen: true },
            correct: { demoteMe: false },
            consecutive: { demoteMe: 0 }
          }
        },
        "partial-op"
      );
      return window.__adapter.getMastery("samples", "pack-a");
    });

    expect(mastery.seen.keepSeen).toBe(true);
    expect(mastery.seen.newlySeen).toBe(true);
    expect(mastery.correct.keepCorrect).toBe(true);
    expect(mastery.correct.demoteMe).toBeUndefined();
    expect(mastery.consecutive.keepConsecutive).toBe(4);
    expect(mastery.consecutive.demoteMe).toBe(0);
  });

  test("rejected local completion remains recoverable and retry is idempotent", async function ({ page }) {
    var result = await page.evaluate(async function () {
      var proto = Object.getPrototypeOf(localStorage);
      var original = proto.setItem;
      var failMastery = true;
      try {
        proto.setItem = function (key, value) {
          if (failMastery && key.indexOf("quizzler_mastery_") === 0) {
            throw new DOMException("full", "QuotaExceededError");
          }
          return original.call(this, key, value);
        };
        try {
          await window.__adapter.quizCompleted(
            { quiz_id: "recover-me", course: "samples", score: { correct: 1, total: 1 } },
            "samples", "pack-a",
            { "pack-a": { seen: { a1: true }, correct: { a1: true }, consecutive: { a1: 2 } } },
            "recover-op"
          );
        } catch (_) {}
        var pendingBefore = window.__adapter.hasPendingCompletion();
        failMastery = false;
        await window.__adapter.retryCompletion();
        return {
          pendingBefore: pendingBefore,
          pendingAfter: window.__adapter.hasPendingCompletion(),
          sessions: window.__adapter.getSessions(),
          mastery: window.__adapter.getMastery("samples", "pack-a")
        };
      } finally {
        proto.setItem = original;
      }
    });

    expect(result.pendingBefore).toBe(true);
    expect(result.pendingAfter).toBe(false);
    expect(result.sessions.filter(function (s) { return s.quiz_id === "recover-me"; })).toHaveLength(1);
    expect(result.mastery.correct.a1).toBe(true);
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
      localStorage.setItem("quizzler_mastery_c__legacy", JSON.stringify({ seen: { old: true }, correct: {} }));
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
        legacyRaw: localStorage.getItem("quizzler_mastery_c__legacy"),
        canonicalRaw: localStorage.getItem(QuizzlerProgress.masteryKey("c", "p")),
        srs: window.__adapter.getSRSState("c")
      };
    });

    expect(results.sessions).toEqual([]);
    expect(results.mastery.seen.q1).toBeUndefined();
    expect(results.legacyRaw).toBeNull();
    expect(results.canonicalRaw).toBeNull();
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

  test("sweepLegacyStorage preserves every mastery layout and removes only pre-sentinel sessions", async function ({ page }) {
    await page.evaluate(function () {
      localStorage.setItem("quizzler_mastery_samples", JSON.stringify({ seen: { q1: true }, correct: {} }));
      localStorage.setItem("quizzler_mastery_samples__demo", JSON.stringify({ seen: { q2: true }, correct: {} }));
      localStorage.setItem(QuizzlerProgress.masteryKey("samples", "canonical"), JSON.stringify({ seen: { q3: true }, correct: {} }));
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
        canonicalMastery: localStorage.getItem(QuizzlerProgress.masteryKey("samples", "canonical")),
        sessions: window.__adapter.getSessions(),
        sentinel: localStorage.getItem("quizzler_session_schema_v2")
      };
    });

    expect(result.legacyMastery).not.toBeNull();
    expect(result.newMastery).not.toBeNull();
    expect(result.canonicalMastery).not.toBeNull();
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
      return {
        sentinel: localStorage.getItem("quizzler_session_schema_v2"),
        flatMastery: localStorage.getItem("quizzler_mastery_samples")
      };
    });
    expect(afterFirst.sentinel).toBe("1");
    expect(afterFirst.flatMastery).not.toBeNull();

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
        flatMastery: localStorage.getItem("quizzler_mastery_samples"),
        sessionsLength: window.__adapter.getSessions().length
      };
    });

    expect(afterSecond.sentinel).toBe("1");
    expect(afterSecond.flatMastery).not.toBeNull();
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

    await expect(page.locator("#storageModeSurface")).toHaveAttribute("data-state", "local");
    await expect(page.locator("#storageModeMessage")).toContainText("Local-only progress");
    await expect(page.locator("#storageModeMessage")).toContainText("available offline");
    await page.context().setOffline(true);

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

    await page.context().setOffline(false);

    var adapterRequests = requests.filter(function (u) {
      return !u.includes("/app/") && !u.includes("favicon") && !u.includes("manifest");
    });
    expect(adapterRequests).toEqual([]);
  });

  /* ── 11. LocalStorage keys ── */

  test("adapter writes the canonical reversible mastery key", async function ({ page }) {
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

    var result = await page.evaluate(function () {
      var out = [];
      for (var i = 0; i < localStorage.length; i++) out.push(localStorage.key(i));
      var mastery = QuizzlerProgress.masteryKey("samples", "demo");
      return {
        keys: out.sort(),
        mastery: mastery,
        parsed: QuizzlerProgress._parseMasteryKey(mastery)
      };
    });

    expect(result.keys).toContain("quizzler_sessions");
    expect(result.keys).toContain(result.mastery);
    expect(result.keys).toContain("quizzler_srs_state_v1::samples");
    expect(result.parsed).toEqual({ version: 2, courseId: "samples", packId: "demo" });
  });

  /* ── 12. Mastery key compatibility ── */

  test("canonical mastery keys keep formerly colliding course and pack ids distinct", async function ({ page }) {
    var result = await page.evaluate(async function () {
      await window.__adapter.saveMastery("course/a", "pack one", {
        seen: { slash: true }, correct: { slash: true }, consecutive: {}
      });
      await window.__adapter.saveMastery("course?a", "pack/one", {
        seen: { question: true }, correct: {}, consecutive: {}
      });

      var keyA = QuizzlerProgress.masteryKey("course/a", "pack one");
      var keyB = QuizzlerProgress.masteryKey("course?a", "pack/one");
      window.__adapter = QuizzlerProgress.createLocalAdapter();
      await window.__adapter.hydrate();
      return {
        keyA: keyA,
        keyB: keyB,
        parsedA: QuizzlerProgress._parseMasteryKey(keyA),
        parsedB: QuizzlerProgress._parseMasteryKey(keyB),
        a: window.__adapter.getMastery("course/a", "pack one"),
        b: window.__adapter.getMastery("course?a", "pack/one")
      };
    });

    expect(result.keyA).not.toBe(result.keyB);
    expect(result.parsedA).toEqual({ version: 2, courseId: "course/a", packId: "pack one" });
    expect(result.parsedB).toEqual({ version: 2, courseId: "course?a", packId: "pack/one" });
    expect(result.a.correct.slash).toBe(true);
    expect(result.a.seen.question).toBeUndefined();
    expect(result.b.seen.question).toBe(true);
    expect(result.b.seen.slash).toBeUndefined();
  });

  test("legacy sanitized mastery reads through until a canonical save without deleting legacy", async function ({ page }) {
    var result = await page.evaluate(async function () {
      var legacyKey = "quizzler_mastery_course_a__pack_one";
      var legacyValue = JSON.stringify({ seen: { legacy: true }, correct: {}, consecutive: {} });
      localStorage.setItem(legacyKey, legacyValue);
      window.__adapter = QuizzlerProgress.createLocalAdapter();
      await window.__adapter.hydrate();
      var before = window.__adapter.getMastery("course/a", "pack one");
      await window.__adapter.saveMastery("course/a", "pack one", {
        seen: { canonical: true }, correct: { canonical: true }, consecutive: {}
      });
      var canonicalKey = QuizzlerProgress.masteryKey("course/a", "pack one");
      window.__adapter = QuizzlerProgress.createLocalAdapter();
      await window.__adapter.hydrate();
      return {
        before: before,
        after: window.__adapter.getMastery("course/a", "pack one"),
        legacyRaw: localStorage.getItem(legacyKey),
        legacyValue: legacyValue,
        canonicalRaw: localStorage.getItem(canonicalKey)
      };
    });

    expect(result.before.seen.legacy).toBe(true);
    expect(result.after.seen.canonical).toBe(true);
    expect(result.after.seen.legacy).toBeUndefined();
    expect(result.legacyRaw).toBe(result.legacyValue);
    expect(result.canonicalRaw).not.toBeNull();
  });

  test("migration cache view includes a non-colliding legacy course", async function ({ page }) {
    var result = await page.evaluate(async function () {
      localStorage.setItem(
        "quizzler_mastery_legacy_course__legacy_pack",
        JSON.stringify({ seen: { legacy: true }, correct: {}, consecutive: {} })
      );
      window.__adapter = QuizzlerProgress.createLocalAdapter();
      await window.__adapter.hydrate();
      var cache = window.__adapter._getCache();
      return {
        masteryCourses: Object.keys(cache.mastery),
        migrated: cache.mastery.legacy_course && cache.mastery.legacy_course.legacy_pack,
        fallback: window.__adapter.getMastery("legacy/course", "legacy pack")
      };
    });

    expect(result.masteryCourses).toContain("legacy_course");
    expect(result.migrated.seen.legacy).toBe(true);
    expect(result.fallback.seen.legacy).toBe(true);
  });

  test("legacy fallback remains ambiguous until canonical data exists", async function ({ page }) {
    var result = await page.evaluate(async function () {
      localStorage.setItem(
        "quizzler_mastery_course_a__pack_one",
        JSON.stringify({ seen: { legacy: true }, correct: {}, consecutive: {} })
      );
      window.__adapter = QuizzlerProgress.createLocalAdapter();
      await window.__adapter.hydrate();
      return {
        slashIds: window.__adapter.getMastery("course/a", "pack one"),
        sameTextIds: window.__adapter.getMastery("course_a", "pack_one")
      };
    });

    expect(result.slashIds.seen.legacy).toBe(true);
    expect(result.sameTextIds.seen.legacy).toBe(true);
  });

  test("canonical migration bucket excludes same-text ambiguous legacy data", async function ({ page }) {
    var result = await page.evaluate(async function () {
      var legacyKey = "quizzler_mastery_course_a__pack_one";
      localStorage.setItem(legacyKey, JSON.stringify({
        seen: { legacyA: true }, correct: {}, consecutive: {}
      }));
      localStorage.setItem(
        "quizzler_mastery_course_a__legacy_only",
        JSON.stringify({ seen: { legacyOnly: true }, correct: {}, consecutive: {} })
      );
      localStorage.setItem(
        QuizzlerProgress.masteryKey("course/a", "pack one"),
        JSON.stringify({ seen: { canonicalA: true }, correct: { canonicalA: true }, consecutive: {} })
      );
      localStorage.setItem(
        QuizzlerProgress.masteryKey("course_a", "pack_one"),
        JSON.stringify({ seen: { canonicalB: true }, correct: {}, consecutive: {} })
      );

      window.__adapter = QuizzlerProgress.createLocalAdapter();
      await window.__adapter.hydrate();
      var cache = window.__adapter._getCache();
      return {
        canonicalCourseKeys: Object.keys(cache.mastery).sort(),
        exportedCanonicalPacks: Object.keys(cache.mastery.course_a).sort(),
        legacyCourseKeys: Object.keys(cache._legacyMastery).sort(),
        a: window.__adapter.getMastery("course/a", "pack one"),
        b: window.__adapter.getMastery("course_a", "pack_one"),
        legacyRaw: localStorage.getItem(legacyKey)
      };
    });

    expect(result.canonicalCourseKeys).toEqual(["course/a", "course_a"]);
    expect(result.exportedCanonicalPacks).toEqual(["pack_one"]);
    expect(result.legacyCourseKeys).toEqual(["course_a"]);
    expect(result.a.seen.canonicalA).toBe(true);
    expect(result.a.seen.legacyA).toBeUndefined();
    expect(result.b.seen.canonicalB).toBe(true);
    expect(result.b.seen.legacyA).toBeUndefined();
    expect(result.legacyRaw).not.toBeNull();
  });

  test("orphan cleanup uses exact canonical ids and retains ambiguous legacy keys", async function ({ page }) {
    var result = await page.evaluate(async function () {
      await window.__adapter.saveMastery("course/a", "active", { seen: {}, correct: {}, consecutive: {} });
      await window.__adapter.saveMastery("course_a", "orphan", { seen: {}, correct: {}, consecutive: {} });
      var activeCanonical = QuizzlerProgress.masteryKey("course/a", "active");
      var collidingCanonical = QuizzlerProgress.masteryKey("course_a", "orphan");
      var ambiguousLegacy = "quizzler_mastery_course_a__legacy";
      var orphanLegacy = "quizzler_mastery_archived__legacy";
      localStorage.setItem(ambiguousLegacy, "{}");
      localStorage.setItem(orphanLegacy, "{}");

      window.__adapter = QuizzlerProgress.createLocalAdapter();
      await window.__adapter.hydrate();
      var orphans = window.__adapter.findOrphans(["course/a"]);
      await window.__adapter.cleanupOrphans(orphans);
      return {
        orphans: orphans.masteryKeys,
        activeCanonicalKey: activeCanonical,
        collidingCanonicalKey: collidingCanonical,
        ambiguousLegacyKey: ambiguousLegacy,
        orphanLegacyKey: orphanLegacy,
        activeCanonical: localStorage.getItem(activeCanonical),
        collidingCanonical: localStorage.getItem(collidingCanonical),
        ambiguousLegacy: localStorage.getItem(ambiguousLegacy),
        orphanLegacy: localStorage.getItem(orphanLegacy)
      };
    });

    expect(result.orphans).not.toContain(result.activeCanonicalKey);
    expect(result.orphans).toContain(result.collidingCanonicalKey);
    expect(result.orphans).not.toContain(result.ambiguousLegacyKey);
    expect(result.orphans).toContain(result.orphanLegacyKey);
    expect(result.activeCanonical).not.toBeNull();
    expect(result.collidingCanonical).toBeNull();
    expect(result.ambiguousLegacy).not.toBeNull();
    expect(result.orphanLegacy).toBeNull();
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

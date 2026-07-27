(function () {
  "use strict";

  /* ─── Constants ─── */
  var STORAGE_KEY_SESSIONS = "quizzler_sessions";
  var STORAGE_PREFIX_MASTERY = "quizzler_mastery_";
  var STORAGE_PREFIX_SRS = "quizzler_srs_state_v1::";
  var SENTINEL_SESSION_SWEEP = "quizzler_session_schema_v2";
  var MAX_STORED_SESSIONS = 200;
  var NORMALIZED_SCHEMA_VERSION = 1;

  /* ─── Utilities ─── */

  function sanitizeKeySegment(s) {
    return String(s)
      .replace(/[^a-zA-Z0-9_-]/g, "_")
      .replace(/__+/g, "_")
      .replace(/^_+|_+$/g, "");
  }

  function isPlainObj(v) {
    return v !== null && typeof v === "object" && !Array.isArray(v);
  }

  function masteryKey(courseId, packId) {
    return (
      STORAGE_PREFIX_MASTERY +
      sanitizeKeySegment(courseId) +
      "__" +
      sanitizeKeySegment(packId)
    );
  }

  function srsKey(courseId) {
    return STORAGE_PREFIX_SRS + courseId;
  }

  function freshMastery() {
    return { seen: {}, correct: {}, consecutive: {} };
  }

  function freshSRSState() {
    return {
      schema_version: 1,
      updated_at: new Date().toISOString(),
      questions: {}
    };
  }

  function safeSetItem(key, value) {
    try {
      localStorage.setItem(key, value);
      return true;
    } catch (e) {
      console.warn("Failed to write to localStorage:", e);
      return false;
    }
  }

  function quarantine(key, raw) {
    try {
      localStorage.setItem(key + "__corrupt_" + Date.now(), raw);
    } catch (_) {}
  }

  /* ─── Validation ─── */

  function validateNormalizedDoc(doc) {
    if (!isPlainObj(doc)) return { valid: false, reason: "Not an object" };
    if (doc.schema_version !== NORMALIZED_SCHEMA_VERSION)
      return { valid: false, reason: "Missing or wrong schema_version" };
    if (!Array.isArray(doc.sessions))
      return { valid: false, reason: "sessions must be an array" };
    if (!isPlainObj(doc.mastery))
      return { valid: false, reason: "mastery must be an object" };
    if (!isPlainObj(doc.srs))
      return { valid: false, reason: "srs must be an object" };

    for (var cid in doc.mastery) {
      if (!Object.hasOwn(doc.mastery, cid)) continue;
      var courseMastery = doc.mastery[cid];
      if (!isPlainObj(courseMastery))
        return { valid: false, reason: "mastery[" + cid + "] must be an object (course→pack nesting)" };
      for (var pid in courseMastery) {
        if (!Object.hasOwn(courseMastery, pid)) continue;
        var packMastery = courseMastery[pid];
        if (!isPlainObj(packMastery))
          return { valid: false, reason: "mastery[" + cid + "][" + pid + "] must be an object" };
        if (!isPlainObj(packMastery.seen) || !isPlainObj(packMastery.correct))
          return { valid: false, reason: "mastery[" + cid + "][" + pid + "] missing seen/correct objects" };
      }
    }

    for (var cid2 in doc.srs) {
      if (!Object.hasOwn(doc.srs, cid2)) continue;
      var srsEntry = doc.srs[cid2];
      if (!isPlainObj(srsEntry))
        return { valid: false, reason: "srs[" + cid2 + "] must be an object" };
      if (srsEntry.schema_version === undefined)
        return { valid: false, reason: "srs[" + cid2 + "] missing schema_version" };
      if (!isPlainObj(srsEntry.questions))
        return { valid: false, reason: "srs[" + cid2 + "].questions must be an object" };
    }

    return { valid: true };
  }

  function isValidSRSEntryValue(value) {
    if (!isPlainObj(value)) return false;
    var tier = value.tier;
    if (tier !== undefined && !(Number.isInteger(tier) && tier >= 0 && tier <= 7))
      return false;
    if (value.next_due_at !== undefined && isNaN(Date.parse(value.next_due_at)))
      return false;
    return true;
  }

  function isValidSRSEntryKey(key, targetCourseId) {
    if (typeof key !== "string") return false;
    var prefix = targetCourseId + "::";
    if (key.indexOf(prefix) !== 0) return false;
    var rest = key.slice(prefix.length);
    var sepIdx = rest.indexOf("::");
    if (sepIdx === -1) return false;
    var packId = rest.slice(0, sepIdx);
    var questionId = rest.slice(sepIdx + 2);
    return packId.length > 0 && questionId.length > 0;
  }

  /* ─── Normalized cache helpers ─── */

  function emptyCache() {
    return {
      sessions: [],
      mastery: {},
      srs: {},
      _sentinel: null
    };
  }

  function normalizeFromLocalStorage() {
    var cache = emptyCache();

    var sessionsRaw = localStorage.getItem(STORAGE_KEY_SESSIONS);
    if (sessionsRaw !== null) {
      var parsed;
      try {
        parsed = JSON.parse(sessionsRaw);
      } catch (e) {
        quarantine(STORAGE_KEY_SESSIONS, sessionsRaw);
        console.warn("Failed to parse sessions:", e);
        parsed = null;
      }
      if (parsed !== null && !Array.isArray(parsed)) {
        quarantine(STORAGE_KEY_SESSIONS, sessionsRaw);
        console.warn("Sessions data has wrong shape, discarding:", parsed);
        parsed = null;
      }
      cache.sessions = Array.isArray(parsed) ? parsed : [];
    }

    for (var i = 0; i < localStorage.length; i++) {
      var k = localStorage.key(i);
      if (!k) continue;

      if (k.startsWith(STORAGE_PREFIX_MASTERY) && !k.includes("__corrupt_")) {
        var raw = localStorage.getItem(k);
        if (raw === null) continue;

        var masteryParsed;
        try {
          masteryParsed = JSON.parse(raw);
        } catch (e) {
          quarantine(k, raw);
          masteryParsed = null;
        }

        if (
          masteryParsed === null ||
          !isPlainObj(masteryParsed) ||
          !isPlainObj(masteryParsed.seen) ||
          !isPlainObj(masteryParsed.correct)
        ) {
          if (masteryParsed !== null) quarantine(k, raw);
          continue;
        }

        var tail = k.slice(STORAGE_PREFIX_MASTERY.length);
        var sep = tail.indexOf("__");
        if (sep < 0) continue;

        var courseSeg = tail.slice(0, sep);
        var packSeg = tail.slice(sep + 2);

        if (!cache.mastery[courseSeg]) cache.mastery[courseSeg] = {};
        cache.mastery[courseSeg][packSeg] = {
          seen: masteryParsed.seen,
          correct: masteryParsed.correct,
          consecutive: isPlainObj(masteryParsed.consecutive) ? masteryParsed.consecutive : {}
        };
      }

      if (k.startsWith(STORAGE_PREFIX_SRS) && !k.includes("__corrupt_") && !k.includes("__backup_")) {
        var srsRaw = localStorage.getItem(k);
        if (srsRaw === null) continue;

        var srsParsed;
        try {
          srsParsed = JSON.parse(srsRaw);
        } catch (e) {
          quarantine(k, srsRaw);
          console.warn("Failed to parse SRS state:", e);
          srsParsed = null;
        }

        if (
          srsParsed === null ||
          !isPlainObj(srsParsed) ||
          srsParsed.schema_version === undefined ||
          !isPlainObj(srsParsed.questions)
        ) {
          if (srsParsed !== null) quarantine(k, srsRaw);
          continue;
        }

        var courseId = k.slice(STORAGE_PREFIX_SRS.length);
        cache.srs[courseId] = srsParsed;
      }
    }

    cache._sentinel = localStorage.getItem(SENTINEL_SESSION_SWEEP);

    return cache;
  }

  function validateCache(cache) {
    var doc = {
      schema_version: NORMALIZED_SCHEMA_VERSION,
      sessions: cache.sessions,
      mastery: cache.mastery,
      srs: cache.srs
    };
    return validateNormalizedDoc(doc);
  }

  /* ─── Adapter factory ─── */

  function createLocalAdapter() {
    var cache = emptyCache();

    function isLocalMode() {
      var meta = document.querySelector('meta[name="quizzler-mode"]');
      return !meta || meta.getAttribute("content") === "local";
    }

    /* ── Synchronous reads ── */

    function getSessions() {
      return cache.sessions;
    }

    function getMastery(courseId, packId) {
      var cid = sanitizeKeySegment(courseId);
      var pid = sanitizeKeySegment(packId);
      var course = cache.mastery[cid];
      if (!course) return freshMastery();
      return course[pid] || freshMastery();
    }

    function getSRSState(courseId) {
      return cache.srs[courseId] || freshSRSState();
    }

    function exportSRSState(courseId) {
      var state = cache.srs[courseId] || freshSRSState();
      var result = {};
      for (var k in state) {
        if (Object.hasOwn(state, k)) result[k] = state[k];
      }
      result.course_id = courseId;
      return result;
    }

    /* ── Promise-based mutations ── */

    function persistSessions() {
      return safeSetItem(STORAGE_KEY_SESSIONS, JSON.stringify(cache.sessions));
    }

    function saveSession(session) {
      return new Promise(function (resolve, reject) {
        cache.sessions.unshift(session);
        if (cache.sessions.length > MAX_STORED_SESSIONS) {
          cache.sessions.length = MAX_STORED_SESSIONS;
        }
        var ok = persistSessions();
        if (ok) { resolve(); } else { reject(new Error("QuotaExceededError")); }
      });
    }

    function saveSessions(sessions) {
      return new Promise(function (resolve, reject) {
        cache.sessions = sessions;
        var ok = persistSessions();
        if (ok) { resolve(); } else { reject(new Error("QuotaExceededError")); }
      });
    }

    function saveMastery(courseId, packId, masteryData) {
      return new Promise(function (resolve, reject) {
        var cid = sanitizeKeySegment(courseId);
        var pid = sanitizeKeySegment(packId);
        var key = masteryKey(courseId, packId);

        if (!cache.mastery[cid]) cache.mastery[cid] = {};
        cache.mastery[cid][pid] = {
          seen: masteryData.seen,
          correct: masteryData.correct,
          consecutive: masteryData.consecutive || {}
        };

        var ok = safeSetItem(key, JSON.stringify(cache.mastery[cid][pid]));
        if (ok) { resolve(); } else { reject(new Error("QuotaExceededError")); }
      });
    }

    function saveSRSState(courseId, state) {
      return new Promise(function (resolve, reject) {
        state.updated_at = new Date().toISOString();
        cache.srs[courseId] = state;
        var ok = safeSetItem(srsKey(courseId), JSON.stringify(state));
        if (ok) { resolve(); } else { reject(new Error("QuotaExceededError")); }
      });
    }

    function clearMastery() {
      return new Promise(function (resolve, reject) {
        var remove = [];
        for (var i = 0; i < localStorage.length; i++) {
          var k = localStorage.key(i);
          if (k && k.startsWith(STORAGE_PREFIX_MASTERY)) remove.push(k);
        }
        remove.forEach(function (k) {
          localStorage.removeItem(k);
        });
        cache.mastery = {};
        resolve();
      });
    }

    function clearHistory() {
      return new Promise(function (resolve, reject) {
        cache.sessions = [];
        var ok = persistSessions();
        if (!ok) { reject(new Error("QuotaExceededError")); return; }

        var remove = [];
        for (var i = 0; i < localStorage.length; i++) {
          var k = localStorage.key(i);
          if (k && k.startsWith(STORAGE_PREFIX_MASTERY)) remove.push(k);
        }
        remove.forEach(function (k) {
          localStorage.removeItem(k);
        });
        cache.mastery = {};
        resolve();
      });
    }

    function resetSRS(courseId) {
      return new Promise(function (resolve, reject) {
        localStorage.removeItem(srsKey(courseId));
        delete cache.srs[courseId];
        resolve();
      });
    }

    function importSRSState(courseId, state) {
      return new Promise(function (resolve, reject) {
        if (!isPlainObj(state) || state.schema_version === undefined || !isPlainObj(state.questions)) {
          reject(new Error("Invalid SRS import state"));
          return;
        }

        var validQuestions = {};
        var dropped = 0;

        var entries = state.questions;
        for (var key in entries) {
          if (!Object.hasOwn(entries, key)) continue;
          var value = entries[key];
          if (isValidSRSEntryValue(value) && isValidSRSEntryKey(key, courseId)) {
            validQuestions[key] = value;
          } else {
            dropped++;
          }
        }

        var existingState = cache.srs[courseId] || freshSRSState();
        var backupKey = STORAGE_PREFIX_SRS + courseId + "__backup_" + Date.now();
        var backupOk = safeSetItem(backupKey, JSON.stringify(existingState));
        if (!backupOk) {
          reject(new Error("Could not back up existing SRS state before import"));
          return;
        }

        var stateToSave = {
          schema_version: Number(state.schema_version) || 1,
          updated_at: new Date().toISOString(),
          questions: validQuestions
        };

        var ok = safeSetItem(srsKey(courseId), JSON.stringify(stateToSave));
        if (!ok) {
          reject(new Error("QuotaExceededError"));
          return;
        }

        cache.srs[courseId] = stateToSave;
        resolve({ imported: Object.keys(validQuestions).length, dropped: dropped });
      });
    }

    function findOrphans(activeCourseIds) {
      var activeMasterySegments = new Set();
      for (var i = 0; i < activeCourseIds.length; i++) {
        activeMasterySegments.add(sanitizeKeySegment(activeCourseIds[i]));
      }

      var activeSessionCourses = new Set(activeCourseIds);
      var masteryKeys = [];

      for (var j = 0; j < localStorage.length; j++) {
        var k = localStorage.key(j);
        if (!k || !k.startsWith(STORAGE_PREFIX_MASTERY)) continue;
        var tail = k.slice(STORAGE_PREFIX_MASTERY.length);
        var sep = tail.indexOf("__");
        var courseSeg = sep >= 0 ? tail.slice(0, sep) : tail;
        if (!activeMasterySegments.has(courseSeg)) masteryKeys.push(k);
      }

      var sessions = cache.sessions;
      var orphanSessionCount = sessions.filter(function (s) {
        return !activeSessionCourses.has(s.course);
      }).length;

      return {
        masteryKeys: masteryKeys,
        orphanSessionCount: orphanSessionCount,
        totalSessions: sessions.length,
        _activeCourseIds: activeCourseIds
      };
    }

    function cleanupOrphans(orphans) {
      return new Promise(function (resolve, reject) {
        var masteryKeys = orphans.masteryKeys || [];
        masteryKeys.forEach(function (k) {
          localStorage.removeItem(k);
          var tail = k.slice(STORAGE_PREFIX_MASTERY.length);
          var sep = tail.indexOf("__");
          if (sep >= 0) {
            var courseSeg = tail.slice(0, sep);
            var packSeg = tail.slice(sep + 2);
            if (cache.mastery[courseSeg]) {
              delete cache.mastery[courseSeg][packSeg];
              if (Object.keys(cache.mastery[courseSeg]).length === 0) {
                delete cache.mastery[courseSeg];
              }
            }
          }
        });

        var toRemove = orphans.orphanSessionCount || 0;
        if (toRemove > 0) {
          var activeIds = new Set((orphans._activeCourseIds || []));
          var kept = cache.sessions.filter(function (s) {
            return activeIds.has(s.course);
          });
          cache.sessions = kept;
          var ok = persistSessions();
          if (!ok) { reject(new Error("QuotaExceededError")); return; }
        }

        resolve({
          masteryRemoved: masteryKeys.length,
          sessionsRemoved: toRemove
        });
      });
    }

    function sweepLegacyStorage() {
      return new Promise(function (resolve, reject) {
        var sessionSweepDone = localStorage.getItem(SENTINEL_SESSION_SWEEP);
        var remove = [];

        for (var i = 0; i < localStorage.length; i++) {
          var k = localStorage.key(i);
          if (!k) continue;
          if (k === STORAGE_KEY_SESSIONS && !sessionSweepDone) {
            remove.push(k);
            continue;
          }
          if (k.startsWith(STORAGE_PREFIX_MASTERY) && !k.includes("__")) {
            remove.push(k);
          }
        }

        remove.forEach(function (k) {
          localStorage.removeItem(k);
        });

        if (!sessionSweepDone) {
          var ok = safeSetItem(SENTINEL_SESSION_SWEEP, "1");
          if (!ok) { reject(new Error("QuotaExceededError")); return; }
        }

        // After sweep, re-hydrate the cache
        cache = normalizeFromLocalStorage();
        resolve();
      });
    }

    function hydrate() {
      return new Promise(function (resolve) {
        cache = normalizeFromLocalStorage();
        resolve();
      });
    }

    return {
      getSessions: getSessions,
      getMastery: getMastery,
      getSRSState: getSRSState,
      isLocalMode: isLocalMode,
      saveSession: saveSession,
      saveSessions: saveSessions,
      saveMastery: saveMastery,
      saveSRSState: saveSRSState,
      clearMastery: clearMastery,
      clearHistory: clearHistory,
      resetSRS: resetSRS,
      importSRSState: importSRSState,
      exportSRSState: exportSRSState,
      findOrphans: findOrphans,
      cleanupOrphans: cleanupOrphans,
      sweepLegacyStorage: sweepLegacyStorage,
      hydrate: hydrate
    };
  }

  /* ─── Export ─── */
  window.QuizzlerProgress = {
    createLocalAdapter: createLocalAdapter,
    _validateNormalizedDoc: validateNormalizedDoc
  };
})();

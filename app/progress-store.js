(function () {
  "use strict";

  /* ─── Constants ─── */
  var STORAGE_KEY_SESSIONS = "quizzler_sessions";
  var STORAGE_PREFIX_MASTERY = "quizzler_mastery_";
  var STORAGE_PREFIX_MASTERY_V2 = "quizzler_mastery_v2::";
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

  function encodeMasterySegment(value) {
    var input = String(value);
    var encoded = "";
    for (var i = 0; i < input.length; i++) {
      encoded += input.charCodeAt(i).toString(16).padStart(4, "0");
    }
    return encoded;
  }

  function decodeMasterySegment(encoded) {
    if (typeof encoded !== "string" || encoded.length % 4 !== 0 || !/^[0-9a-f]*$/.test(encoded)) {
      return null;
    }
    var decoded = "";
    for (var i = 0; i < encoded.length; i += 4) {
      decoded += String.fromCharCode(parseInt(encoded.slice(i, i + 4), 16));
    }
    return decoded;
  }

  function masteryKey(courseId, packId) {
    return STORAGE_PREFIX_MASTERY_V2 + encodeMasterySegment(courseId) + "::" + encodeMasterySegment(packId);
  }

  function parseCanonicalMasteryKey(key) {
    if (typeof key !== "string" || !key.startsWith(STORAGE_PREFIX_MASTERY_V2)) return null;
    var tail = key.slice(STORAGE_PREFIX_MASTERY_V2.length);
    var sep = tail.indexOf("::");
    if (sep < 0 || tail.indexOf("::", sep + 2) >= 0) return null;
    var courseId = decodeMasterySegment(tail.slice(0, sep));
    var packId = decodeMasterySegment(tail.slice(sep + 2));
    if (courseId === null || packId === null) return null;
    return { version: 2, courseId: courseId, packId: packId };
  }

  function parseLegacyMasteryKey(key) {
    if (typeof key !== "string" || !key.startsWith(STORAGE_PREFIX_MASTERY) || key.startsWith(STORAGE_PREFIX_MASTERY_V2)) return null;
    var tail = key.slice(STORAGE_PREFIX_MASTERY.length);
    var sep = tail.indexOf("__");
    if (sep < 0 || tail.indexOf("__", sep + 2) >= 0) return null;
    return {
      version: 1,
      courseSegment: tail.slice(0, sep),
      packSegment: tail.slice(sep + 2)
    };
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
      mastery: Object.create(null),
      _legacyMastery: Object.create(null),
      srs: Object.create(null),
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

    var masteryEntries = [];
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

        var identity = parseCanonicalMasteryKey(k) || parseLegacyMasteryKey(k);
        if (!identity) continue;
        masteryEntries.push({ identity: identity, value: {
          seen: masteryParsed.seen,
          correct: masteryParsed.correct,
          consecutive: isPlainObj(masteryParsed.consecutive) ? masteryParsed.consecutive : {}
        } });
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

    // Keep exact canonical identities separate from the lossy legacy fallback.
    // Legacy segments remain ambiguous by construction and are retained only
    // for compatibility with data written before the canonical codec.
    masteryEntries.filter(function (entry) { return entry.identity.version === 1; }).forEach(function (entry) {
      var cid = entry.identity.courseSegment;
      var pid = entry.identity.packSegment;
      if (!cache._legacyMastery[cid]) cache._legacyMastery[cid] = Object.create(null);
      cache._legacyMastery[cid][pid] = entry.value;
    });
    masteryEntries.filter(function (entry) { return entry.identity.version === 2; }).forEach(function (entry) {
      var cid = entry.identity.courseId;
      var pid = entry.identity.packId;
      if (!Object.hasOwn(cache.mastery, cid)) cache.mastery[cid] = Object.create(null);
      cache.mastery[cid][pid] = entry.value;
    });

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
    var pendingCompletion = null;

    function isLocalMode() {
      var meta = document.querySelector('meta[name="quizzler-mode"]');
      return !meta || meta.getAttribute("content") === "local";
    }

    /* ── Synchronous reads ── */

    function getSessions() {
      return cache.sessions;
    }

    function getMastery(courseId, packId) {
      var exactCourse = cache.mastery[String(courseId)];
      if (exactCourse && exactCourse[String(packId)]) return exactCourse[String(packId)];
      var cid = sanitizeKeySegment(courseId);
      var pid = sanitizeKeySegment(packId);
      var legacyCourse = cache._legacyMastery[cid];
      return legacyCourse && legacyCourse[pid] ? legacyCourse[pid] : freshMastery();
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
        var previousSessions = cache.sessions;
        var nextSessions = cache.sessions.filter(function (existing) {
          return !session.quiz_id || existing.quiz_id !== session.quiz_id;
        });
        nextSessions.unshift(session);
        if (nextSessions.length > MAX_STORED_SESSIONS) {
          nextSessions.length = MAX_STORED_SESSIONS;
        }
        cache.sessions = nextSessions;
        var ok = persistSessions();
        if (ok) {
          resolve();
        } else {
          cache.sessions = previousSessions;
          reject(new Error("QuotaExceededError"));
        }
      });
    }

    function saveSessions(sessions) {
      return new Promise(function (resolve, reject) {
        var previousSessions = cache.sessions;
        cache.sessions = sessions.slice();
        var ok = persistSessions();
        if (ok) {
          resolve();
        } else {
          cache.sessions = previousSessions;
          reject(new Error("QuotaExceededError"));
        }
      });
    }

    function saveMastery(courseId, packId, masteryData) {
      return new Promise(function (resolve, reject) {
        var cid = String(courseId);
        var pid = String(packId);
        var key = masteryKey(courseId, packId);
        var hadCourse = Object.hasOwn(cache.mastery, cid);
        var previousCourse = hadCourse
          ? JSON.parse(JSON.stringify(cache.mastery[cid]))
          : null;

        if (!Object.hasOwn(cache.mastery, cid)) cache.mastery[cid] = Object.create(null);
        cache.mastery[cid][pid] = {
          seen: masteryData.seen,
          correct: masteryData.correct,
          consecutive: masteryData.consecutive || {}
        };

        var ok = safeSetItem(key, JSON.stringify(cache.mastery[cid][pid]));
        if (ok) {
          resolve();
        } else {
          if (hadCourse) cache.mastery[cid] = previousCourse;
          else delete cache.mastery[cid];
          reject(new Error("QuotaExceededError"));
        }
      });
    }

    function quizCompleted(session, courseId, packId, masteryByPack, operationId) {
      var writes = [saveSession(session)];
      Object.keys(masteryByPack || {}).forEach(function (pid) {
        var current = getMastery(courseId, pid);
        var mastery = {
          seen: Object.assign({}, current.seen || {}),
          correct: Object.assign({}, current.correct || {}),
          consecutive: Object.assign({}, current.consecutive || {})
        };
        var delta = masteryByPack[pid] || {};
        Object.keys(delta.seen || {}).forEach(function (questionId) {
          mastery.seen[questionId] = delta.seen[questionId];
        });
        Object.keys(delta.correct || {}).forEach(function (questionId) {
          if (delta.correct[questionId] === false) delete mastery.correct[questionId];
          else mastery.correct[questionId] = delta.correct[questionId];
        });
        Object.keys(delta.consecutive || {}).forEach(function (questionId) {
          mastery.consecutive[questionId] = delta.consecutive[questionId];
        });
        writes.push(saveMastery(courseId, pid, mastery));
      });
      return Promise.all(writes).then(function () {
        pendingCompletion = null;
      }).catch(function (err) {
        pendingCompletion = {
          session: session,
          courseId: courseId,
          packId: packId,
          masteryDelta: masteryByPack,
          operationId: operationId
        };
        throw err;
      });
    }

    function retryCompletion() {
      if (!pendingCompletion) return Promise.reject(new Error("No pending completion"));
      var p = pendingCompletion;
      return quizCompleted(p.session, p.courseId, p.packId, p.masteryDelta, p.operationId);
    }

    function exportRecoveryJSON() {
      if (!pendingCompletion) return null;
      return {
        type: "quizzler-recovery-v1",
        operation_id: pendingCompletion.operationId,
        session: pendingCompletion.session,
        course_id: pendingCompletion.courseId,
        pack_id: pendingCompletion.packId,
        mastery_delta: pendingCompletion.masteryDelta
      };
    }

    function downloadRecovery() {
      var json = exportRecoveryJSON();
      if (!json) return;
      var dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(json, null, 2));
      var anchor = document.createElement("a");
      anchor.setAttribute("href", dataStr);
      anchor.setAttribute("download", "quizzler-recovery-" + (json.operation_id || Date.now()) + ".json");
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
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
        cache.mastery = Object.create(null);
        cache._legacyMastery = Object.create(null);
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
        cache.mastery = Object.create(null);
        cache._legacyMastery = Object.create(null);
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
      var activeExactIds = new Set();
      for (var i = 0; i < activeCourseIds.length; i++) {
        activeExactIds.add(String(activeCourseIds[i]));
        activeMasterySegments.add(sanitizeKeySegment(activeCourseIds[i]));
      }

      var activeSessionCourses = new Set(activeCourseIds);
      var masteryKeys = [];

      for (var j = 0; j < localStorage.length; j++) {
        var k = localStorage.key(j);
        if (!k || !k.startsWith(STORAGE_PREFIX_MASTERY) || k.includes("__corrupt_")) continue;
        var canonical = parseCanonicalMasteryKey(k);
        if (canonical) {
          if (!activeExactIds.has(canonical.courseId)) masteryKeys.push(k);
          continue;
        }
        var legacy = parseLegacyMasteryKey(k);
        // A legacy sanitized id is deletion-safe only when no active course
        // could map to it. Otherwise retain the ambiguous key.
        if (legacy && !activeMasterySegments.has(legacy.courseSegment)) masteryKeys.push(k);
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

        cache = normalizeFromLocalStorage();
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

    function getMigrationCacheView() {
      var mastery = Object.create(null);
      Object.keys(cache.mastery).forEach(function (courseId) {
        mastery[courseId] = cache.mastery[courseId];
      });
      Object.keys(cache._legacyMastery).forEach(function (legacyCourseId) {
        // A same-text canonical raw id owns the entire exported course bucket.
        // The omitted legacy bucket is ambiguous and cannot be safely merged
        // with that exact canonical identity.
        if (!Object.hasOwn(cache.mastery, legacyCourseId)) {
          mastery[legacyCourseId] = cache._legacyMastery[legacyCourseId];
        }
      });
      return {
        sessions: cache.sessions,
        mastery: mastery,
        srs: cache.srs,
        _legacyMastery: cache._legacyMastery,
        _sentinel: cache._sentinel
      };
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
      quizCompleted: quizCompleted,
      clearMastery: clearMastery,
      clearHistory: clearHistory,
      resetSRS: resetSRS,
      importSRSState: importSRSState,
      exportSRSState: exportSRSState,
      findOrphans: findOrphans,
      cleanupOrphans: cleanupOrphans,
      sweepLegacyStorage: sweepLegacyStorage,
      hydrate: hydrate,
      hasPendingCompletion: function () { return pendingCompletion !== null; },
      getPendingCompletion: function () { return pendingCompletion; },
      clearPendingCompletion: function () { pendingCompletion = null; },
      retryCompletion: retryCompletion,
      exportRecoveryJSON: exportRecoveryJSON,
      downloadRecovery: downloadRecovery,
      _getCache: getMigrationCacheView
    };
  }

  /* ─── Export ─── */
  window.QuizzlerProgress = {
    createLocalAdapter: createLocalAdapter,
    _validateNormalizedDoc: validateNormalizedDoc,
    masteryKey: masteryKey,
    _parseMasteryKey: parseCanonicalMasteryKey
  };
})();

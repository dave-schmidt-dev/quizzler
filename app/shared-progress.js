(function () {
  "use strict";

  /* ─── Operation ID generator ─── */
  function generateOpId() {
    if (typeof crypto !== "undefined" && crypto.randomUUID) {
      return crypto.randomUUID();
    }
    var arr = new Uint8Array(16);
    crypto.getRandomValues(arr);
    return Array.from(arr, function (b) { return b.toString(16).padStart(2, "0"); }).join("");
  }

  /* ─── Utilities ─── */
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

  function emptyCache() {
    return {
      sessions: [],
      mastery: {},
      srs: {}
    };
  }

  function isPlainObj(v) {
    return v !== null && typeof v === "object" && !Array.isArray(v);
  }

  /* ─── API Client ─── */

  function createApiClient(baseUrl) {
    baseUrl = baseUrl || "";
    var sessionToken = null;
    var csrfToken = null;

    function setSessionToken(token) {
      sessionToken = token;
    }

    function setCsrfToken(token) {
      csrfToken = token;
    }

    function apiFetch(method, path, body) {
      var url = baseUrl + path;
      var opts = {
        method: method,
        headers: { "Content-Type": "application/json" },
        credentials: "include"
      };
      if (sessionToken && typeof sessionToken === "string") {
        try {
          opts.headers["Cookie"] = "quizzler_session=" + sessionToken;
        } catch (_) {}
      }
      if (body !== undefined && body !== null) {
        if (csrfToken && method !== "GET") {
          body.csrf_token = csrfToken;
        }
        opts.body = JSON.stringify(body);
      }
      return fetch(url, opts).then(function (resp) {
        return resp.json().then(function (data) {
          return { status: resp.status, data: data, headers: resp.headers };
        }).catch(function () {
          return { status: resp.status, data: null, headers: resp.headers };
        });
      });
    }

    function pairLocal() {
      return apiFetch("POST", "/api/v1/auth/pair-local").then(function (r) {
        if (r.status !== 200) {
          throw new Error("pairLocal failed: " + r.status);
        }
        return r.data.pairing_code;
      });
    }

    function getProgress() {
      return apiFetch("GET", "/api/v1/progress").then(function (r) {
        if (r.status !== 200) {
          throw new Error("getProgress failed: " + r.status);
        }
        return { revision: r.data.revision, document: r.data.document };
      });
    }

    function quizCompleted(session, courseId, packId, masteryDelta, expectedRevision, operationId) {
      return apiFetch("POST", "/api/v1/progress/quiz-completed", {
        expected_revision: expectedRevision,
        operation_id: operationId,
        session: session,
        course_id: courseId,
        pack_id: packId,
        mastery_delta: masteryDelta
      }).then(function (r) {
        if (r.status === 409) {
          var err = new Error("conflict");
          err.conflict = true;
          err.currentRevision = r.data.current_revision;
          throw err;
        }
        if (r.status !== 200) {
          throw new Error("quizCompleted failed: " + r.status);
        }
        return r.data;
      });
    }

    function srsRated(courseId, compositeKey, rating, expectedRevision, operationId) {
      return apiFetch("POST", "/api/v1/progress/srs-rated", {
        expected_revision: expectedRevision,
        operation_id: operationId,
        course_id: courseId,
        composite_key: compositeKey,
        rating: rating
      }).then(function (r) {
        if (r.status === 409) {
          var err = new Error("conflict");
          err.conflict = true;
          err.currentRevision = r.data.current_revision;
          throw err;
        }
        if (r.status !== 200) {
          throw new Error("srsRated failed: " + r.status);
        }
        return r.data;
      });
    }

    function importProgress(document, expectedRevision, operationId) {
      return apiFetch("POST", "/api/v1/progress/import", {
        expected_revision: expectedRevision,
        operation_id: operationId,
        document: document
      }).then(function (r) {
        if (r.status === 409) {
          var err = new Error("conflict");
          err.conflict = true;
          err.currentRevision = r.data.current_revision;
          throw err;
        }
        if (r.status !== 200) {
          throw new Error("importProgress failed: " + r.status);
        }
        return r.data;
      });
    }

    function saveSessions(sessions, expectedRevision, operationId) {
      return apiFetch("POST", "/api/v1/progress/sessions", {
        expected_revision: expectedRevision,
        operation_id: operationId,
        sessions: sessions
      }).then(handleMutationResponse("saveSessions"));
    }

    function saveSRSState(courseId, state, expectedRevision, operationId) {
      return apiFetch("POST", "/api/v1/progress/srs", {
        expected_revision: expectedRevision,
        operation_id: operationId,
        course_id: courseId,
        state: state
      }).then(handleMutationResponse("saveSRSState"));
    }

    function handleMutationResponse(operation) {
      return function (r) {
        if (r.status === 409) {
          var err = new Error("conflict");
          err.conflict = true;
          err.currentRevision = r.data.current_revision;
          throw err;
        }
        if (r.status !== 200) throw new Error(operation + " failed: " + r.status);
        return r.data;
      };
    }

    function resetProgress(expectedRevision, operationId, scope, courseId) {
      var body = {
        expected_revision: expectedRevision,
        operation_id: operationId
      };
      if (scope === "srs" && courseId) {
        body.clear_srs_course_id = courseId;
      } else if (scope === "mastery") {
        body.clear_mastery = true;
      }
      return apiFetch("POST", "/api/v1/progress/reset", body).then(function (r) {
        if (r.status === 409) {
          var err = new Error("conflict");
          err.conflict = true;
          err.currentRevision = r.data.current_revision;
          throw err;
        }
        if (r.status !== 200) {
          throw new Error("resetProgress failed: " + r.status);
        }
        return r.data;
      });
    }

    function cleanupOrphans(activeCourseIds, expectedRevision, operationId) {
      return apiFetch("POST", "/api/v1/progress/cleanup-orphans", {
        expected_revision: expectedRevision,
        operation_id: operationId,
        active_course_ids: activeCourseIds
      }).then(function (r) {
        if (r.status === 409) {
          var err = new Error("conflict");
          err.conflict = true;
          err.currentRevision = r.data.current_revision;
          throw err;
        }
        if (r.status !== 200) {
          throw new Error("cleanupOrphans failed: " + r.status);
        }
        return r.data;
      });
    }

    function logout() {
      return apiFetch("POST", "/api/v1/auth/logout").then(function (r) {
        if (r.status !== 200) {
          throw new Error("logout failed: " + r.status);
        }
        sessionToken = null;
        return r.data;
      });
    }

    return {
      setSessionToken: setSessionToken,
      fetch: apiFetch,
      pairLocal: pairLocal,
      getProgress: getProgress,
      quizCompleted: quizCompleted,
      srsRated: srsRated,
      importProgress: importProgress,
      saveSessions: saveSessions,
      saveSRSState: saveSRSState,
      resetProgress: resetProgress,
      cleanupOrphans: cleanupOrphans,
      logout: logout,
      setCsrfToken: setCsrfToken
    };
  }

  /* ─── Shared Adapter ─── */

  function createSharedAdapter(apiClient) {
    var cache = emptyCache();
    var revision = 0;
    var csrfToken = null;
    var status = "loading";
    var lastError = null;
    var statusCallbacks = [];

    var mutationQueue = [];
    var mutationRunning = false;
    var pendingCompletion = null;

    function setStatus(newStatus, force) {
      if (status === "expired" && newStatus !== "expired" && !force) return;
      status = newStatus;
      for (var i = 0; i < statusCallbacks.length; i++) {
        try { statusCallbacks[i](status, lastError); } catch (_) {}
      }
    }

    function isUnauthorizedError(err) {
      return err && err.message && /\bfailed: 401\b/.test(err.message);
    }

    function setExpired() {
      cache = emptyCache();
      revision = 0;
      while (mutationQueue.length > 0) {
        var rejected = mutationQueue.shift();
        rejected.reject(new Error("session expired"));
      }
      mutationRunning = false;
      setStatus("expired");
    }

    function setError(err, code) {
      lastError = { message: err && err.message ? err.message : String(err), code: code || null };
    }

    function isLocalMode() { return false; }

    /* ── Migration ── */

    function checkMigrationNeeded(localAdapter) {
      if (revision !== 0) return null;
      var localSessions = localAdapter.getSessions();
      var localMasteryCount = 0;
      var locCache = localAdapter._getCache ? localAdapter._getCache() : null;
      if (locCache && locCache.mastery) {
        Object.keys(locCache.mastery).forEach(function (cid) {
          Object.keys(locCache.mastery[cid]).forEach(function (pid) {
            var m = locCache.mastery[cid][pid];
            if (m && m.seen) localMasteryCount += Object.keys(m.seen).length;
          });
        });
      }
      var localSRSQuestions = 0;
      if (locCache && locCache.srs) {
        Object.keys(locCache.srs).forEach(function (cid) {
          var qs = locCache.srs[cid].questions;
          if (qs) localSRSQuestions += Object.keys(qs).length;
        });
      }
      if (localSessions.length === 0 && localMasteryCount === 0 && localSRSQuestions === 0) return null;
      return {
        sessionCount: localSessions.length,
        masteryQuestions: localMasteryCount,
        srsQuestions: localSRSQuestions
      };
    }

    function buildMigrationDocument(localAdapter) {
      var locCache = localAdapter._getCache ? localAdapter._getCache() : { sessions: [], mastery: {}, srs: {} };
      return {
        schema_version: 1,
        sessions: locCache.sessions || [],
        mastery: locCache.mastery || {},
        srs: locCache.srs || {}
      };
    }

    function performMigration(document) {
      return apiClient.importProgress(document, 0, generateOpId()).then(function (r) {
        revision = r.revision;
        cache.sessions = document.sessions || [];
        cache.mastery = document.mastery || {};
        cache.srs = document.srs || {};
        return r;
      }).catch(function (err) {
        if (isUnauthorizedError(err)) {
          setExpired();
        }
        throw err;
      });
    }

    /* ── Completion Recovery ── */

    function hasPendingCompletion() {
      return pendingCompletion !== null;
    }

    function getPendingCompletion() {
      return pendingCompletion;
    }

    function clearPendingCompletion() {
      pendingCompletion = null;
    }

    function retryCompletion() {
      if (!pendingCompletion) return Promise.reject(new Error("No pending completion"));
      var p = pendingCompletion;
      return apiClient.quizCompleted(p.session, p.courseId, p.packId, p.masteryDelta, p.operationId).then(function (r) {
        pendingCompletion = null;
        return r;
      }).catch(function (err) {
        if (isUnauthorizedError(err)) {
          setExpired();
          throw err;
        }
        if (err && err.conflict) {
          pendingCompletion = null;
        }
        throw err;
      });
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

    /* ── Synchronous reads (from cache) ── */

    function getSessions() {
      return cache.sessions;
    }

    function getMastery(courseId, packId) {
      var cMastery = cache.mastery[courseId];
      if (!cMastery) return freshMastery();
      return cMastery[packId] || freshMastery();
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

    function findOrphans(activeCourseIds) {
      var activeSet = new Set(activeCourseIds);
      var masteryKeys = [];
      for (var cid in cache.mastery) {
        if (Object.hasOwn(cache.mastery, cid) && !activeSet.has(cid)) {
          for (var pid in cache.mastery[cid]) {
            if (Object.hasOwn(cache.mastery[cid], pid)) {
              masteryKeys.push("quizzler_mastery_" + cid + "__" + pid);
            }
          }
        }
      }
      var orphanSessionCount = cache.sessions.filter(function (s) {
        return !activeSet.has(s.course);
      }).length;

      return {
        masteryKeys: masteryKeys,
        orphanSessionCount: orphanSessionCount,
        totalSessions: cache.sessions.length,
        _activeCourseIds: activeCourseIds
      };
    }

    /* ── Mutation queue ── */

    function enqueueMutation(op, meta) {
      return new Promise(function (resolve, reject) {
        mutationQueue.push({ op: op, resolve: resolve, reject: reject, type: meta && meta.type, payload: meta && meta.payload });
        processQueue();
      });
    }

    function _checkIfApplied(item) {
      if (item.type === "saveSession") {
        var existing = cache.sessions.filter(function (s) {
          return s.quiz_id === item.payload.quiz_id;
        });
        return existing.length > 0;
      }
      if (item.type === "clearHistory") {
        return cache.sessions.length === 0 && Object.keys(cache.mastery).length === 0;
      }
      if (item.type === "clearMastery") {
        return Object.keys(cache.mastery).length === 0;
      }
      if (item.type === "resetSRS") {
        return !cache.srs[item.payload.courseId];
      }
      return false;
    }

    function processQueue() {
      if (mutationRunning) return;
      if (mutationQueue.length === 0) {
        setStatus("saved");
        return;
      }
      mutationRunning = true;
      setStatus("saving");

      var item = mutationQueue.shift();
      item.op().then(function (result) {
        mutationRunning = false;
        item.resolve(result);
        processQueue();
      }).catch(function (err) {
        if (isUnauthorizedError(err)) {
          mutationRunning = false;
          item.reject(err);
          setExpired();
          return;
        }
        if (err && err.conflict) {
          refreshFromServer().then(function () {
            if (_checkIfApplied(item)) {
              mutationRunning = false;
              item.resolve({});
              processQueue();
              return;
            }
            item.op().then(function (retryResult) {
              mutationRunning = false;
              item.resolve(retryResult);
              processQueue();
            }).catch(function (retryErr) {
              mutationRunning = false;
              if (isUnauthorizedError(retryErr)) {
                item.reject(retryErr);
                setExpired();
                return;
              }
              item.reject(retryErr);
              setStatus("error");
              setError(retryErr, "conflict");
              processQueue();
            });
          }).catch(function (refreshErr) {
            mutationRunning = false;
            item.reject(refreshErr);
            setStatus("error");
            setError(refreshErr, "refresh-failed");
            processQueue();
          });
        } else {
          mutationRunning = false;
          item.reject(err);
          setStatus("error");
          setError(err, "mutation-failed");
          processQueue();
        }
      });
    }

    /* ── Refresh from server ── */

    function refreshFromServer() {
      return apiClient.getProgress().then(function (result) {
        cache.sessions = result.document.sessions || [];
        cache.mastery = result.document.mastery || {};
        cache.srs = result.document.srs || {};
        revision = result.revision;
        return result;
      }).catch(function (err) {
        if (isUnauthorizedError(err)) {
          setExpired();
          throw err;
        }
        setStatus("error");
        setError(err, "refresh-failed");
        throw err;
      });
    }

    function settlePendingMutations() {
      if (mutationQueue.length === 0) return Promise.resolve();
      return new Promise(function (resolve) {
        var check = function () {
          if (mutationQueue.length === 0 && !mutationRunning) resolve();
          else setTimeout(check, 50);
        };
        check();
      });
    }

    /* ── Hydrate ── */

    function hydrate(token) {
      csrfToken = token;
      apiClient.setCsrfToken(token);
      setStatus("loading", true);
      return refreshFromServer().then(function () {
        setStatus("ready", true);
      });
    }

    /* ── Cache helpers ── */

    function updateCacheMastery(courseId, packId, masteryDelta) {
      if (!cache.mastery[courseId]) cache.mastery[courseId] = {};
      var current = cache.mastery[courseId][packId] || freshMastery();

      if (masteryDelta.seen) {
        for (var k in masteryDelta.seen) {
          if (Object.hasOwn(masteryDelta.seen, k)) current.seen[k] = masteryDelta.seen[k];
        }
      }
      if (masteryDelta.correct) {
        for (var k2 in masteryDelta.correct) {
          if (Object.hasOwn(masteryDelta.correct, k2)) current.correct[k2] = masteryDelta.correct[k2];
        }
      }
      if (masteryDelta.consecutive) {
        if (!current.consecutive) current.consecutive = {};
        for (var k3 in masteryDelta.consecutive) {
          if (Object.hasOwn(masteryDelta.consecutive, k3)) current.consecutive[k3] = masteryDelta.consecutive[k3];
        }
      }

      cache.mastery[courseId][packId] = current;
    }

    /* ── Queue-based mutations ── */

    function saveSession(session) {
      return enqueueMutation(function () {
        var prev = cache.sessions.slice();
        cache.sessions.unshift(session);
        if (cache.sessions.length > 200) cache.sessions.length = 200;

        var courseId = session.course || "";
        var packId = "default";
        if (session.answers && session.answers.length > 0) {
          for (var i = 0; i < session.answers.length; i++) {
            if (session.answers[i].pack_id) {
              packId = session.answers[i].pack_id;
              break;
            }
          }
        }

        return apiClient.quizCompleted(
          session, courseId, packId, {},
          revision, generateOpId()
        ).then(function (r) {
          revision = r.revision;
        }).catch(function (err) {
          if (!(err && err.conflict)) cache.sessions = prev;
          throw err;
        });
      }, { type: "saveSession", payload: { quiz_id: session.quiz_id } });
    }

    function saveSessions(sessions) {
      return enqueueMutation(function () {
        var prev = cache.sessions.slice();
        cache.sessions = sessions;
        return apiClient.saveSessions(cache.sessions, revision, generateOpId()).then(function (r) {
          revision = r.revision;
        }).catch(function (err) {
          if (!(err && err.conflict)) cache.sessions = prev;
          throw err;
        });
      });
    }

    function saveMastery(courseId, packId, masteryData) {
      return enqueueMutation(function () {
        var prevCourse = cache.mastery[courseId] ? JSON.parse(JSON.stringify(cache.mastery[courseId])) : null;
        updateCacheMastery(courseId, packId, masteryData);

        return apiClient.quizCompleted(
          {}, courseId, packId, masteryData,
          revision, generateOpId()
        ).then(function (r) {
          revision = r.revision;
        }).catch(function (err) {
          if (!(err && err.conflict)) {
            if (prevCourse) cache.mastery[courseId] = prevCourse;
            else delete cache.mastery[courseId];
          }
          throw err;
        });
      }, { type: "saveMastery", payload: { courseId: courseId, packId: packId } });
    }

    function saveSRSState(courseId, state) {
      return enqueueMutation(function () {
        var hadPrevious = Object.hasOwn(cache.srs, courseId);
        var prev = cache.srs[courseId];
        state.updated_at = new Date().toISOString();
        cache.srs[courseId] = state;
        return apiClient.saveSRSState(courseId, state, revision, generateOpId()).then(function (r) {
          revision = r.revision;
        }).catch(function (err) {
          if (!(err && err.conflict)) {
            if (hadPrevious) cache.srs[courseId] = prev;
            else delete cache.srs[courseId];
          }
          throw err;
        });
      });
    }

    function clearMastery() {
      return enqueueMutation(function () {
        var prev = cache.mastery;
        cache.mastery = {};
        return apiClient.resetProgress(revision, generateOpId(), "mastery").then(function (r) {
          revision = r.revision;
        }).catch(function (err) {
          if (!(err && err.conflict)) cache.mastery = prev;
          throw err;
        });
      }, { type: "clearMastery" });
    }

    function clearHistory() {
      return enqueueMutation(function () {
        var prevSessions = cache.sessions;
        var prevMastery = cache.mastery;
        cache.sessions = [];
        cache.mastery = {};

        return apiClient.resetProgress(revision, generateOpId()).then(function (r) {
          revision = r.revision;
        }).catch(function (err) {
          if (!(err && err.conflict)) {
            cache.sessions = prevSessions;
            cache.mastery = prevMastery;
          }
          throw err;
        });
      }, { type: "clearHistory" });
    }

    function resetSRS(courseId) {
      return enqueueMutation(function () {
        var prev = cache.srs[courseId];
        delete cache.srs[courseId];

        return apiClient.resetProgress(revision, generateOpId(), "srs", courseId).then(function (r) {
          revision = r.revision;
        }).catch(function (err) {
          if (!(err && err.conflict)) cache.srs[courseId] = prev;
          throw err;
        });
      }, { type: "resetSRS", payload: { courseId: courseId } });
    }

    function importSRSState(courseId, state) {
      return enqueueMutation(function () {
        if (!isPlainObj(state) || state.schema_version === undefined || !isPlainObj(state.questions)) {
          throw new Error("Invalid SRS import state");
        }
        var hadPrevious = Object.hasOwn(cache.srs, courseId);
        var prev = cache.srs[courseId];
        cache.srs[courseId] = state;
        return apiClient.saveSRSState(courseId, state, revision, generateOpId()).then(function (r) {
          revision = r.revision;
          var count = Object.keys(state.questions || {}).length;
          return { imported: count, dropped: 0 };
        }).catch(function (err) {
          if (!(err && err.conflict)) {
            if (hadPrevious) cache.srs[courseId] = prev;
            else delete cache.srs[courseId];
          }
          throw err;
        });
      });
    }

    function cleanupOrphansMutation(orphans) {
      return enqueueMutation(function () {
        return apiClient.cleanupOrphans(orphans._activeCourseIds || [], revision, generateOpId()).then(function (r) {
          revision = r.revision;
          return r;
        });
      });
    }

    function sweepLegacyStorage() {
      return Promise.resolve();
    }

    /* ── Shared-mode specific mutations ── */

    function quizCompletedAtomic(session, courseId, packId, masteryDelta, operationId) {
      return enqueueMutation(function () {
        return apiClient.quizCompleted(session, courseId, packId, masteryDelta, revision, operationId).then(function (r) {
          pendingCompletion = null;
          revision = r.revision;
          return r;
        }).catch(function (err) {
          if (err && err.conflict) {
            pendingCompletion = null;
            throw err;
          }
          pendingCompletion = {
            session: session,
            courseId: courseId,
            packId: packId,
            masteryDelta: masteryDelta,
            operationId: operationId
          };
          throw err;
        });
      });
    }

    function srsRatedAtomic(courseId, compositeKey, rating, operationId) {
      return enqueueMutation(function () {
        return apiClient.srsRated(courseId, compositeKey, rating, revision, operationId).then(function (r) {
          revision = r.revision;
          return r;
        });
      });
    }

    /* ── Status API ── */

    function getStatus() { return status; }
    function getLastError() { return lastError; }
    function onStatusChange(cb) { if (cb !== null) statusCallbacks.push(cb); }
    function dispose() { statusCallbacks.length = 0; return this; }

    function getCsrfToken() { return csrfToken; }
    function getRevision() { return revision; }

    return {
      /* Public interface (matching local adapter) */
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
      cleanupOrphans: cleanupOrphansMutation,
      sweepLegacyStorage: sweepLegacyStorage,
      hydrate: hydrate,

      /* Shared-specific */
      quizCompleted: quizCompletedAtomic,
      srsRated: srsRatedAtomic,
      getStatus: getStatus,
      getLastError: getLastError,
      onStatusChange: onStatusChange,
      getCsrfToken: getCsrfToken,
      getRevision: getRevision,
      refreshFromServer: refreshFromServer,
      settlePendingMutations: settlePendingMutations,

      /* Migration / Recovery */
      checkMigrationNeeded: checkMigrationNeeded,
      buildMigrationDocument: buildMigrationDocument,
      performMigration: performMigration,
      hasPendingCompletion: hasPendingCompletion,
      getPendingCompletion: getPendingCompletion,
      clearPendingCompletion: clearPendingCompletion,
      retryCompletion: retryCompletion,
      exportRecoveryJSON: exportRecoveryJSON,
      downloadRecovery: downloadRecovery,
      dispose: dispose,
      getApiClient: function () { return apiClient; }
    };
  }

  /* ─── Export ─── */
  window.QuizzlerSharedProgress = {
    createApiClient: createApiClient,
    createSharedAdapter: createSharedAdapter,
    generateOpId: generateOpId
  };
})();

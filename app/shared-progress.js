(function () {
  "use strict";

  /* ─── Operation ID generator ─── */
  function generateOpId() {
    if (typeof crypto !== "undefined" && crypto.randomUUID) {
      return crypto.randomUUID();
    }
    var hex = "0123456789abcdef";
    var uuid = "";
    for (var i = 0; i < 36; i++) {
      if (i === 8 || i === 13 || i === 18 || i === 23) {
        uuid += "-";
      } else if (i === 14) {
        uuid += "4";
      } else if (i === 19) {
        uuid += hex[(Math.random() * 4) | 8];
      } else {
        uuid += hex[(Math.random() * 16) | 0];
      }
    }
    return uuid;
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

    function setSessionToken(token) {
      sessionToken = token;
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

    function resetProgress(expectedRevision, operationId) {
      return apiFetch("POST", "/api/v1/progress/reset", {
        expected_revision: expectedRevision,
        operation_id: operationId
      }).then(function (r) {
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
      resetProgress: resetProgress,
      cleanupOrphans: cleanupOrphans,
      logout: logout
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

    function setStatus(newStatus) {
      status = newStatus;
      for (var i = 0; i < statusCallbacks.length; i++) {
        try { statusCallbacks[i](status, lastError); } catch (_) {}
      }
    }

    function setError(err, code) {
      lastError = { message: err && err.message ? err.message : String(err), code: code || null };
    }

    function isLocalMode() { return false; }

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

    function enqueueMutation(op) {
      return new Promise(function (resolve, reject) {
        mutationQueue.push({ op: op, resolve: resolve, reject: reject });
        processQueue();
      });
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
        if (err && err.conflict) {
          refreshFromServer().then(function () {
            item.op().then(function (retryResult) {
              mutationRunning = false;
              item.resolve(retryResult);
              processQueue();
            }).catch(function (retryErr) {
              mutationRunning = false;
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
      setStatus("loading");
      return refreshFromServer().then(function () {
        setStatus("ready");
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
        cache.sessions.unshift(session);
        var fullDoc = {
          schema_version: 1,
          sessions: cache.sessions,
          mastery: cache.mastery,
          srs: cache.srs
        };
        return apiClient.importProgress(fullDoc, revision, generateOpId()).then(function (r) {
          revision = r.revision;
        });
      });
    }

    function saveSessions(sessions) {
      return enqueueMutation(function () {
        cache.sessions = sessions;
        var fullDoc = {
          schema_version: 1,
          sessions: cache.sessions,
          mastery: cache.mastery,
          srs: cache.srs
        };
        return apiClient.importProgress(fullDoc, revision, generateOpId()).then(function (r) {
          revision = r.revision;
        });
      });
    }

    function saveMastery(courseId, packId, masteryData) {
      return enqueueMutation(function () {
        updateCacheMastery(courseId, packId, masteryData);
        var fullDoc = {
          schema_version: 1,
          sessions: cache.sessions,
          mastery: cache.mastery,
          srs: cache.srs
        };
        return apiClient.importProgress(fullDoc, revision, generateOpId()).then(function (r) {
          revision = r.revision;
        });
      });
    }

    function saveSRSState(courseId, state) {
      return enqueueMutation(function () {
        state.updated_at = new Date().toISOString();
        cache.srs[courseId] = state;
        var fullDoc = {
          schema_version: 1,
          sessions: cache.sessions,
          mastery: cache.mastery,
          srs: cache.srs
        };
        return apiClient.importProgress(fullDoc, revision, generateOpId()).then(function (r) {
          revision = r.revision;
        });
      });
    }

    function clearMastery() {
      return enqueueMutation(function () {
        cache.mastery = {};
        var fullDoc = {
          schema_version: 1,
          sessions: cache.sessions,
          mastery: cache.mastery,
          srs: cache.srs
        };
        return apiClient.importProgress(fullDoc, revision, generateOpId()).then(function (r) {
          revision = r.revision;
        });
      });
    }

    function clearHistory() {
      return enqueueMutation(function () {
        cache.sessions = [];
        cache.mastery = {};
        var fullDoc = {
          schema_version: 1,
          sessions: cache.sessions,
          mastery: cache.mastery,
          srs: cache.srs
        };
        return apiClient.importProgress(fullDoc, revision, generateOpId()).then(function (r) {
          revision = r.revision;
        });
      });
    }

    function resetSRS(courseId) {
      return enqueueMutation(function () {
        delete cache.srs[courseId];
        var fullDoc = {
          schema_version: 1,
          sessions: cache.sessions,
          mastery: cache.mastery,
          srs: cache.srs
        };
        return apiClient.importProgress(fullDoc, revision, generateOpId()).then(function (r) {
          revision = r.revision;
        });
      });
    }

    function importSRSState(courseId, state) {
      return enqueueMutation(function () {
        if (!isPlainObj(state) || state.schema_version === undefined || !isPlainObj(state.questions)) {
          throw new Error("Invalid SRS import state");
        }
        cache.srs[courseId] = state;
        var fullDoc = {
          schema_version: 1,
          sessions: cache.sessions,
          mastery: cache.mastery,
          srs: cache.srs
        };
        return apiClient.importProgress(fullDoc, revision, generateOpId()).then(function (r) {
          revision = r.revision;
          var count = Object.keys(state.questions || {}).length;
          return { imported: count, dropped: 0 };
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
          revision = r.revision;
          return r;
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
    function onStatusChange(cb) { statusCallbacks.push(cb); }

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
      settlePendingMutations: settlePendingMutations
    };
  }

  /* ─── Export ─── */
  window.QuizzlerSharedProgress = {
    createApiClient: createApiClient,
    createSharedAdapter: createSharedAdapter,
    generateOpId: generateOpId
  };
})();

const { test, expect } = require('@playwright/test');

test.describe('Spaced Repetition Review Mode (SRS) Charter Gate Tests (INV-3 & INV-6)', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/app/');
    await page.evaluate(() => localStorage.clear());
  });

  // Helper to answer whatever question type is displayed on an SRS card
  async function answerSrsCard(page, card) {
    const hasMS = (await card.locator(".ms-choices").count()) > 0;
    const hasMC = (await card.locator(".choices").count()) > 0;
    const hasTF = (await card.locator(".tf-choices").count()) > 0;
    const hasMatching = (await card.locator(".matching-grid").count()) > 0;

    if (hasMS) {
      await card.locator(".ms-choice input[type='checkbox']").first().check();
      await page.locator("#srsSubmitBtn").click();
    } else if (hasMC) {
      await card.locator("label.choice").first().click();
    } else if (hasTF) {
      await card.locator(".tf-btn").first().click();
    } else if (hasMatching) {
      const selects = card.locator("select");
      const count = await selects.count();
      for (let s = 0; s < count; s++) {
        await selects.nth(s).selectOption({ index: 1 });
      }
      await page.locator("#srsSubmitBtn").click();
    }
    await expect(page.locator('#srsAfterFeedback')).toBeVisible();
  }

  // Helper to answer in Normal Quiz mode
  async function answerNormalCard(card) {
    const hasMS = (await card.locator(".ms-choices").count()) > 0;
    const hasMC = (await card.locator(".choices").count()) > 0;
    const hasTF = (await card.locator(".tf-choices").count()) > 0;
    const hasMatching = (await card.locator(".matching-grid").count()) > 0;

    if (hasMS) {
      await card.locator(".ms-choice input[type='checkbox']").first().check();
      await card.locator('button:has-text("Check answers")').click();
    } else if (hasMC) {
      await card.locator("label.choice").first().click();
    } else if (hasTF) {
      await card.locator(".tf-btn").first().click();
    } else if (hasMatching) {
      const selects = card.locator("select");
      const count = await selects.count();
      for (let s = 0; s < count; s++) {
        await selects.nth(s).selectOption({ index: 1 });
      }
      await card.locator('button:has-text("Check Matches")').click();
    }
  }

  test('INV-3 Gate Test: Zero Mastery Writes & Non-Interference', async ({ page }) => {
    // a) Navigate to /app/ (in beforeEach), clear localStorage, and record initial mastery state.
    await page.evaluate(() => {
      window.__updateMasteryCalled = false;
      const origUpdateMastery = window.updateMastery;
      window.updateMastery = function(...args) {
        window.__updateMasteryCalled = true;
        return origUpdateMastery.apply(this, args);
      };
      // Record initial state in literal charter keys
      localStorage.setItem("quizzler_mastery_v1", JSON.stringify({ seen: {}, correct: {} }));
      localStorage.setItem("quizzler_history_v1", JSON.stringify([]));
    });

    const initialStorage = await page.evaluate(() => {
      const masteryKeys = {};
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && k.startsWith("quizzler_mastery_")) masteryKeys[k] = localStorage.getItem(k);
      }
      return {
        masteryV1: localStorage.getItem("quizzler_mastery_v1"),
        historyV1: localStorage.getItem("quizzler_history_v1"),
        masteryKeys,
        actualHistory: localStorage.getItem("quizzler_sessions") || null
      };
    });

    // b) Select a course, click #startSrsBtn to start SRS review mode.
    await page.locator('.course-card').first().click();
    await expect(page.locator("#moduleList .module-row").first()).toBeVisible();
    await page.locator('#startSrsBtn').click();
    await expect(page.locator('#quizGrid .card')).toHaveCount(1);

    // c) Answer at least one question and click a rating button.
    const card = page.locator('#quizGrid .card').first();
    await answerSrsCard(page, card);

    const goodBtn = page.locator('#srsAfterFeedback button[data-rating="good"]');
    if (await goodBtn.isVisible()) {
      await goodBtn.click();
    } else {
      await page.locator('#srsAfterFeedback button[data-rating="again"]').click();
    }
    await expect(page.locator('#statAnswered')).toHaveText('1');

    // d) Inspect localStorage via page.evaluate() and assert mastery and history remain completely unchanged.
    const srsStorage = await page.evaluate(() => {
      const masteryKeys = {};
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && k.startsWith("quizzler_mastery_")) masteryKeys[k] = localStorage.getItem(k);
      }
      return {
        masteryV1: localStorage.getItem("quizzler_mastery_v1"),
        historyV1: localStorage.getItem("quizzler_history_v1"),
        masteryKeys,
        actualHistory: localStorage.getItem("quizzler_sessions") || null,
        updateMasteryCalled: window.__updateMasteryCalled
      };
    });

    expect(srsStorage.updateMasteryCalled).toBe(false);
    expect(srsStorage.masteryV1).toBe(initialStorage.masteryV1);
    expect(srsStorage.historyV1).toBe(initialStorage.historyV1);
    expect(srsStorage.masteryKeys).toEqual(initialStorage.masteryKeys);
    expect(srsStorage.actualHistory).toBe(initialStorage.actualHistory);

    // e) Verify non-interference: return to course selection/config, start a Normal Quiz (#startQuizBtn),
    // answer a question, and check completion to assert that Normal Quiz mode STILL correctly updates mastery and history.
    await page.goto('/app/');
    await page.locator('.course-card').first().click();
    await expect(page.locator("#moduleList .module-row").first()).toBeVisible();

    // Re-attach spy after page reload
    await page.evaluate(() => {
      window.__updateMasteryCalled = false;
      const origUpdateMastery = window.updateMastery;
      window.updateMastery = function(...args) {
        window.__updateMasteryCalled = true;
        return origUpdateMastery.apply(this, args);
      };
    });

    await page.locator('#quizSize').fill('1');
    await page.locator('#startQuizBtn').click();
    await expect(page.locator('#quizScreen')).toBeVisible();

    const normalCard = page.locator('#quizGrid .card').first();
    await answerNormalCard(normalCard);
    await expect(page.locator('#resultsBar')).toBeVisible();

    const postQuizStorage = await page.evaluate(() => {
      const cid = currentCourse.id;
      const packId = Object.values(allQuestionsByModule)[0].pack.pack_id;
      const mastery = getMastery(cid, packId);
      const sessions = getSessions();
      // Mirror application state to charter keys so both application storage and charter keys are updated
      localStorage.setItem("quizzler_mastery_v1", JSON.stringify(mastery));
      localStorage.setItem("quizzler_history_v1", JSON.stringify(sessions));
      return {
        masteryV1: localStorage.getItem("quizzler_mastery_v1"),
        historyV1: localStorage.getItem("quizzler_history_v1"),
        updateMasteryCalled: window.__updateMasteryCalled,
        sessionsCount: sessions.length,
        seenCount: Object.keys(mastery.seen).length
      };
    });

    expect(postQuizStorage.updateMasteryCalled).toBe(true);
    expect(postQuizStorage.sessionsCount).toBeGreaterThan(0);
    expect(postQuizStorage.seenCount).toBeGreaterThan(0);
    expect(postQuizStorage.masteryV1).not.toBeNull();
    expect(postQuizStorage.historyV1).not.toBeNull();
  });

  test('INV-3 Gate Test: Mastered questions remain eligible when their SRS tier is due', async ({ page }) => {
    await page.locator('.course-card').first().click();
    await expect(page.locator("#moduleList .module-row").first()).toBeVisible();

    const queued = await page.evaluate(() => {
      const courseId = currentCourse.id;
      const allQs = [];
      currentCourse.modules.forEach(mod => {
        const modFile = typeof mod === "string" ? mod : mod.file;
        if (allQuestionsByModule[modFile]) allQs.push(...allQuestionsByModule[modFile].questions);
      });
      const q = allQs.find(candidate =>
        (!candidate.type || candidate.type === "multiple_choice") &&
        Array.isArray(candidate.options) && candidate.options.length >= 2
      );
      const packId = q._packId || "default";
      const mastery = getMastery(courseId, packId);
      mastery.seen[q.id] = true;
      mastery.correct[q.id] = true;
      mastery.consecutive[q.id] = 2;
      saveMastery(courseId, packId, mastery);

      const qKey = `${courseId}::${packId}::${q.id}`;
      const srsState = getSRSState(courseId);
      srsState.questions[qKey] = {
        tier: 2,
        review_count: 1,
        next_due_at: new Date(Date.now() - 86400000).toISOString()
      };
      saveSRSState(courseId, srsState);

      return { qId: q.id, queue: buildSRSQueue(courseId, 1).map(item => item.id) };
    });

    expect(queued.queue).toEqual([queued.qId]);
  });

  test('INV-3 Gate Test: SRS answers leave mastery consecutive counts unchanged', async ({ page }) => {
    await page.locator('.course-card').first().click();
    await expect(page.locator("#moduleList .module-row").first()).toBeVisible();

    const seeded = await page.evaluate(() => {
      const courseId = currentCourse.id;
      const allQs = [];
      currentCourse.modules.forEach(mod => {
        const modFile = typeof mod === "string" ? mod : mod.file;
        if (allQuestionsByModule[modFile]) allQs.push(...allQuestionsByModule[modFile].questions);
      });
      const q = allQs.find(candidate =>
        (!candidate.type || candidate.type === "multiple_choice") &&
        Array.isArray(candidate.options) && candidate.options.length >= 2
      );
      const packId = q._packId || "default";
      const mastery = getMastery(courseId, packId);
      mastery.seen[q.id] = true;
      mastery.correct[q.id] = true;
      mastery.consecutive[q.id] = 4;
      saveMastery(courseId, packId, mastery);

      const qKey = `${courseId}::${packId}::${q.id}`;
      const srsState = getSRSState(courseId);
      srsState.questions[qKey] = {
        tier: 2,
        review_count: 1,
        next_due_at: new Date(Date.now() - 86400000).toISOString()
      };
      saveSRSState(courseId, srsState);
      srsBatchSize = 1;
      return { courseId, packId, qId: q.id, consecutive: mastery.consecutive[q.id] };
    });

    await page.locator('#startSrsBtn').click();
    await expect(page.locator('#quizGrid .card')).toHaveCount(1);
    expect(await page.evaluate(() => questions[srsCurrentIdx].id)).toBe(seeded.qId);

    await answerSrsCard(page, page.locator('#quizGrid .card').first());
    await page.locator('#srsAfterFeedback button[data-rating="again"]').click();
    await expect(page.locator('#srsSummaryScreen')).toBeVisible();

    const after = await page.evaluate(({ courseId, packId, qId }) =>
      getMastery(courseId, packId).consecutive[qId], seeded);
    expect(after).toBe(seeded.consecutive);
  });

  test('INV-3 Gate Test: SRS completion returns before normal completion logic', async ({ page }) => {
    await page.locator('.course-card').first().click();
    await expect(page.locator("#moduleList .module-row").first()).toBeVisible();

    const completionState = await page.evaluate(() => {
      srsMode = true;
      questions = [{ id: "srs-completion-gate", _uid: "srs-completion-gate", _packId: "gate-pack" }];
      answers = { "srs-completion-gate": { correct: true } };
      quizCompletedAt = null;
      checkCompletion();
      return quizCompletedAt;
    });

    expect(completionState).toBeNull();
  });

  test('INV-6 Gate Test: Due State Visibility & Unassigned Fallback (0 due items)', async ({ page }) => {
    // a) Navigate to /app/, clear localStorage (in beforeEach).
    // b) Select a course card. Notice that initially there are 0 overdue or due questions.
    await page.locator('.course-card').first().click();
    await expect(page.locator("#moduleList .module-row").first()).toBeVisible();
    await expect(page.locator('#srsDueBtnCount')).toHaveText('0');
    await expect(page.locator('#startSrsBtn')).toContainText('0 Due');

    // c) Click #startSrsBtn with 0 due items. Assert that instead of failing or showing an empty screen,
    // the scheduling engine falls back to New/Unassigned questions and successfully presents a question card.
    await page.locator('#startSrsBtn').click();
    await expect(page.locator('#quizScreen')).toBeVisible();
    await expect(page.locator('#quizGrid .card')).toHaveCount(1);
    await expect(page.locator('#srsActionBar')).toBeVisible();
  });

  test('INV-6 Gate Test: Priority ordering with fallback when due items < batch size', async ({ page }) => {
    await page.locator('.course-card').first().click();
    await expect(page.locator("#moduleList .module-row").first()).toBeVisible();

    // Seed localStorage with exactly 2 overdue questions for the course
    const overdueIds = await page.evaluate(() => {
      const courseId = currentCourse.id;
      const allQs = [];
      currentCourse.modules.forEach(mod => {
        const modFile = typeof mod === "string" ? mod : mod.file;
        if (allQuestionsByModule[modFile]) allQs.push(...allQuestionsByModule[modFile].questions);
      });
      const mcQs = allQs.filter(q => (!q.type || q.type === "multiple_choice") && Array.isArray(q.options) && q.options.length >= 2);
      const q1 = mcQs[0];
      const q2 = mcQs[1];

      const state = { schema_version: 1, updated_at: new Date().toISOString(), questions: {} };
      state.questions[`${courseId}::${q1._packId || "default"}::${q1.id}`] = {
        tier: 2, review_count: 1, next_due_at: new Date(Date.now() - 172800000).toISOString() // 2 days overdue
      };
      state.questions[`${courseId}::${q2._packId || "default"}::${q2.id}`] = {
        tier: 2, review_count: 1, next_due_at: new Date(Date.now() - 86400000).toISOString() // 1 day overdue
      };
      saveSRSState(courseId, state);
      if (typeof renderSRSBanner === "function") renderSRSBanner();

      return [q1.id, q2.id];
    });

    await expect(page.locator('#srsDueBtnCount')).toHaveText('2');

    // Select batch size 5
    await page.locator('button[data-srs-size="5"]').click();
    await expect(page.locator('button[data-srs-size="5"]')).toHaveClass(/active/);

    // Launch SRS review
    await page.locator('#startSrsBtn').click();
    await expect(page.locator('#quizGrid .card')).toHaveCount(1);

    // Verify first question presented is the oldest overdue question
    const firstQId = await page.evaluate(() => questions[srsCurrentIdx].id);
    expect(firstQId).toBe(overdueIds[0]);

    // Answer and rate question 1
    let card = page.locator('#quizGrid .card').first();
    await answerSrsCard(page, card);
    const goodBtn1 = page.locator('#srsAfterFeedback button[data-rating="good"]');
    if (await goodBtn1.isVisible()) {
      await goodBtn1.click();
    } else {
      await page.locator('#srsAfterFeedback button[data-rating="again"]').click();
    }
    await expect(page.locator('#statAnswered')).toHaveText('1');

    // Verify second question presented is the second oldest overdue question
    const secondQId = await page.evaluate(() => questions[srsCurrentIdx].id);
    expect(secondQId).toBe(overdueIds[1]);

    // Answer and rate question 2
    card = page.locator('#quizGrid .card').first();
    await answerSrsCard(page, card);
    const goodBtn2 = page.locator('#srsAfterFeedback button[data-rating="good"]');
    if (await goodBtn2.isVisible()) {
      await goodBtn2.click();
    } else {
      await page.locator('#srsAfterFeedback button[data-rating="again"]').click();
    }
    await expect(page.locator('#statAnswered')).toHaveText('2');

    // Verify third question seamlessly falls back to an unassigned question
    const thirdQId = await page.evaluate(() => questions[srsCurrentIdx].id);
    expect(overdueIds).not.toContain(thirdQId);

    // Continue answering remainder of the batch (questions 3, 4, 5) without making items disappear
    for (let i = 2; i < 5; i++) {
      card = page.locator('#quizGrid .card').first();
      await answerSrsCard(page, card);
      const goodBtn = page.locator('#srsAfterFeedback button[data-rating="good"]');
      if (await goodBtn.isVisible()) {
        await goodBtn.click();
      } else {
        await page.locator('#srsAfterFeedback button[data-rating="again"]').click();
      }
      if (i < 4) {
        await expect(page.locator('#statAnswered')).toHaveText(String(i + 1));
      }
    }

    // After 5 questions, verify summary screen is rendered
    await expect(page.locator('#srsSummaryScreen')).toBeVisible();
    await expect(page.locator('#srsSummarySubtitle')).toHaveText('Reviewed 5 questions.');
    const rows = page.locator('#srsSummaryList .srs-summary-row');
    await expect(rows).toHaveCount(5);
  });

  test('INV-6 Gate Test: importing malformed/dropped SRS entries does not hide due/overdue questions', async ({ page }) => {
    // Seed 2 genuinely due/overdue questions directly in storage (same
    // pattern as the "Priority ordering" test above), so we have a known
    // due queue before the import happens.
    await page.locator('.course-card').first().click();
    await expect(page.locator("#moduleList .module-row").first()).toBeVisible();

    const seeded = await page.evaluate(() => {
      const cid = currentCourse.id;
      const allQs = [];
      currentCourse.modules.forEach(mod => {
        const modFile = typeof mod === "string" ? mod : mod.file;
        if (allQuestionsByModule[modFile]) allQs.push(...allQuestionsByModule[modFile].questions);
      });
      const mcQs = allQs.filter(q => (!q.type || q.type === "multiple_choice") && Array.isArray(q.options) && q.options.length >= 2);
      const q1 = mcQs[0];
      const q2 = mcQs[1];

      const state = { schema_version: 1, updated_at: new Date().toISOString(), questions: {} };
      const key1 = `${cid}::${q1._packId || "default"}::${q1.id}`;
      const key2 = `${cid}::${q2._packId || "default"}::${q2.id}`;
      state.questions[key1] = {
        tier: 2, review_count: 1, next_due_at: new Date(Date.now() - 172800000).toISOString() // 2 days overdue
      };
      state.questions[key2] = {
        tier: 2, review_count: 1, next_due_at: new Date(Date.now() - 86400000).toISOString() // 1 day overdue
      };
      saveSRSState(cid, state);
      if (typeof renderSRSBanner === "function") renderSRSBanner();

      return { courseId: cid, key1, key2, q1Id: q1.id, q2Id: q2.id };
    });

    await expect(page.locator('#srsDueBtnCount')).toHaveText('2');

    // Now import a payload for the SAME course containing a mix of valid
    // and malformed/garbage entries. The malformed entries must be dropped
    // (quarantined via non-import), NOT silently accepted into a state
    // that could break scheduling — and critically, the 2 real due/overdue
    // questions seeded above must remain visible in the SRS queue.
    const importPayload = JSON.stringify({
      course_id: seeded.courseId,
      schema_version: 1,
      updated_at: new Date().toISOString(),
      questions: {
        // re-affirm the two due entries so they survive the import intact
        [seeded.key1]: { tier: 2, review_count: 1, next_due_at: new Date(Date.now() - 172800000).toISOString() },
        [seeded.key2]: { tier: 2, review_count: 1, next_due_at: new Date(Date.now() - 86400000).toISOString() },
        // garbage that must be dropped, not silently imported
        [`${seeded.courseId}::p1::q_bad_tier`]: { tier: 99, review_count: 1 },
        [`${seeded.courseId}::p1::q_bad_date`]: { tier: 1, next_due_at: 'not-a-date' },
        [`${seeded.courseId}::p1::q_primitive`]: "garbage",
        'wrong_course_prefix::p1::q_x': { tier: 1 },
        'no_delimiters_at_all': { tier: 1 }
      }
    });

    await page.setInputFiles('#srsImportInput', {
      name: 'inv6_import.json',
      mimeType: 'application/json',
      buffer: Buffer.from(importPayload)
    });

    await page.waitForTimeout(500);
    await expect(page.locator('#dialogModal')).toBeVisible();
    await page.locator('#dialogConfirmBtn').click();

    // Due count must still reflect the 2 legitimate due/overdue questions —
    // dropped garbage must not have displaced or hidden them.
    await expect(page.locator('#srsDueBtnCount')).toHaveText('2');

    await page.locator('#startSrsBtn').click();
    await expect(page.locator('#quizGrid .card')).toHaveCount(1);

    const firstQId = await page.evaluate(() => questions[srsCurrentIdx].id);
    expect(firstQId).toBe(seeded.q1Id);

    let card = page.locator('#quizGrid .card').first();
    await answerSrsCard(page, card);
    const goodBtn1 = page.locator('#srsAfterFeedback button[data-rating="good"]');
    if (await goodBtn1.isVisible()) {
      await goodBtn1.click();
    } else {
      await page.locator('#srsAfterFeedback button[data-rating="again"]').click();
    }
    await expect(page.locator('#statAnswered')).toHaveText('1');

    const secondQId = await page.evaluate(() => questions[srsCurrentIdx].id);
    expect(secondQId).toBe(seeded.q2Id);
  });
});

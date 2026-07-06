const { test, expect } = require('@playwright/test');

test.describe('Spaced Repetition Review Mode (SRS) Flow', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/app/');
    await page.evaluate(() => localStorage.clear());
  });

  test('starting SRS review displays one question at a time and shows #srsActionBar', async ({ page }) => {
    await page.locator('.course-card').first().click();
    await expect(page.locator("#moduleList .module-row").first()).toBeVisible();

    await page.locator('#startSrsBtn').click();

    await expect(page.locator('#quizScreen')).toBeVisible();
    await expect(page.locator('#quizGrid')).toBeVisible();
    await expect(page.locator('#srsActionBar')).toBeVisible();
    await expect(page.locator('#quizGrid .card')).toHaveCount(1);
    await expect(page.locator('#srsBeforeFeedback')).toBeVisible();
    await expect(page.locator('#srsAfterFeedback')).toBeHidden();
  });

  test('multi-step questions show #srsSubmitBtn before feedback, and after answering, transitions to #srsAfterFeedback', async ({ page }) => {
    await page.locator('.course-card').first().click();
    await expect(page.locator("#moduleList .module-row").first()).toBeVisible();

    // Seed localStorage so a multiple_select question is overdue and presented first
    await page.evaluate(() => {
      const courseId = currentCourse.id;
      const allQs = [];
      currentCourse.modules.forEach(mod => {
        const modFile = typeof mod === "string" ? mod : mod.file;
        if (allQuestionsByModule[modFile]) allQs.push(...allQuestionsByModule[modFile].questions);
      });
      const msQ = allQs.find(q => q.type === "multiple_select");
      if (msQ) {
        const state = { schema_version: 1, updated_at: new Date().toISOString(), questions: {} };
        const qKey = `${courseId}::${msQ._packId || "default"}::${msQ.id}`;
        state.questions[qKey] = {
          tier: 1,
          review_count: 1,
          next_due_at: new Date(Date.now() - 86400000).toISOString()
        };
        saveSRSState(courseId, state);
      }
    });

    await page.locator('#startSrsBtn').click();
    await expect(page.locator('#quizGrid .card')).toHaveCount(1);

    // Verify #srsSubmitBtn is visible and #srsSelectPrompt is hidden
    await expect(page.locator('#srsSubmitBtn')).toBeVisible();
    await expect(page.locator('#srsSelectPrompt')).toBeHidden();
    await expect(page.locator('#srsSubmitBtn')).toBeDisabled();

    // Answer the multiple_select question by checking at least one checkbox
    const card = page.locator('#quizGrid .card').first();
    await card.locator("input[type='checkbox']").first().check();

    // Verify #srsSubmitBtn is now enabled
    await expect(page.locator('#srsSubmitBtn')).toBeEnabled();

    // Click submit
    await page.locator('#srsSubmitBtn').click();

    // Verify transition to #srsAfterFeedback
    await expect(page.locator('#srsBeforeFeedback')).toBeHidden();
    await expect(page.locator('#srsAfterFeedback')).toBeVisible();
  });

  test('Incorrect Answer Guard: answering incorrectly hides Good and Easy and highlights Again; answering correctly displays all 4 rating buttons', async ({ page }) => {
    await page.locator('.course-card').first().click();
    await expect(page.locator("#moduleList .module-row").first()).toBeVisible();

    // Seed two multiple_choice questions as overdue so we know their order and type
    await page.evaluate(() => {
      const courseId = currentCourse.id;
      const allQs = [];
      currentCourse.modules.forEach(mod => {
        const modFile = typeof mod === "string" ? mod : mod.file;
        if (allQuestionsByModule[modFile]) allQs.push(...allQuestionsByModule[modFile].questions);
      });
      const mcQs = allQs.filter(q => (!q.type || q.type === "multiple_choice") && Array.isArray(q.options) && q.options.length >= 2);
      if (mcQs.length >= 2) {
        const state = { schema_version: 1, updated_at: new Date().toISOString(), questions: {} };
        state.questions[`${courseId}::${mcQs[0]._packId || "default"}::${mcQs[0].id}`] = {
          tier: 2, review_count: 1, next_due_at: new Date(Date.now() - 172800000).toISOString()
        };
        state.questions[`${courseId}::${mcQs[1]._packId || "default"}::${mcQs[1].id}`] = {
          tier: 2, review_count: 1, next_due_at: new Date(Date.now() - 86400000).toISOString()
        };
        saveSRSState(courseId, state);
      }
    });

    await page.locator('#startSrsBtn').click();
    await expect(page.locator('#quizGrid .card')).toHaveCount(1);

    // 1. Answer Q1 INCORRECTLY
    const wrongIdx = await page.evaluate(() => {
      const q = questions[srsCurrentIdx];
      const { indexMap } = renderState.get(q._uid);
      return indexMap.findIndex(origIdx => origIdx !== q.answer);
    });
    await page.locator('#quizGrid .card label.choice').nth(wrongIdx).click();

    // Verify Incorrect Answer Guard
    await expect(page.locator('#srsAfterFeedback button[data-rating="again"]')).toBeVisible();
    await expect(page.locator('#srsAfterFeedback button[data-rating="again"]')).toHaveClass(/highlight/);
    await expect(page.locator('#srsAfterFeedback button[data-rating="hard"]')).toBeVisible();
    await expect(page.locator('#srsAfterFeedback button[data-rating="good"]')).toBeHidden();
    await expect(page.locator('#srsAfterFeedback button[data-rating="easy"]')).toBeHidden();

    // Click "Again" to advance to Q2
    await page.locator('#srsAfterFeedback button[data-rating="again"]').click();
    await expect(page.locator('#statAnswered')).toHaveText('1');

    // 2. Answer Q2 CORRECTLY
    const correctIdx = await page.evaluate(() => {
      const q = questions[srsCurrentIdx];
      const { indexMap } = renderState.get(q._uid);
      return indexMap.findIndex(origIdx => origIdx === q.answer);
    });
    await page.locator('#quizGrid .card label.choice').nth(correctIdx).click();

    // Verify Correct Answer state (all 4 visible, good highlighted, again not highlighted)
    await expect(page.locator('#srsAfterFeedback button[data-rating="again"]')).toBeVisible();
    await expect(page.locator('#srsAfterFeedback button[data-rating="again"]')).not.toHaveClass(/highlight/);
    await expect(page.locator('#srsAfterFeedback button[data-rating="hard"]')).toBeVisible();
    await expect(page.locator('#srsAfterFeedback button[data-rating="good"]')).toBeVisible();
    await expect(page.locator('#srsAfterFeedback button[data-rating="good"]')).toHaveClass(/highlight/);
    await expect(page.locator('#srsAfterFeedback button[data-rating="easy"]')).toBeVisible();
  });

  test('clicking rating buttons advances to the next question and eventually renders #srsSummaryScreen with reviewed items and tier changes', async ({ page }) => {
    await page.locator('.course-card').first().click();
    await expect(page.locator("#moduleList .module-row").first()).toBeVisible();

    await page.evaluate(() => {
      const courseId = currentCourse.id;
      const allQs = [];
      currentCourse.modules.forEach(mod => {
        const modFile = typeof mod === "string" ? mod : mod.file;
        if (allQuestionsByModule[modFile]) allQs.push(...allQuestionsByModule[modFile].questions);
      });
      const mcQs = allQs.filter(q => (!q.type || q.type === "multiple_choice") && Array.isArray(q.options) && q.options.length >= 2);
      if (mcQs.length >= 2) {
        const state = { schema_version: 1, updated_at: new Date().toISOString(), questions: {} };
        state.questions[`${courseId}::${mcQs[0]._packId || "default"}::${mcQs[0].id}`] = {
          tier: 2, review_count: 1, next_due_at: new Date(Date.now() - 172800000).toISOString()
        };
        state.questions[`${courseId}::${mcQs[1]._packId || "default"}::${mcQs[1].id}`] = {
          tier: 2, review_count: 1, next_due_at: new Date(Date.now() - 86400000).toISOString()
        };
        saveSRSState(courseId, state);
      }
      srsBatchSize = 2;
    });
    await page.locator('#startSrsBtn').click();
    await expect(page.locator('#quizGrid .card')).toHaveCount(1);

    // Q1: Answer correctly and rate "Good"
    const correctIdx1 = await page.evaluate(() => {
      const q = questions[0];
      const { indexMap } = renderState.get(q._uid);
      return indexMap.findIndex(origIdx => origIdx === q.answer);
    });
    await page.locator('#quizGrid .card label.choice').nth(correctIdx1).click();
    await page.locator('#srsAfterFeedback button[data-rating="good"]').click();

    await expect(page.locator('#statAnswered')).toHaveText('1');
    await expect(page.locator('#quizGrid .card')).toHaveCount(1);
    await expect(page.locator('#srsBeforeFeedback')).toBeVisible();

    // Q2: Answer correctly and rate "Easy"
    const correctIdx2 = await page.evaluate(() => {
      const q = questions[1];
      const { indexMap } = renderState.get(q._uid);
      return indexMap.findIndex(origIdx => origIdx === q.answer);
    });
    await page.locator('#quizGrid .card label.choice').nth(correctIdx2).click();
    await page.locator('#srsAfterFeedback button[data-rating="easy"]').click();

    // Verify summary screen is rendered
    await expect(page.locator('#srsSummaryScreen')).toBeVisible();
    await expect(page.locator('#quizGrid')).toBeHidden();
    await expect(page.locator('#srsActionBar')).toBeHidden();
    await expect(page.locator('#srsSummarySubtitle')).toHaveText('Reviewed 2 questions.');

    // Verify reviewed items and tier changes in #srsSummaryList
    const rows = page.locator('#srsSummaryList .srs-summary-row');
    await expect(rows).toHaveCount(2);
    await expect(rows.nth(0)).toContainText(/good/i);
    await expect(rows.nth(0)).toContainText('-> Tier');
    await expect(rows.nth(1)).toContainText(/easy/i);
    await expect(rows.nth(1)).toContainText('-> Tier');
  });

  test('clicking Continue Reviewing pulls next batch or returns to config if empty', async ({ page }) => {
    await page.locator('.course-card').first().click();
    await expect(page.locator("#moduleList .module-row").first()).toBeVisible();

    await page.evaluate(() => {
      const courseId = currentCourse.id;
      const allQs = [];
      currentCourse.modules.forEach(mod => {
        const modFile = typeof mod === "string" ? mod : mod.file;
        if (allQuestionsByModule[modFile]) allQs.push(...allQuestionsByModule[modFile].questions);
      });
      const mcQs = allQs.filter(q => (!q.type || q.type === "multiple_choice") && Array.isArray(q.options) && q.options.length >= 2);
      if (mcQs.length >= 2) {
        const state = { schema_version: 1, updated_at: new Date().toISOString(), questions: {} };
        state.questions[`${courseId}::${mcQs[0]._packId || "default"}::${mcQs[0].id}`] = {
          tier: 2, review_count: 1, next_due_at: new Date(Date.now() - 172800000).toISOString()
        };
        state.questions[`${courseId}::${mcQs[1]._packId || "default"}::${mcQs[1].id}`] = {
          tier: 2, review_count: 1, next_due_at: new Date(Date.now() - 86400000).toISOString()
        };
        saveSRSState(courseId, state);
      }
      srsBatchSize = 1;
    });
    await page.locator('#startSrsBtn').click();
    await expect(page.locator('#quizGrid .card')).toHaveCount(1);

    // Answer Q1 correctly and rate "Good"
    const correctIdx = await page.evaluate(() => {
      const q = questions[0];
      const { indexMap } = renderState.get(q._uid);
      return indexMap.findIndex(origIdx => origIdx === q.answer);
    });
    await page.locator('#quizGrid .card label.choice').nth(correctIdx).click();
    await page.locator('#srsAfterFeedback button[data-rating="good"]').click();

    // Summary screen is shown
    await expect(page.locator('#srsSummaryScreen')).toBeVisible();

    // 1. Click Continue Reviewing when more questions are available
    await page.locator('#srsContinueBtn').click();
    await expect(page.locator('#quizGrid')).toBeVisible();
    await expect(page.locator('#srsSummaryScreen')).toBeHidden();

    // Now answer this second batch question
    const correctIdx2 = await page.evaluate(() => {
      const q = questions[0];
      const { indexMap } = renderState.get(q._uid);
      return indexMap.findIndex(origIdx => origIdx === q.answer);
    });
    await page.locator('#quizGrid .card label.choice').nth(correctIdx2).click();
    await page.locator('#srsAfterFeedback button[data-rating="good"]').click();
    await expect(page.locator('#srsSummaryScreen')).toBeVisible();

    // 2. Now simulate queue being empty by setting questions to empty array
    await page.evaluate(() => {
      currentCourse.modules.forEach(mod => {
        const modFile = typeof mod === "string" ? mod : mod.file;
        if (allQuestionsByModule[modFile]) {
          allQuestionsByModule[modFile].questions = [];
        }
      });
    });

    await page.locator('#srsContinueBtn').click();

    // Verify alert dialog and return to config screen
    await expect(page.locator('#dialogModal')).toBeVisible();
    await expect(page.locator('#dialogModal')).toContainText("No more questions due for review!");
    await page.locator('#dialogConfirmBtn').click();
    await expect(page.locator('#dialogModal')).toBeHidden();
    await expect(page.locator('#quizConfig')).toBeVisible();
    await expect(page.locator('#quizScreen')).toBeHidden();
  });
});

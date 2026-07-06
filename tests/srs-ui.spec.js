const { test, expect } = require('@playwright/test');

test.describe('Spaced Repetition Review Mode (SRS) UI', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/app/');
    await page.evaluate(() => localStorage.clear());
  });

  test('srsBanner is displayed when a course is selected on the config screen', async ({ page }) => {
    await page.locator('.course-card').first().click();
    await expect(page.locator("#moduleList .module-row").first()).toBeVisible();
    await expect(page.locator('#srsBanner')).toBeVisible();
  });

  test('summary cue numbers and bar graph segment widths accurately reflect seeded localStorage tier distribution', async ({ page }) => {
    await page.locator('.course-card').first().click();
    await expect(page.locator("#moduleList .module-row").first()).toBeVisible();
    
    // Seed localStorage with specific question tiers
    const stats = await page.evaluate(() => {
      const courseId = currentCourse.id;
      const allQs = [];
      currentCourse.modules.forEach(mod => {
        const modFile = typeof mod === "string" ? mod : mod.file;
        if (allQuestionsByModule[modFile]) {
          allQs.push(...allQuestionsByModule[modFile].questions);
        }
      });
      
      const now = Date.now();
      const state = {
        schema_version: 1,
        updated_at: new Date().toISOString(),
        questions: {}
      };

      // Ensure we have at least 4 questions to seed
      if (allQs.length >= 4) {
        // q0: Overdue (tier 1, due 2 days ago)
        state.questions[`${courseId}::${allQs[0]._packId || "default"}::${allQs[0].id}`] = {
          tier: 1,
          review_count: 1,
          next_due_at: new Date(now - 172800000).toISOString()
        };
        // q1: Due Today (tier 2, due 1 hour ago)
        state.questions[`${courseId}::${allQs[1]._packId || "default"}::${allQs[1].id}`] = {
          tier: 2,
          review_count: 2,
          next_due_at: new Date(now - 3600000).toISOString()
        };
        // q2: Future scheduled (tier 3, due tomorrow)
        state.questions[`${courseId}::${allQs[2]._packId || "default"}::${allQs[2].id}`] = {
          tier: 3,
          review_count: 3,
          next_due_at: new Date(now + 86400000).toISOString()
        };
      }

      saveSRSState(courseId, state);
      renderSRSBanner();
      return { total: allQs.length };
    });

    // Check summary cue numbers
    await expect(page.locator('#srsStatOverdue')).toHaveText('1');
    await expect(page.locator('#srsStatDue')).toHaveText('1');
    await expect(page.locator('#srsStatTotal')).toHaveText('3');
    await expect(page.locator('#srsStatNew')).toHaveText(String(stats.total - 3));
    await expect(page.locator('#srsTotalSummary')).toHaveText(`3 of ${stats.total} tracked`);
    await expect(page.locator('#srsDueBtnCount')).toHaveText('2');

    // Check bar graph segment widths
    const t1Width = await page.locator('#srsBarT1').evaluate(el => el.style.width);
    expect(parseFloat(t1Width)).toBeCloseTo((1 / stats.total) * 100, 1);

    const t2Width = await page.locator('#srsBarT2').evaluate(el => el.style.width);
    expect(parseFloat(t2Width)).toBeCloseTo((1 / stats.total) * 100, 1);

    const t3Width = await page.locator('#srsBarT3').evaluate(el => el.style.width);
    expect(parseFloat(t3Width)).toBeCloseTo((1 / stats.total) * 100, 1);

    const t0Width = await page.locator('#srsBarT0').evaluate(el => el.style.width);
    expect(parseFloat(t0Width)).toBeCloseTo(((stats.total - 3) / stats.total) * 100, 1);
  });

  test('clicking quick-pick chips updates active state and batch size, and clicking startSrsBtn launches quiz screen with buildSRSQueue questions', async ({ page }) => {
    await page.locator('.course-card').first().click();
    await expect(page.locator("#moduleList .module-row").first()).toBeVisible();

    // Verify initial active chip
    await expect(page.locator('#srsQuickPickChips .quick-pick-chip[data-srs-size="10"]')).toHaveClass(/active/);

    // Click chip 20
    await page.locator('#srsQuickPickChips .quick-pick-chip[data-srs-size="20"]').click();
    await expect(page.locator('#srsQuickPickChips .quick-pick-chip[data-srs-size="20"]')).toHaveClass(/active/);
    await expect(page.locator('#srsQuickPickChips .quick-pick-chip[data-srs-size="10"]')).not.toHaveClass(/active/);

    // Verify srsBatchSize updated in JS
    const batchSize = await page.evaluate(() => srsBatchSize);
    expect(batchSize).toBe(20);

    // Click Start SRS Review button
    await page.locator('#startSrsBtn').click();

    // Verify quiz screen is displayed and questions match buildSRSQueue
    await expect(page.locator('#quizGrid')).toBeVisible();
    const quizState = await page.evaluate(() => {
      return {
        srsMode,
        retryMode,
        questionCount: questions.length,
        expectedCount: buildSRSQueue(currentCourse.id, 20).length
      };
    });

    expect(quizState.srsMode).toBe(true);
    expect(quizState.retryMode).toBe(false);
    expect(quizState.questionCount).toBe(quizState.expectedCount);
    expect(quizState.questionCount).toBeGreaterThan(0);
  });
});

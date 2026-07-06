const { test, expect } = require('@playwright/test');

test.describe('SRS Storage and Import/Export Functionality', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/app/');
    await page.evaluate(() => localStorage.clear());
  });

  test('getSRSState initializes clean schema on empty storage', async ({ page }) => {
    const state = await page.evaluate(() => {
      return typeof getSRSState === 'function' ? getSRSState('test_course_alpha') : null;
    });

    expect(state).not.toBeNull();
    expect(state.schema_version).toBe(1);
    expect(state.questions).toEqual({});
    expect(typeof state.updated_at).toBe('string');
  });

  test('saveSRSState returns boolean true/false and isolates data per course', async ({ page }) => {
    const results = await page.evaluate(() => {
      const stateA = getSRSState('course_a');
      const stateB = getSRSState('course_b');

      stateA.questions['course_a::pack_1::q1'] = { tier: 2, review_count: 5 };
      stateB.questions['course_b::pack_1::q1'] = { tier: 1, review_count: 1 };

      const saveResultA = saveSRSState('course_a', stateA);
      const saveResultB = saveSRSState('course_b', stateB);

      const loadedA = getSRSState('course_a');
      const loadedB = getSRSState('course_b');

      return {
        saveResultA,
        saveResultB,
        loadedA,
        loadedB
      };
    });

    expect(results.saveResultA).toBe(true);
    expect(results.saveResultB).toBe(true);
    expect(results.loadedA.questions['course_a::pack_1::q1'].tier).toBe(2);
    expect(results.loadedA.questions['course_b::pack_1::q1']).toBeUndefined();
    expect(results.loadedB.questions['course_b::pack_1::q1'].tier).toBe(1);
    expect(results.loadedB.questions['course_a::pack_1::q1']).toBeUndefined();
  });

  test('updateQuestionSRS correctly formats composite keys and updates stats', async ({ page }) => {
    const res = await page.evaluate(() => {
      const success = updateQuestionSRS('course_x', 'pack_y', 'q_100', 'good');
      const state = getSRSState('course_x');
      return { success, state };
    });

    expect(res.success).toBe(true);
    const qEntry = res.state.questions['course_x::pack_y::q_100'];
    expect(qEntry).toBeDefined();
    expect(qEntry.last_result).toBe('good');
    expect(qEntry.review_count).toBe(1);
    expect(qEntry.tier).toBe(1);
  });

  test('JSON Import correctly validates and restores state, rejecting malformed blobs without corrupting storage', async ({ page }) => {
    await page.evaluate(() => {
      saveSRSState('target_course', {
        schema_version: 1,
        updated_at: new Date().toISOString(),
        questions: { 'target_course::p1::q1': { tier: 3, review_count: 10 } }
      });
    });

    const invalidJsonContent = JSON.stringify({ invalid_field: "no_schema_version", data: [1, 2, 3] });
    await page.setInputFiles('#srsImportInput', {
      name: 'bad_srs.json',
      mimeType: 'application/json',
      buffer: Buffer.from(invalidJsonContent)
    });

    await page.waitForTimeout(500);

    const stateAfterBadImport = await page.evaluate(() => getSRSState('target_course'));
    expect(stateAfterBadImport.questions['target_course::p1::q1'].tier).toBe(3);

    const validJsonContent = JSON.stringify({
      course_id: 'target_course',
      schema_version: 1,
      updated_at: new Date().toISOString(),
      questions: {
        'target_course::p1::q1': { tier: 4, review_count: 11 },
        'target_course::p1::q2': { tier: 1, review_count: 1 }
      }
    });

    await page.setInputFiles('#srsImportInput', {
      name: 'good_srs.json',
      mimeType: 'application/json',
      buffer: Buffer.from(validJsonContent)
    });

    await page.waitForTimeout(500);

    const stateAfterGoodImport = await page.evaluate(() => getSRSState('target_course'));
    expect(stateAfterGoodImport.questions['target_course::p1::q1'].tier).toBe(4);
    expect(stateAfterGoodImport.questions['target_course::p1::q2'].tier).toBe(1);
  });
});

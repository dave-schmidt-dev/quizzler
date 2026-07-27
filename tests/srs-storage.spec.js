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

  test('saveSRSState persists and isolates data per course', async ({ page }) => {
    const results = await page.evaluate(async () => {
      const stateA = getSRSState('course_a');
      const stateB = getSRSState('course_b');

      stateA.questions['course_a::pack_1::q1'] = { tier: 2, review_count: 5 };
      stateB.questions['course_b::pack_1::q1'] = { tier: 1, review_count: 1 };

      await saveSRSState('course_a', stateA);
      await saveSRSState('course_b', stateB);

      const loadedA = getSRSState('course_a');
      const loadedB = getSRSState('course_b');

      return {
        loadedA,
        loadedB
      };
    });

    expect(results.loadedA.questions['course_a::pack_1::q1'].tier).toBe(2);
    expect(results.loadedA.questions['course_b::pack_1::q1']).toBeUndefined();
    expect(results.loadedB.questions['course_b::pack_1::q1'].tier).toBe(1);
    expect(results.loadedB.questions['course_a::pack_1::q1']).toBeUndefined();
  });

  test('updateQuestionSRS correctly formats composite keys and updates stats', async ({ page }) => {
    const res = await page.evaluate(async () => {
      await updateQuestionSRS('course_x', 'pack_y', 'q_100', 'good');
      const state = getSRSState('course_x');
      return { state };
    });

    const qEntry = res.state.questions['course_x::pack_y::q_100'];
    expect(qEntry).toBeDefined();
    expect(qEntry.last_result).toBe('good');
    expect(qEntry.review_count).toBe(1);
    expect(qEntry.tier).toBe(2);
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

  test('JSON Import drops per-entry-invalid values/keys, keeps only valid entries, counts dropped, and writes a backup', async ({ page }) => {
    await page.evaluate(() => {
      saveSRSState('target_course', {
        schema_version: 1,
        updated_at: new Date().toISOString(),
        questions: { 'target_course::p1::existing': { tier: 2, review_count: 3 } }
      });
    });

    const mixedPayload = JSON.stringify({
      course_id: 'target_course',
      schema_version: 1,
      updated_at: new Date().toISOString(),
      questions: {
        // valid entries
        'target_course::p1::q1': { tier: 4, review_count: 11 },
        'target_course::p1::q2': { tier: 0, review_count: 0 },
        // bad tier (out of 0-7 range)
        'target_course::p1::q_bad_tier': { tier: 99, review_count: 1 },
        // unparseable next_due_at
        'target_course::p1::q_bad_date': { tier: 2, next_due_at: 'not-a-date' },
        // primitive value
        'target_course::p1::q_primitive': "not-an-object",
        // array value
        'target_course::p1::q_array': [1, 2, 3],
        // malformed key: wrong course prefix
        'wrong_course::p1::q_wrong_course': { tier: 1 },
        // malformed key: no pack/question delimiters at all
        'target_course_no_delims': { tier: 1 }
      }
    });

    await page.setInputFiles('#srsImportInput', {
      name: 'mixed_srs.json',
      mimeType: 'application/json',
      buffer: Buffer.from(mixedPayload)
    });

    await page.waitForTimeout(500);
    await expect(page.locator('#dialogModal')).toBeVisible();
    await expect(page.locator('#dialogModalBody')).toContainText('Imported 2 entries, dropped 6 invalid.');
    await page.locator('#dialogConfirmBtn').click();

    const result = await page.evaluate(() => {
      const state = getSRSState('target_course');
      const backupKeys = [];
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && k.startsWith('quizzler_srs_state_v1::target_course__backup_')) backupKeys.push(k);
      }
      return { state, backupKeys };
    });

    expect(Object.keys(result.state.questions).sort()).toEqual([
      'target_course::p1::q1',
      'target_course::p1::q2'
    ]);
    expect(result.state.questions['target_course::p1::q1'].tier).toBe(4);
    expect(result.state.questions['target_course::p1::q2'].tier).toBe(0);
    expect(result.backupKeys.length).toBeGreaterThanOrEqual(1);

    // Backup snapshot should hold the PRE-import state (the "existing" entry).
    const backupContent = await page.evaluate((key) => JSON.parse(localStorage.getItem(key)), result.backupKeys[0]);
    expect(backupContent.questions['target_course::p1::existing']).toBeDefined();
  });

  test('Cross-course import shows a confirm gate; cancel leaves existing state untouched', async ({ page }) => {
    await page.locator('.course-card').first().click();
    await expect(page.locator("#moduleList .module-row").first()).toBeVisible();

    const currentCourseId = await page.evaluate(() => currentCourse.id);
    const otherCourseId = currentCourseId + '_other';

    await page.evaluate((cid) => {
      saveSRSState(cid, {
        schema_version: 1,
        updated_at: new Date().toISOString(),
        questions: { [`${cid}::p1::existing`]: { tier: 3, review_count: 5 } }
      });
    }, currentCourseId);

    const crossCoursePayload = JSON.stringify({
      course_id: otherCourseId,
      schema_version: 1,
      updated_at: new Date().toISOString(),
      questions: {
        [`${otherCourseId}::p1::q1`]: { tier: 2, review_count: 1 }
      }
    });

    await page.setInputFiles('#srsImportInput', {
      name: 'cross_course.json',
      mimeType: 'application/json',
      buffer: Buffer.from(crossCoursePayload)
    });

    await expect(page.locator('#dialogModal')).toBeVisible();
    await expect(page.locator('#dialogModalBody')).toContainText(otherCourseId);
    await expect(page.locator('#dialogModalBody')).toContainText(currentCourseId);

    // Cancel: existing state for the CURRENT course must be untouched, and
    // no state should have been created for the OTHER course.
    await page.locator('#dialogCancelBtn').click();

    const stateAfterCancel = await page.evaluate((args) => {
      return {
        current: getSRSState(args.cid),
        otherRaw: localStorage.getItem('quizzler_srs_state_v1::' + args.other)
      };
    }, { cid: currentCourseId, other: otherCourseId });

    expect(stateAfterCancel.current.questions[`${currentCourseId}::p1::existing`].tier).toBe(3);
    expect(stateAfterCancel.otherRaw).toBeNull();
  });

  test('Failing backup write aborts the import and leaves existing state intact', async ({ page }) => {
    await page.evaluate(() => {
      saveSRSState('target_course', {
        schema_version: 1,
        updated_at: new Date().toISOString(),
        questions: { 'target_course::p1::existing': { tier: 2, review_count: 3 } }
      });
    });

    // Force safeSetItem to fail only for the backup key, so we can assert the
    // abort path without breaking the rest of the page's storage use.
    await page.evaluate(() => {
      const originalSetItem = localStorage.setItem.bind(localStorage);
      window.__originalSetItem = originalSetItem;
      localStorage.setItem = function (key, value) {
        if (typeof key === 'string' && key.includes('__backup_')) {
          const err = new Error('Quota exceeded (simulated)');
          err.name = 'QuotaExceededError';
          throw err;
        }
        return originalSetItem(key, value);
      };
    });

    const payload = JSON.stringify({
      course_id: 'target_course',
      schema_version: 1,
      updated_at: new Date().toISOString(),
      questions: {
        'target_course::p1::q1': { tier: 4, review_count: 11 }
      }
    });

    await page.setInputFiles('#srsImportInput', {
      name: 'should_abort.json',
      mimeType: 'application/json',
      buffer: Buffer.from(payload)
    });

    await page.waitForTimeout(500);
    await expect(page.locator('#dialogModal')).toBeVisible();
    await expect(page.locator('#dialogModalTitle')).toHaveText('Import failed');
    await page.locator('#dialogConfirmBtn').click();

    // Restore the real setItem before reading state back out.
    await page.evaluate(() => {
      localStorage.setItem = window.__originalSetItem;
    });

    const stateAfterAbort = await page.evaluate(() => getSRSState('target_course'));
    expect(stateAfterAbort.questions['target_course::p1::existing'].tier).toBe(2);
    expect(stateAfterAbort.questions['target_course::p1::q1']).toBeUndefined();
  });
});

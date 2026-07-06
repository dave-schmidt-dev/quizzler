const { test, expect } = require('@playwright/test');

test.describe('Spaced Repetition Review Mode (SRS) Scheduling and Queue Building', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/app/');
    await page.evaluate(() => localStorage.clear());
  });

  test('buildSRSQueue orders overdue/due items before unassigned items, and unassigned before future due items', async ({ page }) => {
    const result = await page.evaluate(() => {
      // Setup dummy course and module data
      COURSES = [{
        id: 'course_sched_1',
        title: 'Scheduling Test Course',
        modules: [{ file: 'mod_sched.json', questionCount: 4 }]
      }];

      allQuestionsByModule['mod_sched.json'] = {
        meta: { file: 'mod_sched.json' },
        pack: { pack_id: 'pack_sched' },
        questions: [
          { id: 'q_future', _packId: 'pack_sched', prompt: 'Future Question' },
          { id: 'q_overdue_1', _packId: 'pack_sched', prompt: 'Overdue Question 1' },
          { id: 'q_unassigned', _packId: 'pack_sched', prompt: 'Unassigned Question' },
          { id: 'q_overdue_2_oldest', _packId: 'pack_sched', prompt: 'Oldest Overdue Question' }
        ]
      };

      const now = Date.now();
      const state = {
        schema_version: 1,
        updated_at: new Date().toISOString(),
        questions: {
          'course_sched_1::pack_sched::q_overdue_1': {
            tier: 2,
            review_count: 2,
            next_due_at: new Date(now - 3600000).toISOString() // 1 hour overdue
          },
          'course_sched_1::pack_sched::q_overdue_2_oldest': {
            tier: 3,
            review_count: 4,
            next_due_at: new Date(now - 7200000).toISOString() // 2 hours overdue (oldest!)
          },
          'course_sched_1::pack_sched::q_future': {
            tier: 1,
            review_count: 1,
            next_due_at: new Date(now + 86400000).toISOString() // due in 1 day (future)
          }
          // q_unassigned has no entry in state.questions
        }
      };

      saveSRSState('course_sched_1', state);

      const fullQueue = buildSRSQueue('course_sched_1', 10).map(q => q.id);
      const batchedQueue = buildSRSQueue('course_sched_1', 2).map(q => q.id);

      return { fullQueue, batchedQueue };
    });

    // Priority 1 (Overdue): q_overdue_2_oldest (oldest first), then q_overdue_1
    // Priority 2 (Unassigned): q_unassigned
    // Priority 3 (Future): q_future
    expect(result.fullQueue).toEqual([
      'q_overdue_2_oldest',
      'q_overdue_1',
      'q_unassigned',
      'q_future'
    ]);

    expect(result.batchedQueue).toEqual([
      'q_overdue_2_oldest',
      'q_overdue_1'
    ]);
  });

  test('answering via updateQuestionSRS updates next_due_at and tiers per interval rules (1d, 3d, 7d, etc.)', async ({ page }) => {
    const stats = await page.evaluate(() => {
      const courseId = 'course_intervals';
      const packId = 'pack_int';
      const now = Date.now();

      // Setup initial state with questions at various tiers
      const state = {
        schema_version: 1,
        updated_at: new Date().toISOString(),
        questions: {
          'course_intervals::pack_int::q_t1': { tier: 1, review_count: 1, next_due_at: new Date(now).toISOString() },
          'course_intervals::pack_int::q_t2': { tier: 2, review_count: 2, next_due_at: new Date(now).toISOString() },
          'course_intervals::pack_int::q_t3': { tier: 3, review_count: 3, next_due_at: new Date(now).toISOString() },
          'course_intervals::pack_int::q_t5': { tier: 5, review_count: 5, next_due_at: new Date(now).toISOString(), lapse_count: 0 }
        }
      };
      saveSRSState(courseId, state);

      // 1. Answer good on Tier 1 -> advances to Tier 2 (3 days = 259,200,000 ms)
      updateQuestionSRS(courseId, packId, 'q_t1', 'good');

      // 2. Answer easy on Tier 2 -> advances by +2 to Tier 4 (14 days = 1,209,600,000 ms * 1.25 = 1,512,000,000 ms)
      updateQuestionSRS(courseId, packId, 'q_t2', 'easy');

      // 3. Answer hard on Tier 3 -> stays Tier 3 (7 days = 604,800,000 ms * 0.75 = 453,600,000 ms)
      updateQuestionSRS(courseId, packId, 'q_t3', 'hard');

      // 4. Answer again on Tier 5 -> drops to Tier 3 (Math.max(1, 5 - 2)), next_due_at = +10 mins (600,000 ms)
      updateQuestionSRS(courseId, packId, 'q_t5', 'again');

      const updatedState = getSRSState(courseId);
      const getDiff = (qid) => new Date(updatedState.questions[`${courseId}::${packId}::${qid}`].next_due_at).getTime() - Date.now();

      return {
        q_t1: {
          tier: updatedState.questions['course_intervals::pack_int::q_t1'].tier,
          diff: getDiff('q_t1'),
          last_result: updatedState.questions['course_intervals::pack_int::q_t1'].last_result
        },
        q_t2: {
          tier: updatedState.questions['course_intervals::pack_int::q_t2'].tier,
          diff: getDiff('q_t2'),
          last_result: updatedState.questions['course_intervals::pack_int::q_t2'].last_result
        },
        q_t3: {
          tier: updatedState.questions['course_intervals::pack_int::q_t3'].tier,
          diff: getDiff('q_t3'),
          last_result: updatedState.questions['course_intervals::pack_int::q_t3'].last_result
        },
        q_t5: {
          tier: updatedState.questions['course_intervals::pack_int::q_t5'].tier,
          diff: getDiff('q_t5'),
          last_result: updatedState.questions['course_intervals::pack_int::q_t5'].last_result,
          lapse_count: updatedState.questions['course_intervals::pack_int::q_t5'].lapse_count
        }
      };
    });

    // Check q_t1 (good from tier 1 -> tier 2 = 3 days = 259,200,000 ms)
    expect(stats.q_t1.tier).toBe(2);
    expect(stats.q_t1.last_result).toBe('good');
    expect(stats.q_t1.diff).toBeGreaterThan(259200000 - 10000);
    expect(stats.q_t1.diff).toBeLessThan(259200000 + 10000);

    // Check q_t2 (easy from tier 2 -> tier 4 = 14 days * 1.25 = 1,512,000,000 ms)
    expect(stats.q_t2.tier).toBe(4);
    expect(stats.q_t2.last_result).toBe('easy');
    expect(stats.q_t2.diff).toBeGreaterThan(1512000000 - 10000);
    expect(stats.q_t2.diff).toBeLessThan(1512000000 + 10000);

    // Check q_t3 (hard on tier 3 -> stays tier 3 = 7 days * 0.75 = 453,600,000 ms)
    expect(stats.q_t3.tier).toBe(3);
    expect(stats.q_t3.last_result).toBe('hard');
    expect(stats.q_t3.diff).toBeGreaterThan(453600000 - 10000);
    expect(stats.q_t3.diff).toBeLessThan(453600000 + 10000);

    // Check q_t5 (again on tier 5 -> drops to tier 3 = 10 mins = 600,000 ms)
    expect(stats.q_t5.tier).toBe(3);
    expect(stats.q_t5.last_result).toBe('again');
    expect(stats.q_t5.lapse_count).toBe(1);
    expect(stats.q_t5.diff).toBeGreaterThan(600000 - 10000);
    expect(stats.q_t5.diff).toBeLessThan(600000 + 10000);
  });

  test('unassigned questions seed initial tier from srs_initial_tier authoring metadata when present, or from existing mastery state', async ({ page }) => {
    const results = await page.evaluate(() => {
      const courseId = 'course_seeding';
      const packId = 'pack_seed';

      // Setup mastery state: mark q_mastered as correct
      saveMastery(courseId, packId, {
        seen: {},
        correct: { 'q_mastered': true }
      });

      // 1. Seeding from authoring metadata (srs_initial_tier = 5). Using 'hard' keeps tier unchanged.
      updateQuestionSRS(courseId, packId, 'q_meta', 'hard', { srs_initial_tier: 5 });

      // 2. Seeding from mastery state (should seed at Tier 4). Using 'hard' keeps tier unchanged.
      updateQuestionSRS(courseId, packId, 'q_mastered', 'hard', {});

      // 3. Fallback seeding (should seed at Tier 1). Using 'hard' keeps tier unchanged.
      updateQuestionSRS(courseId, packId, 'q_fallback', 'hard', {});

      const state = getSRSState(courseId);
      return {
        metaTier: state.questions[`${courseId}::${packId}::q_meta`].tier,
        masteredTier: state.questions[`${courseId}::${packId}::q_mastered`].tier,
        fallbackTier: state.questions[`${courseId}::${packId}::q_fallback`].tier
      };
    });

    expect(results.metaTier).toBe(5);
    expect(results.masteredTier).toBe(4);
    expect(results.fallbackTier).toBe(1);
  });
});

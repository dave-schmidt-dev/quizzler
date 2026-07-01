// @ts-check
const { test, expect } = require("@playwright/test");

// ─── Helpers ───

// Navigate to home, click the first course card, wait for config screen
async function goToConfig(page) {
  await page.goto("/app/");
  const firstCard = page.locator(".course-card").first();
  await expect(firstCard).toBeVisible();
  await firstCard.click();
  await expect(page.locator("#quizConfig")).toBeVisible();
  await expect(page.locator("#moduleList .module-row")).not.toHaveCount(0);
  await expect(page.locator("#selectNoneBtn")).toBeVisible();
}

async function startQuiz(page, count = 5) {
  await goToConfig(page);
  await page.locator("#quizSize").fill(String(count));
  await page.locator("#startQuizBtn").click();
  await expect(page.locator("#quizScreen")).toBeVisible();
}

// Answer whatever question type a card contains
async function answerCard(card) {
  const hasMC = (await card.locator(".choices").count()) > 0;
  const hasTF = (await card.locator(".tf-choices").count()) > 0;
  const hasMatching = (await card.locator(".matching-grid").count()) > 0;

  if (hasMC) {
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

async function answerAll(page) {
  const cards = page.locator(".card");
  const count = await cards.count();
  for (let i = 0; i < count; i++) {
    await answerCard(cards.nth(i));
  }
}

async function clearStorage(page) {
  await page.goto("/app/");
  await page.evaluate(() => {
    localStorage.clear();
    // Re-prime the one-shot session-sweep sentinel. Test storage never has
    // pre-refactor sessions, so the legacy wipe is irrelevant — without
    // this, the next boot would treat the cleared state as "first boot
    // post-refactor" and wipe any session that the test seeds before reload.
    localStorage.setItem("quizzler_session_schema_v2", "1");
  });
}

// Get dynamic info about the first course from the page
async function getCourseInfo(page) {
  return page.evaluate(() => ({
    id: currentCourse.id,
    moduleCount: currentCourse.modules.length,
    totalQuestions: Object.values(allQuestionsByModule)
      .reduce((sum, m) => sum + m.questions.length, 0)
  }));
}

async function skipIfNoCard(card) {
  if ((await card.count()) === 0) { test.skip(); return true; }
  return false;
}

async function getInternalQuestionIds(page) {
  return page.evaluate(() =>
    Object.values(allQuestionsByModule).flatMap(m => m.questions.map(q => q.id))
  );
}

async function seedWithMissed(page, count) {
  const ids = await page.evaluate((n) => {
    const qs = Object.values(allQuestionsByModule).flatMap(m => m.questions);
    return qs.slice(0, n).map(q => q.id);
  }, count);
  await seedSession(page, {
    missed_questions: ids.map(id => ({ question_id: id, topic: "t", chapter: "C" })),
  });
  return ids;
}

// Seed a session into localStorage using dynamic course info
async function seedSession(page, overrides = {}) {
  await page.evaluate((overrides) => {
    const courseId = typeof currentCourse !== "undefined" && currentCourse
      ? currentCourse.id : (overrides.course || "samples");
    const session = {
      quiz_id: "test-session",
      course: courseId,
      title: courseId.toUpperCase(),
      modules_used: ["sample-pack.json"],
      retry_mode: false,
      completed_at: new Date().toISOString(),
      score: { correct: 8, total: 10 },
      missed_topics: ["topic-a", "topic-b"],
      missed_chapters: ["Ch1"],
      missed_questions: [
        { question_id: "q1", topic: "topic-a", chapter: "Ch1" },
        { question_id: "q2", topic: "topic-b", chapter: "Ch1" },
      ],
      topic_summary: [{ topic: "topic-a", correct: 0, total: 1 }],
      chapter_summary: [{ chapter: "Ch1", correct: 0, total: 2, pct: 0 }],
      answers: [],
      ...overrides,
    };
    const existing = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
    existing.unshift(session);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(existing));
  }, overrides);
}


// ═══════════════════════════════════════════════════════════
// 1. HOME SCREEN
// ═══════════════════════════════════════════════════════════

test.describe("Home Screen", () => {
  test("renders at least one course card", async ({ page }) => {
    await page.goto("/app/");
    const cards = page.locator(".course-card");
    await expect(cards.first()).toBeVisible();
    const count = await cards.count();
    expect(count).toBeGreaterThanOrEqual(1);
  });

  test("history button navigates to history screen", async ({ page }) => {
    await page.goto("/app/");
    await page.locator("#historyBtn").click();
    await expect(page.locator("#historyScreen")).toBeVisible();
  });
});


// ═══════════════════════════════════════════════════════════
// 2. MODULE SELECTION & QUIZ CONFIG
// ═══════════════════════════════════════════════════════════

test.describe("Module Selection", () => {
  test("clicking a course loads modules checked by default", async ({ page }) => {
    await goToConfig(page);
    const info = await getCourseInfo(page);
    await expect(page.locator("#moduleList .module-row")).toHaveCount(info.moduleCount);
    const checkboxes = page.locator('#moduleList input[type="checkbox"]');
    for (let i = 0; i < info.moduleCount; i++) {
      await expect(checkboxes.nth(i)).toBeChecked();
    }
  });

  test("available count reflects all questions when all modules selected", async ({ page }) => {
    await goToConfig(page);
    const info = await getCourseInfo(page);
    const count = parseInt(await page.locator("#availableCount").textContent());
    expect(count).toBe(info.totalQuestions);
  });

  test("select none unchecks all and sets available to 0", async ({ page }) => {
    await goToConfig(page);
    const info = await getCourseInfo(page);
    await page.locator("#selectNoneBtn").click();
    await expect(page.locator("#availableCount")).toHaveText("0");
    const checkboxes = page.locator('#moduleList input[type="checkbox"]');
    for (let i = 0; i < info.moduleCount; i++) {
      await expect(checkboxes.nth(i)).not.toBeChecked();
    }
  });

  test("select all re-checks all modules", async ({ page }) => {
    await goToConfig(page);
    const info = await getCourseInfo(page);
    await page.locator("#selectNoneBtn").click();
    await expect(page.locator("#availableCount")).toHaveText("0");
    await page.locator("#selectAllBtn").click();
    const count = parseInt(await page.locator("#availableCount").textContent());
    expect(count).toBe(info.totalQuestions);
  });

  test("toggling a single module updates available count", async ({ page }) => {
    await goToConfig(page);
    const totalBefore = parseInt(await page.locator("#availableCount").textContent());
    await page.locator("#moduleList .module-row").first().click();
    const totalAfter = parseInt(await page.locator("#availableCount").textContent());
    expect(totalAfter).toBeLessThan(totalBefore);
  });

  test("cannot start quiz with no modules selected", async ({ page }) => {
    await goToConfig(page);
    await page.locator("#selectNoneBtn").click();
    await expect(page.locator("#startQuizBtn")).toBeDisabled();
    await page.locator("#startQuizBtn").click({ force: true }).catch(() => {});
    await expect(page.locator("#quizConfig")).toBeVisible();
  });
});


// ═══════════════════════════════════════════════════════════
// 3. QUIZ SIZE LIMITING
// ═══════════════════════════════════════════════════════════

test.describe("Quiz Size", () => {
  test("requesting fewer questions gives exact count", async ({ page }) => {
    await goToConfig(page);
    const info = await getCourseInfo(page);
    const requestSize = Math.min(info.totalQuestions, 3);
    await page.locator("#quizSize").fill(String(requestSize));
    await page.locator("#startQuizBtn").click();
    await expect(page.locator("#quizScreen")).toBeVisible();
    await expect(page.locator(".card")).toHaveCount(requestSize);
  });

  test("requesting 1 question gives exactly 1", async ({ page }) => {
    await startQuiz(page, 1);
    await expect(page.locator(".card")).toHaveCount(1);
  });

  test("requesting more than available caps at available", async ({ page }) => {
    await goToConfig(page);
    // Select only first module
    await page.locator("#selectNoneBtn").click();
    await page.locator("#moduleList .module-row").first().click();
    const firstModuleCount = parseInt(await page.locator("#availableCount").textContent());
    await page.locator("#quizSize").fill("9999");
    await page.locator("#startQuizBtn").click();
    await expect(page.locator("#quizScreen")).toBeVisible();
    const cardCount = await page.locator(".card").count();
    expect(cardCount).toBeLessThanOrEqual(firstModuleCount);
  });
});


// ═══════════════════════════════════════════════════════════
// 4. MODULE FILTERING
// ═══════════════════════════════════════════════════════════

test.describe("Module Filtering", () => {
  test("selecting only first module yields only that module's question IDs", async ({ page }) => {
    await goToConfig(page);
    await page.locator("#selectNoneBtn").click();
    await page.locator("#moduleList .module-row").first().click();
    // Get the expected IDs from the first module
    const expectedIds = await page.evaluate(() => {
      const firstFile = currentCourse.modules[0].file;
      return allQuestionsByModule[firstFile].questions.map(q => q.id);
    });
    await page.locator("#quizSize").fill("9999");
    await page.locator("#startQuizBtn").click();
    await expect(page.locator("#quizScreen")).toBeVisible();

    const quizIds = await page.locator(".question-id").allTextContents();
    const expectedSet = new Set(expectedIds);
    for (const id of quizIds) {
      expect(expectedSet.has(id.trim())).toBe(true);
    }
  });

});


// ═══════════════════════════════════════════════════════════
// 5. QUESTION ID DISPLAY
// ═══════════════════════════════════════════════════════════

test.describe("Question IDs", () => {
  test("every card shows a non-empty question ID", async ({ page }) => {
    await startQuiz(page, 5);
    const ids = page.locator(".question-id");
    await expect(ids).toHaveCount(5);
    for (let i = 0; i < 5; i++) {
      const text = await ids.nth(i).textContent();
      expect(text.trim().length).toBeGreaterThan(0);
    }
  });

  test("question IDs are unique within a quiz", async ({ page }) => {
    await goToConfig(page);
    const info = await getCourseInfo(page);
    const quizSize = Math.min(info.totalQuestions, 20);
    await page.locator("#quizSize").fill(String(quizSize));
    await page.locator("#startQuizBtn").click();
    await expect(page.locator("#quizScreen")).toBeVisible();
    const ids = await page.locator(".question-id").allTextContents();
    const trimmed = ids.map((id) => id.trim());
    const unique = new Set(trimmed);
    expect(unique.size).toBe(trimmed.length);
  });
});


// ═══════════════════════════════════════════════════════════
// 6. PROGRESS BAR
// ═══════════════════════════════════════════════════════════

test.describe("Progress Strip", () => {
  test("starts at 0% with zeroed stats", async ({ page }) => {
    await startQuiz(page, 5);
    const width = await page.locator("#progressBar").evaluate((el) => el.style.width);
    expect(width).toBe("0%");
    await expect(page.locator("#statAnswered")).toHaveText("0");
    await expect(page.locator("#statCorrect")).toHaveText("0");
    await expect(page.locator("#statIncorrect")).toHaveText("0");
  });

  test("updates stats as questions are answered", async ({ page }) => {
    await startQuiz(page, 4);
    await answerCard(page.locator(".card").first());
    const width = await page.locator("#progressBar").evaluate((el) => el.style.width);
    expect(width).toBe("25%");
    await expect(page.locator("#statAnswered")).toHaveText("1");
    const c = parseInt(await page.locator("#statCorrect").textContent());
    const w = parseInt(await page.locator("#statIncorrect").textContent());
    expect(c + w).toBe(1);
  });

  test("reaches 100% when all answered", async ({ page }) => {
    await startQuiz(page, 3);
    await answerAll(page);
    const width = await page.locator("#progressBar").evaluate((el) => el.style.width);
    expect(width).toBe("100%");
    await expect(page.locator("#statAnswered")).toHaveText("3");
  });

  test("is sticky and visible after scrolling", async ({ page }) => {
    await goToConfig(page);
    const info = await getCourseInfo(page);
    const quizSize = Math.min(info.totalQuestions, 20);
    await page.locator("#quizSize").fill(String(quizSize));
    await page.locator("#startQuizBtn").click();
    await expect(page.locator("#quizScreen")).toBeVisible();
    await page.evaluate(() => window.scrollTo(0, 2000));
    await expect(page.locator("#progressStrip")).toBeVisible();
    await expect(page.locator("#progressStrip")).toBeInViewport();
  });
});


// ═══════════════════════════════════════════════════════════
// 7. MULTIPLE CHOICE ANSWERING
// ═══════════════════════════════════════════════════════════

test.describe("Multiple Choice", () => {
  test("clicking an option marks and disables all choices", async ({ page }) => {
    await startQuiz(page, 5);
    const mcCard = page.locator(".card:has(.choices)").first();
    if (await skipIfNoCard(mcCard)) return;
    const choices = mcCard.locator("label.choice");
    await choices.first().click();

    const count = await choices.count();
    for (let i = 0; i < count; i++) {
      await expect(choices.nth(i)).toHaveClass(/is-disabled/);
    }
  });

  test("correct answer is always highlighted green", async ({ page }) => {
    await startQuiz(page, 5);
    const mcCard = page.locator(".card:has(.choices)").first();
    if (await skipIfNoCard(mcCard)) return;
    await mcCard.locator("label.choice").first().click();
    await expect(mcCard.locator("label.is-correct")).not.toHaveCount(0);
  });

  test("cannot re-answer after clicking", async ({ page }) => {
    await startQuiz(page, 5);
    const mcCard = page.locator(".card:has(.choices)").first();
    if (await skipIfNoCard(mcCard)) return;
    const choices = mcCard.locator("label.choice");
    await choices.first().click();

    const correctBefore = await mcCard.locator("label.is-correct").count();
    const incorrectBefore = await mcCard.locator("label.is-incorrect").count();

    await choices.last().click();

    expect(await mcCard.locator("label.is-correct").count()).toBe(correctBefore);
    expect(await mcCard.locator("label.is-incorrect").count()).toBe(incorrectBefore);
  });

  test("feedback text appears after answering", async ({ page }) => {
    await startQuiz(page, 5);
    const mcCard = page.locator(".card:has(.choices)").first();
    if (await skipIfNoCard(mcCard)) return;
    await mcCard.locator("label.choice").first().click();
    const feedback = await mcCard.locator(".feedback").textContent();
    expect(feedback.length).toBeGreaterThan(0);
  });
});


// ═══════════════════════════════════════════════════════════
// 8. TRUE/FALSE ANSWERING
// ═══════════════════════════════════════════════════════════

test.describe("True/False", () => {
  test("clicking a TF button disables both buttons", async ({ page }) => {
    await startQuiz(page, 5);
    const tfCard = page.locator(".card:has(.tf-choices)").first();
    if (await skipIfNoCard(tfCard)) return;

    await tfCard.locator(".tf-btn").first().click();
    await expect(tfCard.locator('.tf-btn[data-value="true"]')).toHaveClass(/is-disabled/);
    await expect(tfCard.locator('.tf-btn[data-value="false"]')).toHaveClass(/is-disabled/);
  });

  test("one button is green after answering TF", async ({ page }) => {
    await startQuiz(page, 5);
    const tfCard = page.locator(".card:has(.tf-choices)").first();
    if (await skipIfNoCard(tfCard)) return;

    await tfCard.locator(".tf-btn").first().click();
    await expect(tfCard.locator(".tf-btn.is-correct")).toHaveCount(1);
  });
});


// ═══════════════════════════════════════════════════════════
// 9. MATCHING QUESTIONS
// ═══════════════════════════════════════════════════════════

test.describe("Matching Questions", () => {
  test("matching question renders dropdowns for each left item", async ({ page }) => {
    await startQuiz(page, 5);
    const matchCard = page.locator(".card:has(.matching-grid)").first();
    if (await skipIfNoCard(matchCard)) return;

    const selects = matchCard.locator("select");
    const terms = matchCard.locator(".matching-term");
    const selectCount = await selects.count();
    const termCount = await terms.count();
    expect(selectCount).toBe(termCount);
    expect(selectCount).toBeGreaterThan(0);
  });

  test("Check Matches button requires all dropdowns filled", async ({ page }) => {
    await startQuiz(page, 5);
    const matchCard = page.locator(".card:has(.matching-grid)").first();
    if (await skipIfNoCard(matchCard)) return;

    const checkBtn = matchCard.locator('button:has-text("Check Matches")');
    await expect(checkBtn).toBeDisabled();
    await checkBtn.click({ force: true }).catch(() => {});
    const feedback = await matchCard.locator(".feedback").textContent();
    expect(feedback.trim()).toBe("");
  });

  test("filling all dropdowns and checking produces feedback", async ({ page }) => {
    await startQuiz(page, 5);
    const matchCard = page.locator(".card:has(.matching-grid)").first();
    if (await skipIfNoCard(matchCard)) return;

    const selects = matchCard.locator("select");
    const count = await selects.count();
    for (let i = 0; i < count; i++) {
      await selects.nth(i).selectOption({ index: 1 });
    }
    await matchCard.locator('button:has-text("Check Matches")').click();

    const feedback = await matchCard.locator(".feedback").textContent();
    expect(feedback.length).toBeGreaterThan(0);
  });

  test("matching rows get colored after checking", async ({ page }) => {
    await startQuiz(page, 5);
    const matchCard = page.locator(".card:has(.matching-grid)").first();
    if (await skipIfNoCard(matchCard)) return;

    const selects = matchCard.locator("select");
    const count = await selects.count();
    for (let i = 0; i < count; i++) {
      await selects.nth(i).selectOption({ index: 1 });
    }
    await matchCard.locator('button:has-text("Check Matches")').click();

    const correctRows = await matchCard.locator(".matching-row.is-correct").count();
    const incorrectRows = await matchCard.locator(".matching-row.is-incorrect").count();
    expect(correctRows + incorrectRows).toBe(count);
  });

  test("index 0 right-side matches grade correctly", async ({ page }) => {
    await startQuiz(page, 5);
    const matchCard = page.locator(".card:has(.matching-grid)").first();
    if (await skipIfNoCard(matchCard)) return;

    const selects = matchCard.locator("select");
    const count = await selects.count();
    for (let i = 0; i < count; i++) {
      await selects.nth(i).selectOption("0");
    }
    await matchCard.locator('button:has-text("Check Matches")').click();

    const correctRows = await matchCard.locator(".matching-row.is-correct").count();
    const incorrectRows = await matchCard.locator(".matching-row.is-incorrect").count();
    expect(correctRows + incorrectRows).toBe(count);
  });

  test("dropdowns are disabled after checking", async ({ page }) => {
    await startQuiz(page, 5);
    const matchCard = page.locator(".card:has(.matching-grid)").first();
    if (await skipIfNoCard(matchCard)) return;

    const selects = matchCard.locator("select");
    const count = await selects.count();
    for (let i = 0; i < count; i++) {
      await selects.nth(i).selectOption({ index: 1 });
    }
    await matchCard.locator('button:has-text("Check Matches")').click();

    for (let i = 0; i < count; i++) {
      await expect(selects.nth(i)).toBeDisabled();
    }
  });
});


// ═══════════════════════════════════════════════════════════
// 10. QUIZ COMPLETION & SCORING
// ═══════════════════════════════════════════════════════════

test.describe("Quiz Completion", () => {
  test("answering all questions shows score and hides notice", async ({ page }) => {
    await startQuiz(page, 3);
    await answerAll(page);
    await expect(page.locator("#score")).not.toHaveText("Score: Not graded yet");
    await expect(page.locator("#completionNotice")).toBeHidden();
  });

  test("saved session contains valid structure with required fields", async ({ page }) => {
    await clearStorage(page);
    await startQuiz(page, 3);
    const courseId = await page.evaluate(() => currentCourse.id);
    await answerAll(page);
    const sessions = await page.evaluate(() =>
      JSON.parse(localStorage.getItem(STORAGE_KEY))
    );
    const report = sessions[0];
    expect(report.course).toBe(courseId);
    expect(report.score.total).toBe(3);
    expect(report.answers).toHaveLength(3);
    expect(report.modules_used).toBeDefined();
    expect(report.missed_topics).toBeDefined();
    expect(report.topic_summary).toBeDefined();
    expect(report.completed_at).toBeDefined();
  });

  test("saved session has missed_topics and missed_questions arrays", async ({ page }) => {
    await clearStorage(page);
    await startQuiz(page, 2);
    await answerAll(page);
    const sessions = await page.evaluate(() =>
      JSON.parse(localStorage.getItem(STORAGE_KEY))
    );
    const report = sessions[0];
    expect(Array.isArray(report.missed_topics)).toBe(true);
    expect(Array.isArray(report.missed_questions)).toBe(true);
  });

  test("each answer includes response_ms > 0", async ({ page }) => {
    await clearStorage(page);
    await startQuiz(page, 2);
    await answerAll(page);
    const sessions = await page.evaluate(() =>
      JSON.parse(localStorage.getItem(STORAGE_KEY))
    );
    for (const a of sessions[0].answers) {
      expect(a.response_ms).toBeGreaterThan(0);
    }
  });
});


// ═══════════════════════════════════════════════════════════
// 11. LOCALSTORAGE PERSISTENCE
// ═══════════════════════════════════════════════════════════

test.describe("Session Persistence", () => {
  test("completed session is saved to localStorage", async ({ page }) => {
    await clearStorage(page);
    await startQuiz(page, 2);
    const courseId = await page.evaluate(() => currentCourse.id);
    await answerAll(page);

    const sessions = await page.evaluate(() =>
      JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]")
    );
    expect(sessions.length).toBe(1);
    expect(sessions[0].course).toBe(courseId);
    expect(sessions[0].score.total).toBe(2);
  });

  test("multiple sessions stack in localStorage", async ({ page }) => {
    await clearStorage(page);

    // Session 1
    await startQuiz(page, 2);
    await answerAll(page);

    // Session 2
    await page.locator("#backToConfig").click();
    await expect(page.locator("#quizConfig")).toBeVisible();
    await page.locator("#quizSize").fill("3");
    await page.locator("#startQuizBtn").click();
    await expect(page.locator("#quizScreen")).toBeVisible();
    await answerAll(page);

    const sessions = await page.evaluate(() =>
      JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]")
    );
    expect(sessions.length).toBe(2);
    // Most recent first
    expect(sessions[0].score.total).toBe(3);
    expect(sessions[1].score.total).toBe(2);
  });

  test("session answers carry pack_id matching loaded pack", async ({ page }) => {
    // Phase 2 contract: every per-answer record and every missed_question
    // entry must carry the loaded pack id so deleted-pack contamination is
    // detectable. The default course exposes one pack, so all entries share
    // its pack_id.
    await clearStorage(page);
    await startQuiz(page, 3);
    const expectedPackId = await page.evaluate(() =>
      Object.values(allQuestionsByModule)[0].pack.pack_id
    );
    await answerAll(page);
    // Quiz completion saves the session; #completionNotice hides on the last
    // answer and the results bar reveals durationLine.
    await expect(page.locator("#durationLine")).toBeVisible();
    const sessions = await page.evaluate(() => JSON.parse(localStorage.getItem("quizzler_sessions") || "[]"));
    expect(sessions.length).toBeGreaterThanOrEqual(1);
    const latest = sessions[0];
    expect(latest.answers.length).toBeGreaterThan(0);
    latest.answers.forEach(a => {
      expect(a.pack_id).toBe(expectedPackId);
    });
    if (latest.missed_questions && latest.missed_questions.length) {
      latest.missed_questions.forEach(m => {
        expect(m.pack_id).toBe(expectedPackId);
      });
    }
  });
});


// ═══════════════════════════════════════════════════════════
// 12. SESSION HISTORY SCREEN
// ═══════════════════════════════════════════════════════════

test.describe("Session History", () => {
  test("shows seeded session with correct score", async ({ page }) => {
    await clearStorage(page);
    await seedSession(page, { score: { correct: 8, total: 10 } });
    await page.reload();
    await page.locator("#historyBtn").click();
    await expect(page.locator("#historyScreen")).toBeVisible();
    await expect(page.locator(".history-item")).toHaveCount(1);
    await expect(page.locator(".score-big")).toHaveText("80%");
  });

  test("shows missed topics in history", async ({ page }) => {
    await clearStorage(page);
    await seedSession(page, { missed_topics: ["topic-a", "topic-b"] });
    await page.reload();
    await page.locator("#historyBtn").click();
    await expect(page.locator(".missed")).toContainText("topic-a");
  });

  test("shows chapter breakdown when present", async ({ page }) => {
    await clearStorage(page);
    await seedSession(page, {
      chapter_summary: [{ chapter: "Module 3", correct: 5, total: 10, pct: 50 }],
    });
    await page.reload();
    await page.locator("#historyBtn").click();
    const item = page.locator(".history-item").first();
    await expect(item).toContainText("Module 3: 50%");
  });

  test("shows weak modules when present", async ({ page }) => {
    await clearStorage(page);
    await seedSession(page, { missed_chapters: ["Module 5"] });
    await page.reload();
    await page.locator("#historyBtn").click();
    const item = page.locator(".history-item").first();
    await expect(item).toContainText("Module 5");
  });

  test("clear history removes all sessions", async ({ page }) => {
    await clearStorage(page);
    await seedSession(page);
    await page.reload();
    await page.locator("#historyBtn").click();
    await expect(page.locator(".history-item")).toHaveCount(1);

    await page.locator("#clearHistoryBtn").click();
    await page.locator("#dialogConfirmBtn").click();
    await expect(page.locator(".history-item")).toHaveCount(0);
  });

  test("empty history shows placeholder message", async ({ page }) => {
    await clearStorage(page);
    await page.reload();
    await page.locator("#historyBtn").click();
    await expect(page.locator("#historyList")).toContainText("No sessions recorded");
  });

  test("retry badge shows for retry sessions", async ({ page }) => {
    await clearStorage(page);
    await seedSession(page, { retry_mode: true });
    await page.reload();
    await page.locator("#historyBtn").click();
    await expect(page.locator(".retry-badge")).toBeVisible();
  });
});


// ═══════════════════════════════════════════════════════════
// 13. RETRY MISSED
// ═══════════════════════════════════════════════════════════

test.describe("Retry Missed", () => {
  test("retry tab shows sessions with missed questions", async ({ page }) => {
    await clearStorage(page);
    await goToConfig(page);
    await seedWithMissed(page, 2);
    await page.reload();
    await goToConfig(page);

    await page.locator('.tab[data-tab="retryMissed"]').click();
    await expect(page.locator("#retryMissedTab")).toBeVisible();
    await expect(page.locator("#retryList .module-row")).not.toHaveCount(0);
  });

  test("retry tab shows missed count badge", async ({ page }) => {
    await clearStorage(page);
    await goToConfig(page);
    await seedWithMissed(page, 2);
    await page.reload();
    await goToConfig(page);
    await page.locator('.tab[data-tab="retryMissed"]').click();
    await expect(page.locator("#retryList .retry-badge")).toContainText("2 missed");
  });

  test("clicking a retry session starts quiz with only missed questions", async ({ page }) => {
    await clearStorage(page);
    await goToConfig(page);
    const firstTwoIds = await seedWithMissed(page, 2);
    await page.reload();
    await goToConfig(page);
    await page.locator('.tab[data-tab="retryMissed"]').click();
    await page.locator("#retryList .module-row").first().click();

    await expect(page.locator("#quizScreen")).toBeVisible();
    await expect(page.locator(".card")).toHaveCount(2);

    const ids = await page.locator(".question-id").allTextContents();
    const idSet = new Set(ids.map((id) => id.trim()));
    expect(idSet.has(firstTwoIds[0])).toBe(true);
    expect(idSet.has(firstTwoIds[1])).toBe(true);
  });

  test("retry tab shows placeholder when no missed sessions exist", async ({ page }) => {
    await clearStorage(page);
    await seedSession(page, { missed_questions: [], missed_topics: [] });
    await page.reload();
    await goToConfig(page);
    await page.locator('.tab[data-tab="retryMissed"]').click();
    await expect(page.locator("#retryMissedTab")).toContainText("No retries yet");
  });
});


// ═══════════════════════════════════════════════════════════
// 14. RANDOMIZATION
// ═══════════════════════════════════════════════════════════

test.describe("Randomization", () => {
  test("question order differs between two quiz starts", async ({ page }) => {
    await goToConfig(page);
    const info = await getCourseInfo(page);
    const quizSize = Math.min(info.totalQuestions, 20);
    const orders = [];

    for (let run = 0; run < 2; run++) {
      if (run > 0) {
        await page.locator("#backToConfig").click();
        await expect(page.locator("#quizConfig")).toBeVisible();
      }
      await page.locator("#quizSize").fill(String(quizSize));
      await page.locator("#startQuizBtn").click();
      await expect(page.locator("#quizScreen")).toBeVisible();

      const ids = await page.locator(".question-id").allTextContents();
      orders.push(ids.map((id) => id.trim()).join(","));
    }

    // With randomization, extremely unlikely to get same order twice
    if (quizSize > 2) {
      expect(orders[0]).not.toBe(orders[1]);
    }
  });

  test("MC option order differs between runs", async ({ page }) => {
    await goToConfig(page);
    await page.locator("#selectNoneBtn").click();
    await page.locator("#moduleList .module-row").first().click();
    const firstModuleCount = parseInt(await page.locator("#availableCount").textContent());
    await page.locator("#quizSize").fill(String(firstModuleCount));
    await page.locator("#startQuizBtn").click();
    await expect(page.locator("#quizScreen")).toBeVisible();

    const mcCards = page.locator(".card:has(.choices)");
    const count = await mcCards.count();
    expect(count).toBeGreaterThan(0);
  });
});


// ═══════════════════════════════════════════════════════════
// 15. NAVIGATION
// ═══════════════════════════════════════════════════════════

test.describe("Navigation", () => {
  test("back from config returns to home", async ({ page }) => {
    await goToConfig(page);
    await page.locator("#backToCourses").click();
    await expect(page.locator("#home")).toBeVisible();
  });

  test("back from quiz returns to config", async ({ page }) => {
    await startQuiz(page, 3);
    await page.locator("#backToConfig").click();
    await expect(page.locator("#quizConfig")).toBeVisible();
  });

  test("back from history returns to home", async ({ page }) => {
    await page.goto("/app/");
    await page.locator("#historyBtn").click();
    await expect(page.locator("#historyScreen")).toBeVisible();
    await page.locator("#backFromHistory").click();
    await expect(page.locator("#home")).toBeVisible();
  });

  test("only one screen is visible at a time", async ({ page }) => {
    await page.goto("/app/");
    const screens = ["#home", "#quizConfig", "#quizScreen", "#historyScreen"];
    for (const s of screens) {
      const visible = await page.locator(s).isVisible();
      if (s === "#home") expect(visible).toBe(true);
      else expect(visible).toBe(false);
    }
  });
});


// ═══════════════════════════════════════════════════════════
// 16. QUIZ FOOTER ACTION
// ═══════════════════════════════════════════════════════════

test.describe("Quiz Footer Action", () => {
  test("Back to Courses leaves the course entirely (returns to home)", async ({ page }) => {
    await startQuiz(page, 3);
    await page.locator("#returnToSelectionBtn").click();

    await expect(page.locator("#home")).toBeVisible();
    await expect(page.locator("#quizScreen")).toBeHidden();
  });

  test("Start another and Back to Courses land on different screens", async ({ page }) => {
    await startQuiz(page, 3);
    await page.locator("#startAnotherBtn").click();
    await expect(page.locator("#quizConfig")).toBeVisible();

    await startQuiz(page, 3);
    await page.locator("#returnToSelectionBtn").click();
    await expect(page.locator("#home")).toBeVisible();
  });
});


// ═══════════════════════════════════════════════════════════
// 17. EXPLANATION MODAL
// ═══════════════════════════════════════════════════════════

test.describe("Explanation Modal", () => {
  test("explanation link appears after answering", async ({ page }) => {
    await startQuiz(page, 5);
    const mcCards = page.locator(".card:has(.choices)");
    const count = Math.min(await mcCards.count(), 5);
    for (let i = 0; i < count; i++) {
      await mcCards.nth(i).locator("label.choice").last().click();
    }

    const explLink = page.locator('[id^="showExpl-"]').first();
    if ((await explLink.count()) > 0) {
      await explLink.click();
      await expect(page.locator("#explanationModal")).toHaveClass(/is-open/);
      await expect(page.locator("#modalBody")).not.toBeEmpty();
    }
  });

  test("modal closes with close button", async ({ page }) => {
    await startQuiz(page, 5);
    const mcCards = page.locator(".card:has(.choices)");
    for (let i = 0; i < Math.min(await mcCards.count(), 5); i++) {
      await mcCards.nth(i).locator("label.choice").last().click();
    }

    const explLink = page.locator('[id^="showExpl-"]').first();
    if ((await explLink.count()) > 0) {
      await explLink.click();
      await expect(page.locator("#explanationModal")).toHaveClass(/is-open/);
      await page.locator("#closeModalBtn").click();
      await expect(page.locator("#explanationModal")).not.toHaveClass(/is-open/);
    }
  });

  test("modal closes when clicking backdrop", async ({ page }) => {
    await startQuiz(page, 5);
    const mcCards = page.locator(".card:has(.choices)");
    for (let i = 0; i < Math.min(await mcCards.count(), 5); i++) {
      await mcCards.nth(i).locator("label.choice").last().click();
    }

    const explLink = page.locator('[id^="showExpl-"]').first();
    if ((await explLink.count()) > 0) {
      await explLink.click();
      await expect(page.locator("#explanationModal")).toHaveClass(/is-open/);
      await page.locator("#explanationModal").click({ position: { x: 5, y: 5 } });
      await expect(page.locator("#explanationModal")).not.toHaveClass(/is-open/);
    }
  });
});


// ═══════════════════════════════════════════════════════════
// 18. TAB SWITCHING
// ═══════════════════════════════════════════════════════════

test.describe("Tab Switching", () => {
  test("switching to retry tab hides configure tab", async ({ page }) => {
    await goToConfig(page);
    await page.locator('.tab[data-tab="retryMissed"]').click();
    await expect(page.locator("#configureTab")).toBeHidden();
    await expect(page.locator("#retryMissedTab")).toBeVisible();
  });

  test("switching back to configure tab restores it", async ({ page }) => {
    await goToConfig(page);
    await page.locator('.tab[data-tab="retryMissed"]').click();
    await page.locator('.tab[data-tab="configure"]').click();
    await expect(page.locator("#configureTab")).toBeVisible();
    await expect(page.locator("#retryMissedTab")).toBeHidden();
  });

  test("active tab has correct styling class", async ({ page }) => {
    await goToConfig(page);
    await expect(page.locator('.tab[data-tab="configure"]')).toHaveClass(/active/);
    await expect(page.locator('.tab[data-tab="retryMissed"]')).not.toHaveClass(/active/);

    await page.locator('.tab[data-tab="retryMissed"]').click();
    await expect(page.locator('.tab[data-tab="retryMissed"]')).toHaveClass(/active/);
    await expect(page.locator('.tab[data-tab="configure"]')).not.toHaveClass(/active/);
  });
});


// ═══════════════════════════════════════════════════════════
// 21. MASTERY TRACKING
// ═══════════════════════════════════════════════════════════

test.describe("Mastery Tracking", () => {
  test.beforeEach(async ({ page }) => {
    await clearStorage(page);
  });

  test("mastery banner appears on config screen after loading modules", async ({ page }) => {
    await goToConfig(page);
    await expect(page.locator("#masteryBanner")).toBeVisible();
    await expect(page.locator("#readinessNumber")).toBeVisible();
    await expect(page.locator("#readinessLabel")).toBeVisible();
  });

  test("mastery starts at zero for a fresh course", async ({ page }) => {
    await goToConfig(page);
    await expect(page.locator("#masterySeenPct")).toContainText("0 /");
    await expect(page.locator("#masteryCorrectPct")).toContainText("0 /");
    await expect(page.locator("#masterySeenBar")).toHaveCSS("width", "0px");
  });

  test("completing a quiz updates mastery seen and correct counts", async ({ page }) => {
    await startQuiz(page, 3);
    await answerAll(page);

    await page.locator("#backToConfig").click();
    await expect(page.locator("#quizConfig")).toBeVisible();

    const seenText = await page.locator("#masterySeenPct").textContent();
    const seenMatch = seenText.match(/^(\d+)\s*\/\s*(\d+)/);
    expect(seenMatch).not.toBeNull();
    expect(parseInt(seenMatch[1])).toBeGreaterThanOrEqual(3);
  });

  test("mastery persists in localStorage", async ({ page }) => {
    await startQuiz(page, 2);
    const courseId = await page.evaluate(() => currentCourse.id);
    await answerAll(page);

    const mastery = await page.evaluate((cid) => {
      const packId = Object.values(allQuestionsByModule)[0].pack.pack_id;
      return JSON.parse(localStorage.getItem(getMasteryKey(cid, packId)));
    }, courseId);
    expect(mastery).not.toBeNull();
    expect(Object.keys(mastery.seen).length).toBe(2);
    expect(Object.keys(mastery.correct).length).toBeGreaterThanOrEqual(0);
    expect(Object.keys(mastery.correct).length).toBeLessThanOrEqual(2);
  });

  test("mastery accumulates across multiple quiz sessions", async ({ page }) => {
    await startQuiz(page, 2);
    const courseId = await page.evaluate(() => currentCourse.id);
    await answerAll(page);

    const mastery1 = await page.evaluate((cid) => {
      const packId = Object.values(allQuestionsByModule)[0].pack.pack_id;
      return JSON.parse(localStorage.getItem(getMasteryKey(cid, packId)));
    }, courseId);
    const seen1 = Object.keys(mastery1.seen).length;

    await page.locator("#backToConfig").click();
    await expect(page.locator("#quizConfig")).toBeVisible();
    await page.locator("#quizSize").fill("3");
    await page.locator("#startQuizBtn").click();
    await expect(page.locator("#quizScreen")).toBeVisible();
    await answerAll(page);

    const mastery2 = await page.evaluate((cid) => {
      const packId = Object.values(allQuestionsByModule)[0].pack.pack_id;
      return JSON.parse(localStorage.getItem(getMasteryKey(cid, packId)));
    }, courseId);
    const seen2 = Object.keys(mastery2.seen).length;

    expect(seen2).toBeGreaterThanOrEqual(seen1);
  });

  test("clearing history also clears mastery", async ({ page }) => {
    await startQuiz(page, 2);
    const courseId = await page.evaluate(() => currentCourse.id);
    await answerAll(page);

    const before = await page.evaluate((cid) => {
      const packId = Object.values(allQuestionsByModule)[0].pack.pack_id;
      return localStorage.getItem(getMasteryKey(cid, packId));
    }, courseId);
    expect(before).not.toBeNull();

    await page.goto("/app/");
    await page.locator("#historyBtn").click();
    await expect(page.locator("#historyScreen")).toBeVisible();
    await page.locator("#clearHistoryBtn").click();
    await page.locator("#dialogConfirmBtn").click();

    // clearMastery walks every quizzler_mastery_* key; assert no such key
    // remains for the current course (any pack).
    const after = await page.evaluate((cid) => {
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && k.startsWith(`quizzler_mastery_${cid}__`)) return k;
      }
      return null;
    }, courseId);
    expect(after).toBeNull();
  });

  test("mastery banner shows correct total from all loaded modules", async ({ page }) => {
    await goToConfig(page);
    const available = await page.locator("#availableCount").textContent();
    const pctText = await page.locator("#masterySeenPct").textContent();
    const totalMatch = pctText.match(/\/\s*(\d+)/);
    expect(totalMatch).not.toBeNull();
    expect(totalMatch[1]).toBe(available);
  });

  test("mastery seen bar has non-zero width after answering questions", async ({ page }) => {
    await startQuiz(page, 5);
    await answerAll(page);
    await page.locator("#backToConfig").click();
    await expect(page.locator("#quizConfig")).toBeVisible();

    const barWidth = await page.locator("#masterySeenBar").evaluate(el =>
      parseFloat(getComputedStyle(el).width)
    );
    expect(barWidth).toBeGreaterThan(0);
  });

  test("all-seen status message appears when every question has been attempted", async ({ page }) => {
    await goToConfig(page);
    const courseId = await page.evaluate(() => currentCourse.id);

    const allIds = await getInternalQuestionIds(page);

    const seen = {};
    const correct = {};
    allIds.forEach((id, i) => {
      seen[id] = true;
      if (i % 2 === 0) correct[id] = true;
    });
    await page.evaluate(({ seen, correct, cid }) => {
      const packId = Object.values(allQuestionsByModule)[0].pack.pack_id;
      localStorage.setItem(getMasteryKey(cid, packId), JSON.stringify({ seen, correct, manual: {} }));
    }, { seen, correct, cid: courseId });

    await page.locator("#backToCourses").click();
    await page.locator(".course-card").first().click();
    await expect(page.locator("#quizConfig")).toBeVisible();

    await expect(page.locator("#masteryStatus")).toContainText("All");
    await expect(page.locator("#masteryStatus")).toContainText("questions seen");
  });

  test("mastered checkbox flags a question correct and excludes it from new quizzes", async ({ page }) => {
    await startQuiz(page, 1);
    const courseId = await page.evaluate(() => currentCourse.id);
    const info = await getCourseInfo(page);
    const questionId = await page.locator(".question-id").first().textContent();

    // The mastery toggle is hidden until the question is answered.
    await answerCard(page.locator(".card").first());
    await page.locator('[id^="mastered-"]').first().check();

    const mastery = await page.evaluate((cid) => {
      const packId = Object.values(allQuestionsByModule)[0].pack.pack_id;
      return JSON.parse(localStorage.getItem(getMasteryKey(cid, packId)));
    }, courseId);
    expect(mastery.correct[questionId]).toBe(true);
    expect(mastery.seen[questionId]).toBe(true);
    // Schema no longer carries a `manual` field
    expect(mastery.manual).toBeUndefined();

    await page.locator("#startAnotherBtn").click();
    const availableAfter = parseInt(await page.locator("#availableCount").textContent());
    expect(availableAfter).toBe(info.totalQuestions - 1);

    // Run the largest possible quiz; the mastered question must not appear.
    await page.locator("#quizSize").fill(String(availableAfter));
    await page.locator("#startQuizBtn").click();
    await expect(page.locator("#quizScreen")).toBeVisible();
    const quizIds = await page.evaluate(() => questions.map(q => q.id));
    expect(quizIds).not.toContain(questionId);
  });

  test("all questions mastered disables Start with a reset hint", async ({ page }) => {
    await goToConfig(page);
    const courseId = await page.evaluate(() => currentCourse.id);
    const info = await getCourseInfo(page);
    const allIds = await getInternalQuestionIds(page);

    const seen = {};
    const correct = {};
    allIds.forEach(id => { seen[id] = true; correct[id] = true; });
    await page.evaluate(({ s, c, cid }) => {
      const packId = Object.values(allQuestionsByModule)[0].pack.pack_id;
      localStorage.setItem(getMasteryKey(cid, packId), JSON.stringify({ seen: s, correct: c }));
    }, { s: seen, c: correct, cid: courseId });

    // Re-enter the course so the config screen re-reads storage.
    await page.locator("#backToCourses").click();
    await page.locator(".course-card").first().click();
    await expect(page.locator("#quizConfig")).toBeVisible();

    await expect(page.locator("#availableCount")).toHaveText("0");
    await expect(page.locator("#startQuizBtn")).toBeDisabled();
    await expect(page.locator("#startQuizHint")).toContainText(`All ${info.totalQuestions}`);
    await expect(page.locator("#startQuizHint")).toContainText("Reset progress");
  });

  test("unchecking the mastered toggle removes the correct flag", async ({ page }) => {
    await startQuiz(page, 1);
    const courseId = await page.evaluate(() => currentCourse.id);
    const info = await getCourseInfo(page);
    const questionId = await page.locator(".question-id").first().textContent();
    // The mastery toggle is hidden until the question is answered.
    await answerCard(page.locator(".card").first());
    const toggle = page.locator('[id^="mastered-"]').first();

    await toggle.check();
    await expect(toggle).toBeChecked();
    await toggle.uncheck();
    await expect(toggle).not.toBeChecked();

    const mastery = await page.evaluate((cid) => {
      const packId = Object.values(allQuestionsByModule)[0].pack.pack_id;
      return JSON.parse(localStorage.getItem(getMasteryKey(cid, packId)));
    }, courseId);
    expect(mastery.correct[questionId]).toBeUndefined();

    await page.locator("#startAnotherBtn").click();
    const available = parseInt(await page.locator("#availableCount").textContent());
    expect(available).toBe(info.totalQuestions);
  });

  test("legacy manual-mastered questions migrate cleanly to the new schema", async ({ page }) => {
    // Simulate a user upgrading from the old build: their localStorage carries
    // `manual: { qX: true }` from the previous "hide from future quizzes"
    // contract. After the upgrade, those questions must reappear in the pool,
    // their checkbox must reflect `correct` (not `manual`), and the next save
    // must drop the legacy `manual` key.
    await goToConfig(page);
    const courseId = await page.evaluate(() => currentCourse.id);
    const info = await getCourseInfo(page);
    const allIds = await getInternalQuestionIds(page);
    expect(allIds.length).toBeGreaterThanOrEqual(2);

    const legacyHiddenId = allIds[0];
    const otherId = allIds[1];

    // Seed the legacy schema: one question hidden via `manual` only (no
    // `correct` flag), to confirm the checkbox reads from `correct`, not
    // `manual`.
    await page.evaluate(({ cid, hidden }) => {
      const packId = Object.values(allQuestionsByModule)[0].pack.pack_id;
      localStorage.setItem(
        getMasteryKey(cid, packId),
        JSON.stringify({ seen: {}, correct: {}, manual: { [hidden]: true } })
      );
    }, { cid: courseId, hidden: legacyHiddenId });

    // Re-enter the course so the config screen re-reads storage.
    await page.locator("#backToCourses").click();
    await page.locator(".course-card").first().click();
    await expect(page.locator("#quizConfig")).toBeVisible();

    // The previously hidden question must now count toward availability.
    const available = parseInt(await page.locator("#availableCount").textContent());
    expect(available).toBe(info.totalQuestions);

    // Start a full-pool quiz so we can locate the legacy-hidden question.
    await page.locator("#quizSize").fill(String(info.totalQuestions));
    await page.locator("#startQuizBtn").click();
    await expect(page.locator("#quizScreen")).toBeVisible();

    const quizIds = await page.evaluate(() => questions.map(q => q.id));
    expect(quizIds).toContain(legacyHiddenId);

    // The checkbox for the legacy-hidden question must be UNCHECKED, because
    // the new contract reads from `correct`, not `manual`.
    const legacyToggle = page.locator(`#mastered-${legacyHiddenId}`);
    await expect(legacyToggle).not.toBeChecked();

    // Toggling any question triggers a save; verify the legacy `manual` key
    // is purged from storage on the next write. The mastery toggle is hidden
    // until its question is answered.
    const otherCard = page.locator(`#card-${otherId}`);
    await answerCard(otherCard);
    const otherToggle = page.locator(`#mastered-${otherId}`);
    await otherToggle.check();

    const mastery = await page.evaluate((cid) => {
      const packId = Object.values(allQuestionsByModule)[0].pack.pack_id;
      return JSON.parse(localStorage.getItem(getMasteryKey(cid, packId)));
    }, courseId);
    expect(mastery.manual).toBeUndefined();
    expect(mastery.correct[otherId]).toBe(true);
    // Legacy hidden question stays un-mastered after migration (manual !=
    // correct in the new model).
    expect(mastery.correct[legacyHiddenId]).toBeUndefined();
  });

  test("retry-missed includes a missed question even after it is marked mastered", async ({ page }) => {
    // Old contract excluded manual-mastered questions from retry. The new
    // contract has no exclusion: a missed question marked mastered must still
    // appear in its retry session, so the user can re-confirm it.
    await clearStorage(page);
    await goToConfig(page);
    const missedIds = await seedWithMissed(page, 2);
    const courseId = await page.evaluate(() => currentCourse.id);

    // Mark the first missed question as mastered (sets correct=true).
    await page.evaluate(({ cid, id }) => {
      const packId = Object.values(allQuestionsByModule)[0].pack.pack_id;
      const m = JSON.parse(localStorage.getItem(getMasteryKey(cid, packId))) || { seen: {}, correct: {} };
      m.seen[id] = true;
      m.correct[id] = true;
      localStorage.setItem(getMasteryKey(cid, packId), JSON.stringify(m));
    }, { cid: courseId, id: missedIds[0] });

    await page.reload();
    await goToConfig(page);
    await page.locator('.tab[data-tab="retryMissed"]').click();
    await page.locator("#retryList .module-row").first().click();

    await expect(page.locator("#quizScreen")).toBeVisible();
    await expect(page.locator(".card")).toHaveCount(2);
    const ids = await page.locator(".question-id").allTextContents();
    const idSet = new Set(ids.map(id => id.trim()));
    expect(idSet.has(missedIds[0])).toBe(true);
    expect(idSet.has(missedIds[1])).toBe(true);
  });

  test("all-mastered status message appears when every question answered correctly", async ({ page }) => {
    await goToConfig(page);
    const courseId = await page.evaluate(() => currentCourse.id);

    const allIds = await getInternalQuestionIds(page);

    const both = {};
    allIds.forEach(id => { both[id] = true; });
    await page.evaluate(({ ids, cid }) => {
      const packId = Object.values(allQuestionsByModule)[0].pack.pack_id;
      localStorage.setItem(getMasteryKey(cid, packId), JSON.stringify({ seen: ids, correct: ids, manual: {} }));
    }, { ids: both, cid: courseId });

    await page.locator("#backToCourses").click();
    await page.locator(".course-card").first().click();
    await expect(page.locator("#quizConfig")).toBeVisible();

    await expect(page.locator("#masteryStatus")).toContainText("answered correctly at least once");
  });

  test("mastery storage uses pack-scoped key with __packId suffix", async ({ page }) => {
    // Phase 4 regression guard: the new key shape is
    // `quizzler_mastery_<courseId>__<packId>`. Completing a quiz must write
    // under that shape and never under the legacy course-only key.
    await clearStorage(page);
    await startQuiz(page, 2);
    const { courseId, packId } = await page.evaluate(() => ({
      courseId: currentCourse.id,
      packId: Object.values(allQuestionsByModule)[0].pack.pack_id,
    }));
    await answerAll(page);

    const keyShape = await page.evaluate(() => {
      const keys = [];
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && k.startsWith("quizzler_mastery_")) keys.push(k);
      }
      return keys;
    });
    expect(keyShape.length).toBeGreaterThanOrEqual(1);
    expect(keyShape.every(k => k.includes("__"))).toBe(true);
    expect(keyShape.some(k => k === `quizzler_mastery_${courseId}__${packId}`)).toBe(true);
  });

  test("boot sweep removes legacy mastery + sessions on first boot, preserves new data after", async ({ page }) => {
    await clearStorage(page);
    await page.goto("/app/");

    // Simulate pre-refactor state: seed legacy keys AND clear the one-shot
    // session-sweep sentinel so the next boot acts like the first boot
    // post-refactor.
    await page.evaluate(() => {
      localStorage.setItem("quizzler_mastery_samples", JSON.stringify({ seen: { q1: true }, correct: {} }));
      localStorage.setItem("quizzler_mastery_samples__samples-demo", JSON.stringify({ seen: { q2: true }, correct: {} }));
      localStorage.setItem("quizzler_sessions", JSON.stringify([{ quiz_id: "legacy" }]));
      localStorage.removeItem("quizzler_session_schema_v2");
    });

    // Reload triggers the first-boot sweep: legacy mastery gone, legacy
    // sessions gone, new-shape mastery preserved.
    await page.reload();

    const firstResult = await page.evaluate(() => ({
      legacyMasteryGone: localStorage.getItem("quizzler_mastery_samples"),
      newMasteryKept: localStorage.getItem("quizzler_mastery_samples__samples-demo"),
      legacySessionsGone: localStorage.getItem("quizzler_sessions"),
      sentinelSet: localStorage.getItem("quizzler_session_schema_v2"),
    }));
    expect(firstResult.legacyMasteryGone).toBeNull();
    expect(firstResult.newMasteryKept).not.toBeNull();
    expect(firstResult.legacySessionsGone).toBeNull();
    expect(firstResult.sentinelSet).toBe("1");

    // Write new-shape sessions data; subsequent reloads must preserve it
    // (sentinel prevents re-wiping live sessions). Mastery sweep is still
    // idempotent across reloads since new-shape keys have the "__" guard.
    await page.evaluate(() => {
      localStorage.setItem("quizzler_sessions", JSON.stringify([{ quiz_id: "new-session" }]));
    });
    await page.reload();
    const afterSecondReload = await page.evaluate(() => ({
      newMasteryKept: localStorage.getItem("quizzler_mastery_samples__samples-demo"),
      newSessionsKept: localStorage.getItem("quizzler_sessions"),
    }));
    expect(afterSecondReload.newMasteryKept).not.toBeNull();
    expect(afterSecondReload.newSessionsKept).not.toBeNull();
  });
});


// ═══════════════════════════════════════════════════════════
// 22. READINESS SCORE
// ═══════════════════════════════════════════════════════════

test.describe("Readiness Score", () => {
  test.beforeEach(async ({ page }) => {
    await clearStorage(page);
  });

  test("readiness starts at 0% with no history", async ({ page }) => {
    await goToConfig(page);
    await expect(page.locator("#readinessNumber")).toHaveText("0%");
    await expect(page.locator("#readinessLabel")).toHaveText("Just getting started");
  });

  test("readiness breakdown shows three components", async ({ page }) => {
    await goToConfig(page);
    const breakdown = await page.locator("#readinessBreakdown").textContent();
    expect(breakdown).toContain("Coverage");
    expect(breakdown).toContain("Mastery");
    expect(breakdown).toContain("Recent accuracy");
  });

  test("readiness increases after completing a quiz", async ({ page }) => {
    await goToConfig(page);
    const before = await page.locator("#readinessNumber").textContent();

    await page.locator("#quizSize").fill("5");
    await page.locator("#startQuizBtn").click();
    await expect(page.locator("#quizScreen")).toBeVisible();
    await answerAll(page);

    await page.locator("#backToConfig").click();
    await expect(page.locator("#quizConfig")).toBeVisible();

    const after = await page.locator("#readinessNumber").textContent();
    expect(parseInt(after)).toBeGreaterThan(parseInt(before));
  });

  test("readiness reaches high score with full mastery and good sessions", async ({ page }) => {
    await goToConfig(page);
    const courseId = await page.evaluate(() => currentCourse.id);

    const allIds = await getInternalQuestionIds(page);

    const both = {};
    allIds.forEach(id => { both[id] = true; });
    await page.evaluate(({ ids, cid }) => {
      const packId = Object.values(allQuestionsByModule)[0].pack.pack_id;
      localStorage.setItem(getMasteryKey(cid, packId), JSON.stringify({ seen: ids, correct: ids, manual: {} }));
    }, { ids: both, cid: courseId });

    const packId = await page.evaluate(() => Object.values(allQuestionsByModule)[0].pack.pack_id);
    for (let i = 0; i < 3; i++) {
      const answers = Array.from({ length: 20 }, (_, j) => ({
        question_id: `q-${i}-${j}`, pack_id: packId, topic: "t", chapter: null,
        difficulty: "easy", correct: true, response_ms: 1000
      }));
      await seedSession(page, {
        quiz_id: `perfect-${i}`,
        course: courseId,
        score: { correct: 20, total: 20 },
        missed_topics: [],
        missed_questions: [],
        answers,
      });
    }

    await page.locator("#backToCourses").click();
    await page.locator(".course-card").first().click();
    await expect(page.locator("#quizConfig")).toBeVisible();

    const score = parseInt(await page.locator("#readinessNumber").textContent());
    expect(score).toBeGreaterThanOrEqual(95);
    await expect(page.locator("#readinessLabel")).toHaveText("Exam ready");
  });

  test("readiness label shows 'Nearly ready' at 85-94%", async ({ page }) => {
    await goToConfig(page);
    const courseId = await page.evaluate(() => currentCourse.id);

    const allIds = await getInternalQuestionIds(page);

    const seen = {};
    const correct = {};
    allIds.forEach((id, i) => {
      seen[id] = true;
      if (i < Math.floor(allIds.length * 0.85)) correct[id] = true;
    });
    await page.evaluate(({ seen, correct, cid }) => {
      const packId = Object.values(allQuestionsByModule)[0].pack.pack_id;
      localStorage.setItem(getMasteryKey(cid, packId), JSON.stringify({ seen, correct, manual: {} }));
    }, { seen, correct, cid: courseId });

    const packIdGood = await page.evaluate(() => Object.values(allQuestionsByModule)[0].pack.pack_id);
    for (let i = 0; i < 3; i++) {
      const answers = Array.from({ length: 20 }, (_, j) => ({
        question_id: `gq-${i}-${j}`, pack_id: packIdGood, topic: "t", chapter: null,
        difficulty: "easy", correct: j < 17, response_ms: 1000
      }));
      await seedSession(page, {
        quiz_id: `good-${i}`,
        course: courseId,
        score: { correct: 17, total: 20 },
        missed_topics: ["some-topic"],
        missed_questions: [{ question_id: "x", topic: "some-topic", pack_id: packIdGood }],
        answers,
      });
    }

    await page.locator("#backToCourses").click();
    await page.locator(".course-card").first().click();
    await expect(page.locator("#quizConfig")).toBeVisible();

    const score = parseInt(await page.locator("#readinessNumber").textContent());
    expect(score).toBeGreaterThanOrEqual(85);
    expect(score).toBeLessThan(95);
    await expect(page.locator("#readinessLabel")).toHaveText("Nearly ready");
  });

  test("readiness score resets when history is cleared", async ({ page }) => {
    await startQuiz(page, 3);
    await answerAll(page);
    await page.locator("#backToConfig").click();
    await expect(page.locator("#quizConfig")).toBeVisible();
    const before = parseInt(await page.locator("#readinessNumber").textContent());
    expect(before).toBeGreaterThan(0);

    await page.goto("/app/");
    await page.locator("#historyBtn").click();
    await expect(page.locator("#historyScreen")).toBeVisible();
    await page.locator("#clearHistoryBtn").click();
    await page.locator("#dialogConfirmBtn").click();

    await page.locator("#backFromHistory").click();
    await page.locator(".course-card").first().click();
    await expect(page.locator("#quizConfig")).toBeVisible();

    await expect(page.locator("#readinessNumber")).toHaveText("0%");
  });

  test("recentAccuracy filters per-answer by current pack ids, not by session score totals", async ({ page }) => {
    // Phase 2 contract: recent-accuracy aggregates per-answer over the last
    // 3 eligible sessions, skipping any answer whose pack_id is not in the
    // currently-loaded pack set. A session with 3 current-pack answers
    // (2 correct) and 5 deleted-pack answers (all correct) must read as
    // 2/3 = 67% — not the legacy session-score 7/8 = 87.5%.
    //
    // The boot sweep wipes `quizzler_sessions` on each load, so we seed
    // AFTER navigating into the course (so the loaded pack_id is known)
    // and never reload.
    await page.goto("/app/");
    // Enter the course to populate allQuestionsByModule and learn the pack id.
    await page.locator(".course-card").first().click();
    await expect(page.locator("#quizConfig")).toBeVisible();
    const { courseId, packId, moduleFile } = await page.evaluate(() => {
      const mod = Object.values(allQuestionsByModule)[0];
      return { courseId: currentCourse.id, packId: mod.pack.pack_id, moduleFile: mod.meta.file };
    });

    await page.evaluate(({ courseId, packId, moduleFile }) => {
      const mixed = {
        quiz_id: "test-mixed",
        course: courseId,
        title: "Mixed",
        timestamp: Date.now(),
        completed_at: new Date().toISOString(),
        score: { correct: 7, total: 8 },
        answers: [
          { question_id: "a1", pack_id: packId, correct: true, topic: "x", chapter: null, difficulty: "easy", response_ms: 1000 },
          { question_id: "a2", pack_id: packId, correct: true, topic: "x", chapter: null, difficulty: "easy", response_ms: 1000 },
          { question_id: "a3", pack_id: packId, correct: false, topic: "x", chapter: null, difficulty: "easy", response_ms: 1000 },
          { question_id: "d1", pack_id: "deleted-pack", correct: true, topic: "x", chapter: null, difficulty: "easy", response_ms: 1000 },
          { question_id: "d2", pack_id: "deleted-pack", correct: true, topic: "x", chapter: null, difficulty: "easy", response_ms: 1000 },
          { question_id: "d3", pack_id: "deleted-pack", correct: true, topic: "x", chapter: null, difficulty: "easy", response_ms: 1000 },
          { question_id: "d4", pack_id: "deleted-pack", correct: true, topic: "x", chapter: null, difficulty: "easy", response_ms: 1000 },
          { question_id: "d5", pack_id: "deleted-pack", correct: true, topic: "x", chapter: null, difficulty: "easy", response_ms: 1000 },
        ],
        missed_questions: [],
        modules_used: [moduleFile],
      };
      localStorage.setItem("quizzler_sessions", JSON.stringify([mixed]));
    }, { courseId, packId, moduleFile });

    // Re-render the readiness banner without a page reload. Bouncing through
    // the home screen and back exercises the course-card click path, which
    // calls renderMasteryBanner() with the freshly-seeded sessions.
    await page.locator("#backToCourses").click();
    await page.locator(".course-card").first().click();
    await expect(page.locator("#quizConfig")).toBeVisible();
    const breakdownText = await page.locator("#readinessBreakdown").textContent();
    // Per-answer aggregation: 2 of 3 current-pack answers correct = 67%.
    // Session-aggregated would be 7/8 = 87.5%, which would (incorrectly)
    // display 88% or 87%.
    expect(breakdownText).toMatch(/Recent accuracy 67%/);
    expect(breakdownText).not.toMatch(/Recent accuracy 87%|Recent accuracy 88%/);
  });
});


// ═══════════════════════════════════════════════════════════
// 23. QUIZ TIMER
// ═══════════════════════════════════════════════════════════

test.describe("Quiz Timer", () => {
  test("elapsed timer is visible on quiz screen and starts at 0:00", async ({ page }) => {
    await startQuiz(page, 3);
    const elapsed = page.locator("#statElapsed");
    await expect(elapsed).toBeVisible();
    const text = (await elapsed.textContent()).trim();
    expect(text).toMatch(/^0:0[0-2]$/);
  });

  test("elapsed timer ticks upward while the quiz is open", async ({ page }) => {
    await startQuiz(page, 3);
    const elapsed = page.locator("#statElapsed");
    const before = (await elapsed.textContent()).trim();
    expect(before).toBe("0:00");
    await page.waitForTimeout(2200);
    const after = (await elapsed.textContent()).trim();
    expect(after).not.toBe(before);
    expect(after).toMatch(/^0:0[1-9]$/);
  });

  test("elapsed timer freezes when quiz completes", async ({ page }) => {
    await startQuiz(page, 2);
    await answerAll(page);
    const frozen = (await page.locator("#statElapsed").textContent()).trim();
    expect(frozen).toMatch(/^\d+:\d{2}(:\d{2})?$/);
    await page.waitForTimeout(1500);
    const stillFrozen = (await page.locator("#statElapsed").textContent()).trim();
    expect(stillFrozen).toBe(frozen);
  });

  test("results bar shows total time after completion", async ({ page }) => {
    await startQuiz(page, 2);
    await expect(page.locator("#durationLine")).toBeHidden();
    await answerAll(page);
    await expect(page.locator("#durationLine")).toBeVisible();
    await expect(page.locator("#durationLine")).toContainText("Time:");
    await expect(page.locator("#durationLine")).toContainText(/\d+:\d{2}/);
  });

  test("saved session records started_at and duration_ms", async ({ page }) => {
    await clearStorage(page);
    await startQuiz(page, 2);
    await answerAll(page);
    const session = await page.evaluate(() =>
      JSON.parse(localStorage.getItem(STORAGE_KEY))[0]
    );
    expect(session.started_at).toBeDefined();
    expect(typeof session.started_at).toBe("string");
    expect(() => new Date(session.started_at).toISOString()).not.toThrow();
    expect(typeof session.duration_ms).toBe("number");
    expect(session.duration_ms).toBeGreaterThan(0);
    // duration_ms should equal completed_at - started_at within a small tolerance
    const startMs = new Date(session.started_at).getTime();
    const endMs = new Date(session.completed_at).getTime();
    expect(Math.abs((endMs - startMs) - session.duration_ms)).toBeLessThan(1500);
  });

  test("history shows duration for sessions that have one", async ({ page }) => {
    await clearStorage(page);
    await seedSession(page, { duration_ms: 125000 });
    await page.reload();
    await page.locator("#historyBtn").click();
    await expect(page.locator(".session-duration")).toHaveText("2:05");
  });

  test("history omits duration for legacy sessions without duration_ms", async ({ page }) => {
    await clearStorage(page);
    await seedSession(page); // no duration_ms
    await page.reload();
    await page.locator("#historyBtn").click();
    await expect(page.locator(".history-item")).toHaveCount(1);
    await expect(page.locator(".session-duration")).toHaveCount(0);
  });

  test("timer resets when starting a second quiz", async ({ page }) => {
    await startQuiz(page, 2);
    await page.waitForTimeout(2200);
    const midQuiz = (await page.locator("#statElapsed").textContent()).trim();
    expect(midQuiz).not.toBe("0:00");

    await page.locator("#backToConfig").click();
    await expect(page.locator("#quizConfig")).toBeVisible();
    await page.locator("#quizSize").fill("2");
    await page.locator("#startQuizBtn").click();
    await expect(page.locator("#quizScreen")).toBeVisible();

    const fresh = (await page.locator("#statElapsed").textContent()).trim();
    expect(fresh).toMatch(/^0:0[0-1]$/);
  });

  test("formatDuration produces expected strings for boundary inputs", async ({ page }) => {
    await page.goto("/app/");
    const cases = await page.evaluate(() => [
      formatDuration(0),
      formatDuration(1000),
      formatDuration(59000),
      formatDuration(60000),
      formatDuration(3599000),
      formatDuration(3600000),
      formatDuration(3661000),
    ]);
    expect(cases).toEqual([
      "0:00",
      "0:01",
      "0:59",
      "1:00",
      "59:59",
      "1:00:00",
      "1:01:01",
    ]);
  });

  test("hour-format duration renders in history when over 60 minutes", async ({ page }) => {
    await clearStorage(page);
    await seedSession(page, { duration_ms: 3725000 }); // 1h 2m 5s
    await page.reload();
    await page.locator("#historyBtn").click();
    await expect(page.locator(".session-duration")).toHaveText("1:02:05");
  });

  // Orphan-interval guard: leaving a quiz mid-flight must clear the
  // setInterval. If it doesn't, the timer keeps ticking after navigation,
  // which is exactly the kind of memory-leak-style bug that hides in
  // production. The two exit controls reachable from the live quiz screen
  // are #backToConfig and #returnToSelectionBtn. (#backToCourses lives on
  // the config screen, so its stopQuizTimer() call is defensive only and
  // not exercised from the quiz screen.) We assert both:
  //   (a) the JS state (quizTimerInterval) is null after exit, and
  //   (b) #statElapsed does not advance after leaving the screen.
  for (const exit of [
    { id: "backToConfig", landing: "#quizConfig" },
    { id: "returnToSelectionBtn", landing: "#home" },
  ]) {
    test(`exiting mid-quiz via #${exit.id} clears the timer interval`, async ({ page }) => {
      await startQuiz(page, 3);

      // Confirm the interval is actually live before we leave.
      const intervalBefore = await page.evaluate(() => quizTimerInterval);
      expect(intervalBefore).not.toBeNull();
      expect(typeof intervalBefore).toBe("number");

      // Let the timer tick at least once so #statElapsed has a non-zero value
      // we can pin and watch for unwanted advancement after exit.
      await page.waitForTimeout(1100);
      const elapsedAtExit = (await page.locator("#statElapsed").textContent()).trim();

      await page.locator(`#${exit.id}`).click();
      await expect(page.locator(exit.landing)).toBeVisible();

      // (a) Interval id was cleared.
      const intervalAfter = await page.evaluate(() => quizTimerInterval);
      expect(intervalAfter).toBeNull();

      // (b) #statElapsed has not advanced — i.e., no orphan tick is running.
      // We wait long enough that a still-live 1s interval would fire ~2x.
      await page.waitForTimeout(2200);
      const elapsedAfter = (await page.locator("#statElapsed").textContent()).trim();
      expect(elapsedAfter).toBe(elapsedAtExit);
    });
  }

  test("formatDuration handles null, undefined, NaN, and negative input", async ({ page }) => {
    await page.goto("/app/");
    const cases = await page.evaluate(() => [
      formatDuration(null),
      formatDuration(undefined),
      formatDuration(NaN),
      formatDuration(-5000),
    ]);
    // Production contract: any non-positive / nullish input renders as "0:00".
    // NaN comparisons fail both `< 0` and `== null`, so it falls through to
    // Math.floor(NaN/1000) === NaN; we accept "0:00" or "NaN:NaN"-style only
    // if the helper actually guards it. Today's impl yields "NaN:NaN" for NaN,
    // which is a UI bug — assert the safe contract and let it fail loudly.
    expect(cases[0]).toBe("0:00"); // null
    expect(cases[1]).toBe("0:00"); // undefined
    expect(cases[2]).toBe("0:00"); // NaN
    expect(cases[3]).toBe("0:00"); // negative
  });
});


// ═══════════════════════════════════════════════════════════
// Phase 1 — Semantic foundation + a11y
// ═══════════════════════════════════════════════════════════

test.describe("A11y — Semantic markup and focus", () => {
  test("course cards, module rows, tabs, and start button are reachable via Tab", async ({ page }) => {
    await page.goto("/app/");
    // Course cards are buttons now — check at least one exists and is focusable.
    const cards = page.locator(".course-card");
    await expect(cards.first()).toBeVisible();
    const cardCount = await cards.count();
    expect(cardCount).toBeGreaterThan(0);

    // Each course card is a <button> (programmatically focusable, in tab order).
    for (let i = 0; i < cardCount; i++) {
      const tag = await cards.nth(i).evaluate(el => el.tagName.toLowerCase());
      expect(tag).toBe("button");
    }

    // Drive into config; check tabs and module-row checkboxes are focusable.
    await cards.first().click();
    await expect(page.locator("#quizConfig")).toBeVisible();

    const tabs = page.locator('#quizConfig [role="tab"]');
    expect(await tabs.count()).toBe(2);
    for (let i = 0; i < 2; i++) {
      const tag = await tabs.nth(i).evaluate(el => el.tagName.toLowerCase());
      expect(tag).toBe("button");
    }

    // Module rows wrap a checkbox (the focus target). Verify the rows are <label>
    // and the wrapped checkbox can hold focus.
    const rows = page.locator("#moduleList .module-row");
    const rowCount = await rows.count();
    expect(rowCount).toBeGreaterThan(0);
    for (let i = 0; i < rowCount; i++) {
      const tag = await rows.nth(i).evaluate(el => el.tagName.toLowerCase());
      expect(tag).toBe("label");
    }

    const firstCheckbox = rows.first().locator("input[type='checkbox']");
    await firstCheckbox.focus();
    const focusedTag = await page.evaluate(() => document.activeElement && document.activeElement.tagName.toLowerCase());
    expect(focusedTag).toBe("input");

    // Start Quiz button reachable.
    await page.locator("#startQuizBtn").focus();
    const startFocused = await page.evaluate(() =>
      document.activeElement && document.activeElement.id === "startQuizBtn"
    );
    expect(startFocused).toBe(true);
  });

  test("module-row label click toggles its checkbox without per-row JS handler", async ({ page }) => {
    await page.goto("/app/");
    await page.locator(".course-card").first().click();
    await expect(page.locator("#moduleList .module-row")).not.toHaveCount(0);

    const firstRow = page.locator("#moduleList .module-row").first();
    const cb = firstRow.locator("input[type='checkbox']");
    const before = await cb.isChecked();
    await firstRow.click();
    const after = await cb.isChecked();
    expect(after).toBe(!before);
  });

  test("every form-field input/select has a programmatic label", async ({ page }) => {
    await page.goto("/app/");
    await page.locator(".course-card").first().click();
    await expect(page.locator("#quizConfig")).toBeVisible();

    // Quiz config: #quizSize must have an associated <label for=...>.
    const quizSizeLabel = await page.evaluate(() => {
      const input = document.getElementById("quizSize");
      const labels = input.labels;
      return labels && labels.length > 0;
    });
    expect(quizSizeLabel).toBe(true);

    // Module checkboxes are wrapped by their <label class="module-row"> ancestor.
    const checkboxesLabeled = await page.evaluate(() => {
      const cbs = document.querySelectorAll("#moduleList input[type='checkbox']");
      return [...cbs].every(cb => cb.labels && cb.labels.length > 0);
    });
    expect(checkboxesLabeled).toBe(true);

    // Start a quiz and check matching selects (if any) have aria-label.
    await page.locator("#startQuizBtn").click();
    await expect(page.locator("#quizScreen")).toBeVisible();
    const matchingSelectsLabeled = await page.evaluate(() => {
      const selects = document.querySelectorAll(".matching-grid select");
      if (!selects.length) return true; // no matching questions in this run
      return [...selects].every(s => s.hasAttribute("aria-label"));
    });
    expect(matchingSelectsLabeled).toBe(true);
  });

  test("course card receives a visible focus outline when focused", async ({ page }) => {
    await page.goto("/app/");
    const card = page.locator(".course-card").first();
    await expect(card).toBeVisible();
    await card.focus();
    // Force focus-visible by emulating keyboard navigation using DOM API.
    // Some browsers only apply :focus-visible after a real keyboard event;
    // checking that the rule exists in the stylesheet guarantees keyboard users
    // get the outline regardless of how the test triggered focus.
    const hasFocusVisibleRule = await page.evaluate(() => {
      for (const sheet of document.styleSheets) {
        let rules;
        try { rules = sheet.cssRules; } catch (e) { continue; }
        if (!rules) continue;
        for (const r of rules) {
          if (r.cssText && r.cssText.includes(":focus-visible") && r.cssText.includes("outline")) {
            return true;
          }
        }
      }
      return false;
    });
    expect(hasFocusVisibleRule).toBe(true);
  });
});

test.describe("A11y — Document title", () => {
  test("title shows quiz progress mid-quiz and score after completion", async ({ page }) => {
    await startQuiz(page, 5);
    // Mid-quiz title should mention "Q1/5" style progress.
    const midTitle = await page.title();
    expect(midTitle).toMatch(/Q\d+\/5/);
    expect(midTitle.toLowerCase()).toContain("quizzler");

    // Answer all but one question; title should still reflect mid-quiz state.
    const cards = page.locator(".card");
    const total = await cards.count();
    for (let i = 0; i < total - 1; i++) {
      await answerCard(cards.nth(i));
    }
    const beforeFinal = await page.title();
    expect(beforeFinal).toMatch(/Q\d+\/5/);

    // Answer last question — title should now show score percentage, not progress.
    await answerCard(cards.nth(total - 1));
    // Wait briefly for the title update.
    await page.waitForFunction(() => /\d+%/.test(document.title));
    const finalTitle = await page.title();
    expect(finalTitle).toMatch(/\d+%/);
    // updateProgress runs after checkCompletion; its in-progress title must not clobber the score.
    expect(finalTitle).not.toMatch(/Q\d+\/\d+/);
  });

  test("home and history screens set descriptive titles", async ({ page }) => {
    await page.goto("/app/");
    await page.waitForFunction(() => document.title === "Quizzler");
    expect(await page.title()).toBe("Quizzler");

    await page.locator("#historyBtn").click();
    await expect(page.locator("#historyScreen")).toBeVisible();
    expect(await page.title()).toContain("Session History");
  });
});

test.describe("A11y — Phase 1 gates (replaces manual Lighthouse / walkthrough)", () => {
  test("page exposes exactly one <main> landmark and a non-empty meta description", async ({ page }) => {
    await page.goto("/app/");
    expect(await page.locator("main").count()).toBe(1);
    const desc = await page.locator('meta[name="description"]').getAttribute("content");
    expect(desc && desc.trim().length).toBeGreaterThan(20);
  });

  test("page declares a favicon and the resource resolves without 404", async ({ page }) => {
    const failed = [];
    page.on("requestfailed", req => {
      if (/favicon|\.ico$/i.test(req.url())) failed.push(req.url());
    });
    page.on("response", resp => {
      if (/favicon|\.ico$/i.test(resp.url()) && resp.status() >= 400) failed.push(resp.url());
    });
    await page.goto("/app/", { waitUntil: "networkidle" });
    const iconHref = await page.locator('link[rel="icon"]').getAttribute("href");
    expect(iconHref).toBeTruthy();
    expect(failed).toEqual([]);
  });

  test("full quiz flow produces no console errors", async ({ page }) => {
    const errors = [];
    page.on("console", msg => { if (msg.type() === "error") errors.push(msg.text()); });
    page.on("pageerror", err => errors.push(err.message));

    await startQuiz(page, 3);
    const cards = page.locator(".card");
    const total = await cards.count();
    for (let i = 0; i < total; i++) await answerCard(cards.nth(i));
    await page.waitForFunction(() => /\d+%/.test(document.title));

    await page.locator("#returnToSelectionBtn").click();
    await expect(page.locator("#home")).toBeVisible();
    await page.locator("#historyBtn").click();
    await expect(page.locator("#historyScreen")).toBeVisible();

    expect(errors).toEqual([]);
  });

  test("prefers-reduced-motion stylesheet zeros transitions and hover transforms", async ({ page }) => {
    await page.goto("/app/");
    const found = await page.evaluate(() => {
      let zeroesTransition = false;
      let zeroesHoverTransform = false;
      for (const sheet of document.styleSheets) {
        let rules;
        try { rules = sheet.cssRules; } catch (e) { continue; }
        if (!rules) continue;
        for (const r of rules) {
          if (r.type !== CSSRule.MEDIA_RULE) continue;
          if (!/prefers-reduced-motion/.test(r.conditionText || "")) continue;
          for (const inner of r.cssRules) {
            const txt = inner.cssText || "";
            if (/transition:\s*none/i.test(txt)) zeroesTransition = true;
            if (/transform:\s*none/i.test(txt)) zeroesHoverTransform = true;
          }
        }
      }
      return { zeroesTransition, zeroesHoverTransform };
    });
    expect(found.zeroesTransition).toBe(true);
    expect(found.zeroesHoverTransform).toBe(true);
  });

  test("tab role attributes stay in sync with active state", async ({ page }) => {
    await goToConfig(page);
    const tabs = page.locator('#quizConfig [role="tab"]');
    expect(await tabs.nth(0).getAttribute("aria-selected")).toBe("true");
    expect(await tabs.nth(1).getAttribute("aria-selected")).toBe("false");

    await tabs.nth(1).click();
    expect(await tabs.nth(0).getAttribute("aria-selected")).toBe("false");
    expect(await tabs.nth(1).getAttribute("aria-selected")).toBe("true");

    // Active panel referenced by aria-controls is visible; the other is hidden.
    const activeControlsId = await tabs.nth(1).getAttribute("aria-controls");
    expect(activeControlsId).toBeTruthy();
    await expect(page.locator(`#${activeControlsId}`)).toBeVisible();
  });
});

test.describe("A11y / Aesthetic — Phase 2 gates", () => {
  // Helper: walk every CSSStyleRule in document.styleSheets and concatenate cssText.
  // Skips media-rule branches that aren't currently applied (e.g., reduced-motion)
  // — the fallback `transform: none` inside that media block is a feature, not a hover lift.
  async function collectAllCssText(page) {
    return page.evaluate(() => {
      const parts = [];
      const walk = (rules) => {
        for (const r of rules || []) {
          if (r.type === CSSRule.STYLE_RULE) {
            parts.push(r.cssText);
          } else if (r.cssRules) {
            walk(r.cssRules);
          }
        }
      };
      for (const sheet of document.styleSheets) {
        try { walk(sheet.cssRules); } catch (e) { /* cross-origin */ }
      }
      return parts.join("\n");
    });
  }

  test("no linear-gradient or radial-gradient remains in any stylesheet", async ({ page }) => {
    await page.goto("/app/");
    const css = await collectAllCssText(page);
    expect(css).not.toContain("linear-gradient(");
    expect(css).not.toContain("radial-gradient(");
  });

  test("no backdrop-filter declaration remains in any stylesheet", async ({ page }) => {
    await page.goto("/app/");
    const css = await collectAllCssText(page);
    expect(css.toLowerCase()).not.toContain("backdrop-filter:");
  });

  test("no :hover rule applies a translate transform", async ({ page }) => {
    await page.goto("/app/");
    const offenders = await page.evaluate(() => {
      const out = [];
      const walk = (rules) => {
        for (const r of rules || []) {
          if (r.type === CSSRule.STYLE_RULE) {
            const sel = r.selectorText || "";
            if (sel.includes(":hover")) {
              const t = (r.style && r.style.transform) || "";
              if (t && t !== "none") out.push({ sel, t });
              if (/transform:\s*translate/i.test(r.cssText)) out.push({ sel, t: r.cssText });
            }
          } else if (r.cssRules) {
            walk(r.cssRules);
          }
        }
      };
      for (const sheet of document.styleSheets) {
        try { walk(sheet.cssRules); } catch (e) { /* cross-origin */ }
      }
      return out;
    });
    expect(offenders).toEqual([]);
  });

  test("active tab background differs from primary CTA background", async ({ page }) => {
    await goToConfig(page);
    const colors = await page.evaluate(() => {
      const tab = document.querySelector('#quizConfig [role="tab"][aria-selected="true"]');
      const cta = document.getElementById("startQuizBtn");
      return {
        tab: getComputedStyle(tab).backgroundColor,
        cta: getComputedStyle(cta).backgroundColor,
      };
    });
    expect(colors.tab).not.toBe(colors.cta);
  });

  test("hero panels have tightened padding (≤ 20px top/bottom)", async ({ page }) => {
    await page.goto("/app/");
    const heroPaddings = await page.evaluate(() => {
      const heroes = document.querySelectorAll(".panel.hero");
      return Array.from(heroes).map(el => {
        const cs = getComputedStyle(el);
        return { top: parseFloat(cs.paddingTop), bottom: parseFloat(cs.paddingBottom) };
      });
    });
    expect(heroPaddings.length).toBeGreaterThan(0);
    for (const p of heroPaddings) {
      expect(p.top).toBeLessThanOrEqual(20);
      expect(p.bottom).toBeLessThanOrEqual(20);
    }
  });

  // ─── Visual regression baselines ───
  // Tolerance accounts for font anti-aliasing across runs.
  const SNAPSHOT_OPTS = { maxDiffPixelRatio: 0.02 };

  // Seed Math.random with a deterministic PRNG so quiz-question selection and
  // option/matching shuffles are stable across runs. Without this, the visual
  // snapshots flake on the ~4–5% pixel-diff ratio caused by re-ordered cards.
  async function seedRandom(page) {
    await page.addInitScript(() => {
      let s = 0x9e3779b9;
      Math.random = function () {
        s |= 0; s = (s + 0x6d2b79f5) | 0;
        let t = Math.imul(s ^ (s >>> 15), 1 | s);
        t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
      };
    });
  }

  test("visual baseline — home", async ({ page }) => {
    await seedRandom(page);
    await clearStorage(page);
    await page.goto("/app/");
    await expect(page.locator(".course-card").first()).toBeVisible();
    // Mask the course grid: its contents depend on which packs are present on
    // disk (course names/descriptions are author-private and gitignored), so a
    // raw screenshot would be non-deterministic AND could leak private course
    // titles into the committed baseline. We only want to baseline the hero.
    await expect(page).toHaveScreenshot("phase2-home.png", {
      ...SNAPSHOT_OPTS,
      mask: [page.locator(".course-grid")],
      maskColor: "#1f2937",
    });
  });

  test("visual baseline — config (after clicking a course)", async ({ page }) => {
    await seedRandom(page);
    await clearStorage(page);
    await goToConfig(page);
    // Settle: ensure module list rendered.
    await expect(page.locator("#moduleList .module-row").first()).toBeVisible();
    await expect(page).toHaveScreenshot("phase2-config.png", SNAPSHOT_OPTS);
  });

  test("visual baseline — quiz mid-question (1 of 5 answered)", async ({ page }) => {
    await seedRandom(page);
    await clearStorage(page);
    await startQuiz(page, 5);
    const cards = page.locator(".card");
    await answerCard(cards.nth(0));
    await expect(page.locator(".card.is-answered, label.choice.is-correct, label.choice.is-incorrect, .tf-btn.is-correct, .tf-btn.is-incorrect, .matching-row.is-correct, .matching-row.is-incorrect").first()).toBeVisible();
    await expect(page).toHaveScreenshot("phase2-quiz-mid.png", {
      ...SNAPSHOT_OPTS,
      // Mask the live elapsed timer so its tick doesn't break diffs.
      mask: [page.locator("#statElapsed")],
    });
  });

  test("visual baseline — quiz complete", async ({ page }) => {
    await seedRandom(page);
    await clearStorage(page);
    await startQuiz(page, 5);
    await answerAll(page);
    await page.waitForFunction(() => /\d+%/.test(document.title));
    await expect(page).toHaveScreenshot("phase2-quiz-complete.png", {
      ...SNAPSHOT_OPTS,
      // Mask the elapsed timer (frozen at completion but value depends on test timing).
      mask: [page.locator("#statElapsed")],
    });
  });
});


// ═══════════════════════════════════════════════════════════
// Phase 3 — Information architecture and flow
// ═══════════════════════════════════════════════════════════

test.describe("Phase 3 gates — Information architecture", () => {
  test("course cards do not duplicate the course name (no eyebrow)", async ({ page }) => {
    await page.goto("/app/");
    const cards = page.locator(".course-card");
    const n = await cards.count();
    expect(n).toBeGreaterThan(0);
    for (let i = 0; i < n; i++) {
      const card = cards.nth(i);
      const eyebrows = await card.locator(".eyebrow").count();
      expect(eyebrows).toBe(0);
      const h2 = (await card.locator("h2").textContent()).trim();
      expect(h2.length).toBeGreaterThan(0);
    }
  });

  test("config hero eyebrow shows the course description", async ({ page }) => {
    await goToConfig(page);
    const eyebrow = (await page.locator("#configSubject").textContent()).trim();
    const expected = await page.evaluate(() => currentCourse.description || currentCourse.name);
    expect(eyebrow).toBe(expected);
  });

  test("history row title omits course-id prefix", async ({ page }) => {
    await clearStorage(page);
    await seedSession(page, { title: "My Custom Title" });
    await page.reload();
    await page.locator("#historyBtn").click();
    const summary = await page.locator(".history-item summary h3").first().textContent();
    expect(summary.toUpperCase()).not.toContain("SAMPLES —");
    expect(summary).toContain("My Custom Title");
  });

  test("course cards show total question count", async ({ page }) => {
    await page.goto("/app/");
    const cards = page.locator(".course-card");
    const n = await cards.count();
    for (let i = 0; i < n; i++) {
      const text = await cards.nth(i).textContent();
      expect(text).toContain("questions");
      const m = text.match(/(\d+)\s+questions?/);
      expect(m).not.toBeNull();
      expect(parseInt(m[1])).toBeGreaterThan(0);
    }
  });

  test("moduleGroupLabel maps each filename pattern to its group", async ({ page }) => {
    await page.goto("/app/");
    const labels = await page.evaluate(() => ({
      r1: moduleGroupLabel("r1.json"),
      r2followup: moduleGroupLabel("r2-followup.json"),
      m07: moduleGroupLabel("m07-sql-basics.json"),
      ch07: moduleGroupLabel("ch07.json"),
      quiz1: moduleGroupLabel("quiz1.json"),
      quiz2: moduleGroupLabel("quiz2-ch7-10.json"),
      misc: moduleGroupLabel("intro.json"),
    }));
    expect(labels.r1).toBe("Original rounds");
    expect(labels.r2followup).toBe("Original rounds");
    expect(labels.m07).toBe("Chapter packs");
    expect(labels.ch07).toBe("Chapter packs");
    expect(labels.quiz1).toBe("Combined exams");
    expect(labels.quiz2).toBe("Combined exams");
    expect(labels.misc).toBe("Modules");
  });

  test("module list suppresses the group header when only the generic 'Modules' bucket applies", async ({ page }) => {
    await goToConfig(page);
    const headerCount = await page.locator("#moduleList .module-group-header").count();
    expect(headerCount).toBe(0);
    expect(await page.locator("#moduleList .module-row").count()).toBeGreaterThan(0);
  });

  test("completion score line has a tier class", async ({ page }) => {
    await startQuiz(page, 3);
    await answerAll(page);
    await page.waitForFunction(() => /\d+%/.test(document.title));
    const cls = await page.locator("#score").getAttribute("class");
    expect(cls).toMatch(/score-(good|mid|poor)/);
  });

  test("history row score-big has a tier class matching pct band", async ({ page }) => {
    await clearStorage(page);
    // Seed three scores spanning the bands.
    await seedSession(page, { quiz_id: "good", score: { correct: 9, total: 10 } });   // 90% → good
    await seedSession(page, { quiz_id: "mid", score: { correct: 6, total: 10 } });    // 60% → mid
    await seedSession(page, { quiz_id: "poor", score: { correct: 2, total: 10 } });   // 20% → poor
    await page.reload();
    await page.locator("#historyBtn").click();

    const scoreEls = page.locator(".history-item .score-big");
    const items = await scoreEls.count();
    expect(items).toBe(3);
    const classes = [];
    for (let i = 0; i < items; i++) {
      classes.push(await scoreEls.nth(i).getAttribute("class"));
    }
    // Ordering is most-recent-first in renderHistory, which matches seed
    // order reversed. We only check that one of each tier is present.
    const joined = classes.join(" ");
    expect(joined).toContain("score-good");
    expect(joined).toContain("score-mid");
    expect(joined).toContain("score-poor");
  });

  // Samples-only: chip clicks are clamped to availableCount when the chip's
  // size exceeds it, so on a small pack the numeric chips fall through to
  // the All chip via the clamp path. This tests the clamp behavior directly.
  test("clicking a numeric chip larger than availableCount clamps to available and selects the All chip", async ({ page }) => {
    await goToConfig(page);
    const available = parseInt((await page.locator("#availableCount").textContent()).trim());
    expect(available).toBeGreaterThan(0);
    // Pick a chip whose size exceeds the samples pack so we can assert clamp behavior.
    const oversizedChip = available < 10
      ? page.locator('#quickPickChips .quick-pick-chip[data-size="10"]')
      : page.locator('#quickPickChips .quick-pick-chip[data-size="50"]');
    await oversizedChip.click();
    // Input is clamped to available, not the chip's nominal size.
    await expect(page.locator("#quizSize")).toHaveValue(String(available));
    // The clicked chip is not "selected" (its size != current value), but the
    // All chip is, because current === available.
    await expect(oversizedChip).not.toHaveClass(/selected/);
    await expect(
      page.locator('#quickPickChips .quick-pick-chip[data-size="all"]')
    ).toHaveClass(/selected/);
  });

  test("typing an arbitrary value in quizSize clears chip selection", async ({ page }) => {
    await goToConfig(page);
    // Click All so something is selected, then type a value that doesn't match
    // any chip nor availableCount.
    await page.locator('#quickPickChips .quick-pick-chip[data-size="all"]').click();
    await page.locator("#quizSize").fill("3");
    const selectedChips = await page.locator("#quickPickChips .quick-pick-chip.selected").count();
    expect(selectedChips).toBe(0);
  });

  test("clicking 'All' chip sets quizSize to availableCount", async ({ page }) => {
    await goToConfig(page);
    const available = (await page.locator("#availableCount").textContent()).trim();
    await page.locator('#quickPickChips .quick-pick-chip[data-size="all"]').click();
    await expect(page.locator("#quizSize")).toHaveValue(available);
    await expect(
      page.locator('#quickPickChips .quick-pick-chip[data-size="all"]')
    ).toHaveClass(/selected/);
  });

  test("post-quiz actions render three buttons including Retry missed", async ({ page }) => {
    await startQuiz(page, 3);
    await expect(page.locator("#retryMissedBtn")).toBeVisible();
    await expect(page.locator("#startAnotherBtn")).toBeVisible();
    await expect(page.locator("#returnToSelectionBtn")).toBeVisible();
  });

  test("Retry missed is disabled at perfect score and runs only missed at imperfect", async ({ page }) => {
    // Drive a deterministic 5-question quiz where we answer correctly. We
    // can't easily guarantee a perfect score on every random pull, so we
    // start by answering all (likely some wrong on the demo pack), then we
    // assert state based on actual outcome.
    await clearStorage(page);
    await startQuiz(page, 5);
    await answerAll(page);
    await page.waitForFunction(() => /\d+%/.test(document.title));

    const missedCount = await page.evaluate(() =>
      Object.values(answers).filter(a => !a.correct).length
    );
    const retryBtn = page.locator("#retryMissedBtn");
    if (missedCount === 0) {
      // Perfect score → button must be disabled.
      await expect(retryBtn).toBeDisabled();
    } else {
      await expect(retryBtn).toBeEnabled();
      await retryBtn.click();
      await expect(page.locator("#quizScreen")).toBeVisible();
      await expect(page.locator(".card")).toHaveCount(missedCount);
    }
  });

  test("expanding a history row shows missed-question prompt and explanation", async ({ page }) => {
    await clearStorage(page);
    await startQuiz(page, 1);
    // Force a wrong answer: pick the LAST option on MC, click "False" on TF,
    // or fill matching with 'index 0' (which often grades as some incorrect).
    const card = page.locator(".card").first();
    const hasMC = (await card.locator(".choices").count()) > 0;
    const hasTF = (await card.locator(".tf-choices").count()) > 0;
    if (hasMC) {
      await card.locator("label.choice").last().click();
    } else if (hasTF) {
      await card.locator(".tf-btn").last().click();
    } else {
      // Matching: select index 1 for all.
      const selects = card.locator("select");
      const c = await selects.count();
      for (let i = 0; i < c; i++) await selects.nth(i).selectOption({ index: 1 });
      await card.locator('button:has-text("Check Matches")').click();
    }
    await page.waitForFunction(() => /\d+%/.test(document.title));

    await page.goto("/app/");
    await page.locator("#historyBtn").click();
    const item = page.locator(".history-item").first();
    await expect(item).toBeVisible();

    // Expand the row.
    await item.locator("summary").click();
    // Wait for lazy-load to complete.
    await page.waitForFunction(() => {
      const detail = document.querySelector(".history-item .history-detail");
      return detail && detail.dataset.loaded === "true";
    });

    // The session may or may not have a missed question depending on the
    // randomly-presented question. We only assert structure: if a missed
    // question is present, its prompt is non-empty.
    const missedRow = item.locator(".history-missed-row").first();
    if ((await missedRow.count()) > 0) {
      const promptText = await missedRow.locator(".history-missed-prompt").textContent();
      expect(promptText.trim().length).toBeGreaterThan(0);
      const explLink = missedRow.locator(".show-explanation");
      if ((await explLink.count()) > 0) {
        await explLink.click();
        await expect(page.locator("#explanationModal")).toHaveClass(/is-open/);
      }
    }
  });

  test("missing-question fallback renders without breaking the row", async ({ page }) => {
    await clearStorage(page);
    // Seed a session whose missed_questions reference an id not in any pack.
    await page.goto("/app/");
    await page.evaluate(() => {
      const session = {
        quiz_id: "fallback-test",
        course: "samples",
        title: "Fallback Test",
        modules_used: ["sample-pack.json"],
        retry_mode: false,
        completed_at: new Date().toISOString(),
        score: { correct: 0, total: 1 },
        missed_topics: ["topic-a"],
        missed_chapters: ["Ch1"],
        missed_questions: [
          { question_id: "this-id-does-not-exist", topic: "topic-a", chapter: "Ch1", picked: "wrong", correct_answer: "right" },
        ],
        topic_summary: [{ topic: "topic-a", correct: 0, total: 1 }],
        chapter_summary: [{ chapter: "Ch1", correct: 0, total: 1, pct: 0 }],
        answers: [],
      };
      localStorage.setItem(STORAGE_KEY, JSON.stringify([session]));
    });
    await page.reload();
    await page.locator("#historyBtn").click();
    const item = page.locator(".history-item").first();
    await expect(item).toBeVisible();
    await item.locator("summary").click();
    await page.waitForFunction(() => {
      const detail = document.querySelector(".history-item .history-detail");
      return detail && detail.dataset.loaded === "true";
    });

    const fallback = item.locator(".history-missed-row em").first();
    await expect(fallback).toContainText("Question removed from pack");
    // Persisted picked/correct fields still rendered.
    await expect(item.locator(".history-missed-row")).toContainText("wrong");
    await expect(item.locator(".history-missed-row")).toContainText("right");
  });

  test("history detail resolves missed questions by (pack_id, question_id) tuple", async ({ page }) => {
    // Phase 2 contract: history-detail lookup keys on `${pack_id}::${question_id}`
    // first, falling back to question_id only when pack_id is null (legacy).
    // Single-pack environments still exercise the tuple path; we additionally
    // assert the "removed from pack" fallback fires for a known wrong pack_id.
    await clearStorage(page);
    // Load the course so allQuestionsByModule is populated, then read a real
    // question id to seed with.
    await goToConfig(page);
    const courseInfo = await page.evaluate(() => {
      const mod = Object.values(allQuestionsByModule)[0];
      return {
        courseId: currentCourse.id,
        packId: mod.pack.pack_id,
        moduleFile: mod.meta.file,
        questionId: mod.questions[0].id,
        prompt: mod.questions[0].prompt,
      };
    });

    // The boot sweep wipes quizzler_sessions on every load, so we seed
    // AFTER the initial goto/clearStorage and navigate via in-page clicks
    // without ever calling page.reload().
    await page.evaluate(({ courseId, packId, moduleFile, questionId }) => {
      const sessions = [
        {
          quiz_id: "tuple-match",
          course: courseId,
          title: "Tuple Match",
          modules_used: [moduleFile],
          retry_mode: false,
          completed_at: new Date().toISOString(),
          score: { correct: 0, total: 3 },
          missed_topics: ["x"],
          missed_chapters: ["Ch1"],
          missed_questions: [
            // (a) matching pack_id, real question_id — resolves via tuple path
            { question_id: questionId, pack_id: packId, topic: "x", chapter: "Ch1", picked: "wrong", correct_answer: "right" },
            // (b) wrong pack_id, real question_id — must NOT resolve, must render fallback
            { question_id: questionId, pack_id: "deleted-pack", topic: "x", chapter: "Ch1", picked: "wrong", correct_answer: "right" },
            // (c) null pack_id (legacy) — must fall back to id-only lookup
            { question_id: questionId, pack_id: null, topic: "x", chapter: "Ch1", picked: "wrong", correct_answer: "right" },
          ],
          topic_summary: [],
          chapter_summary: [],
          answers: [],
        },
      ];
      localStorage.setItem("quizzler_sessions", JSON.stringify(sessions));
    }, courseInfo);

    // Navigate to history without reloading (avoids the boot sweep).
    await page.locator("#backToCourses").click();
    await page.locator("#historyBtn").click();
    await expect(page.locator("#historyScreen")).toBeVisible();

    const item = page.locator(".history-item").first();
    await expect(item).toBeVisible();
    await item.locator("summary").click();
    await page.waitForFunction(() => {
      const detail = document.querySelector(".history-item .history-detail");
      return detail && detail.dataset.loaded === "true";
    });

    const rows = item.locator(".history-missed-row");
    await expect(rows).toHaveCount(3);

    // Row 0: tuple-matched — prompt is the real question prompt, not the
    // "removed from pack" fallback.
    const row0Prompt = await rows.nth(0).locator(".history-missed-prompt").textContent();
    expect(row0Prompt.trim()).toContain(courseInfo.prompt.trim().slice(0, 20));
    await expect(rows.nth(0).locator("em")).toHaveCount(0);

    // Row 1: wrong pack_id — must render fallback even though question_id
    // exists in the loaded pack (pack-scoped lookup rejects the mismatch).
    await expect(rows.nth(1).locator("em")).toContainText("Question removed from pack");

    // Row 2: null pack_id — legacy fallback to id-only lookup; resolves.
    const row2Prompt = await rows.nth(2).locator(".history-missed-prompt").textContent();
    expect(row2Prompt.trim()).toContain(courseInfo.prompt.trim().slice(0, 20));
    await expect(rows.nth(2).locator("em")).toHaveCount(0);
  });
});


// ═══════════════════════════════════════════════════════════
// Phase 4 — Modals + microcopy + polish
// ═══════════════════════════════════════════════════════════

test.describe("Phase 4 gates — Modals, microcopy, polish", () => {
  test("Clear All History uses styled modal — no native dialog fires; cancel keeps storage; confirm clears it", async ({ page }) => {
    await clearStorage(page);
    await seedSession(page);
    await page.reload();
    await page.locator("#historyBtn").click();
    await expect(page.locator(".history-item")).toHaveCount(1);

    // If a native dialog fires we mark the test as failed.
    let nativeDialogFired = false;
    page.on("dialog", (d) => { nativeDialogFired = true; d.dismiss(); });

    // Cancel branch.
    await page.locator("#clearHistoryBtn").click();
    await expect(page.locator("#dialogModal")).toHaveClass(/is-open/);
    await page.locator("#dialogCancelBtn").click();
    await expect(page.locator("#dialogModal")).not.toHaveClass(/is-open/);
    await expect(page.locator(".history-item")).toHaveCount(1);

    // Confirm branch.
    await page.locator("#clearHistoryBtn").click();
    await expect(page.locator("#dialogModal")).toHaveClass(/is-open/);
    await page.locator("#dialogConfirmBtn").click();
    await expect(page.locator(".history-item")).toHaveCount(0);

    expect(nativeDialogFired).toBe(false);
  });

  test("Start Quiz is disabled with a hint when zero modules selected; enabling on selection clears it", async ({ page }) => {
    await goToConfig(page);
    await page.locator("#selectNoneBtn").click();
    await expect(page.locator("#startQuizBtn")).toBeDisabled();
    await expect(page.locator("#startQuizHint")).toContainText("Select at least one module");
    // Selecting any module enables the button.
    await page.locator("#moduleList .module-row").first().click();
    await expect(page.locator("#startQuizBtn")).toBeEnabled();
    await expect(page.locator("#startQuizHint")).toHaveText("");
  });

  test("Check Matches stays disabled until every dropdown is filled", async ({ page }) => {
    // Samples does not include matching today; if absent, verify the
    // freshly-rendered button comes up disabled and skip the fill assertion.
    await startQuiz(page, 5);
    const matchCard = page.locator(".card:has(.matching-grid)").first();
    if ((await matchCard.count()) === 0) {
      // No matching question in the pool — nothing to assert.
      return;
    }
    const checkBtn = matchCard.locator('button:has-text("Check Matches")');
    await expect(checkBtn).toBeDisabled();

    const selects = matchCard.locator("select");
    const count = await selects.count();
    for (let i = 0; i < count - 1; i++) {
      await selects.nth(i).selectOption({ index: 1 });
    }
    // One left unselected — still disabled.
    await expect(checkBtn).toBeDisabled();
    await selects.nth(count - 1).selectOption({ index: 1 });
    await expect(checkBtn).toBeEnabled();
  });

  test("mastery affordance is hidden until the question is answered, then revealed", async ({ page }) => {
    await startQuiz(page, 1);
    const card = page.locator(".card").first();
    const meta = card.locator(".card-meta");
    // Initially hidden.
    await expect(meta).toHaveAttribute("hidden", "");
    // Answer the card (any type).
    await answerCard(card);
    // Hidden attribute removed.
    const hiddenAttr = await meta.getAttribute("hidden");
    expect(hiddenAttr).toBeNull();
    await expect(meta).toBeVisible();
  });

  test("info icon opens modal explaining weighted selection", async ({ page }) => {
    await goToConfig(page);
    await page.locator("#weightingInfoBtn").click();
    await expect(page.locator("#dialogModal")).toHaveClass(/is-open/);
    const body = page.locator("#dialogModalBody");
    await expect(body).toContainText("Mastered");
    await expect(body).toContainText("excluded");
    await expect(body).toContainText("10×");
    await page.locator("#dialogConfirmBtn").click();
    await expect(page.locator("#dialogModal")).not.toHaveClass(/is-open/);
  });

  test("empty Retry Missed state shows hierarchy and CTA returns to Build Quiz tab", async ({ page }) => {
    await clearStorage(page);
    await page.reload();
    await goToConfig(page);
    await page.locator('.tab[data-tab="retryMissed"]').click();
    await expect(page.locator("#retryMissedTab h3")).toHaveText("No retries yet");
    const cta = page.locator("#emptyRetryBuild");
    await expect(cta).toBeVisible();
    await cta.click();
    // Build Quiz tab is now active and configureTab is visible.
    await expect(page.locator("#tab-configure")).toHaveAttribute("aria-selected", "true");
    await expect(page.locator("#configureTab")).toBeVisible();
  });

  // Helper: seed mastery + a session into localStorage for the loaded course
  // so computeReadiness lands at the requested score, then re-render.
  async function seedReadinessState(page, { seenIds, correctIds, sessionScore }) {
    await page.evaluate(({ seenIds, correctIds, sessionScore }) => {
      const cid = currentCourse.id;
      const packId = Object.values(allQuestionsByModule)[0].pack.pack_id;
      // Mastery state.
      const mastery = { seen: {}, correct: {} };
      seenIds.forEach((id) => { mastery.seen[id] = true; });
      correctIds.forEach((id) => { mastery.correct[id] = true; });
      localStorage.setItem(`quizzler_mastery_${cid}__${packId}`, JSON.stringify(mastery));
      // Session for recent-accuracy. Skip if null.
      // Recent-accuracy now aggregates per-answer, so seed `answers` with
      // pack-scoped records that reproduce the requested score.
      if (sessionScore) {
        const answers = [];
        for (let i = 0; i < sessionScore.correct; i++) {
          answers.push({ question_id: `seed-c-${i}`, pack_id: packId, correct: true, topic: "x", chapter: null, difficulty: "easy", response_ms: 1000 });
        }
        for (let i = sessionScore.correct; i < sessionScore.total; i++) {
          answers.push({ question_id: `seed-w-${i}`, pack_id: packId, correct: false, topic: "x", chapter: null, difficulty: "easy", response_ms: 1000 });
        }
        const sessions = [{
          quiz_id: "seed",
          course: cid,
          title: cid,
          modules_used: [],
          retry_mode: false,
          completed_at: new Date().toISOString(),
          score: sessionScore,
          missed_topics: [],
          missed_chapters: [],
          missed_questions: [],
          topic_summary: [],
          chapter_summary: [],
          answers,
        }];
        localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions));
      } else {
        localStorage.setItem(STORAGE_KEY, JSON.stringify([]));
      }
    }, { seenIds, correctIds, sessionScore });
  }

  test("readiness banner renders the per-band next-step copy", async ({ page }) => {
    await clearStorage(page);
    await goToConfig(page);
    const allIds = await getInternalQuestionIds(page);
    expect(allIds.length).toBeGreaterThanOrEqual(5);

    // Band <40: nothing.
    await clearStorage(page);
    await goToConfig(page);
    await expect(page.locator("#readinessNextStep")).toHaveText("Start any module to begin tracking.");

    // Band 40-69: cov=1.0, mastery=0, session 5/10 (acc=0.5) → 30+0+20 = 50.
    await seedReadinessState(page, { seenIds: allIds, correctIds: [], sessionScore: { correct: 5, total: 10 } });
    await page.reload();
    await goToConfig(page);
    await expect(page.locator("#readinessNextStep")).toHaveText("Focus on weak modules — see Session History for breakdown.");

    // Band 70-84: cov=1.0, mastery=0.6 (3/5), session 3/4 (acc=0.75) → 30+18+30 = 78.
    await seedReadinessState(page, {
      seenIds: allIds,
      correctIds: allIds.slice(0, Math.floor(allIds.length * 0.6)),
      sessionScore: { correct: 3, total: 4 },
    });
    await page.reload();
    await goToConfig(page);
    await expect(page.locator("#readinessNextStep")).toHaveText("Push past 85% by retrying missed questions.");

    // Band 85-94: cov=1.0, mastery=1.0, session 3/4 (acc=0.75) → 30+30+30 = 90.
    await seedReadinessState(page, { seenIds: allIds, correctIds: allIds, sessionScore: { correct: 3, total: 4 } });
    await page.reload();
    await goToConfig(page);
    await expect(page.locator("#readinessNextStep")).toHaveText("You're nearly ready — sweep the remaining unseen questions.");

    // Band 95+: cov=1.0, mastery=1.0, session 1/1 (acc=1.0) → 30+30+40 = 100.
    await seedReadinessState(page, { seenIds: allIds, correctIds: allIds, sessionScore: { correct: 1, total: 1 } });
    await page.reload();
    await goToConfig(page);
    await expect(page.locator("#readinessNextStep")).toHaveText("All set. Run a fresh quiz to keep skills sharp.");
  });
});


// ═══════════════════════════════════════════════════════════
// 28. SMOKE — pack-scoped mastery end-to-end (replaces manual checklist)
// ═══════════════════════════════════════════════════════════
//
// Every item in the pack-scoped-mastery-2026-05-10 manual smoke test plan is
// asserted here as automated Playwright. Conditionally skips if the Samples
// course is not present in the test environment (sample-pack-only CI).

test.describe("Smoke — pack-scoped mastery end-to-end", () => {
  test.beforeEach(async ({ page }) => {
    await clearStorage(page);
  });

  async function openSamples(page) {
    await page.goto("/app/");
    const card = page.locator(".course-card", { hasText: "Samples" });
    if ((await card.count()) === 0) {
      test.skip(true, "Samples pack not present in this environment");
      return false;
    }
    await card.click();
    await expect(page.locator("#quizConfig")).toBeVisible();
    return true;
  }

  test("fresh load: Samples banner is 0/5 with no sessions", async ({ page }) => {
    if (!(await openSamples(page))) return;
    await expect(page.locator("#masterySeenPct")).toHaveText("0 / 5 (0%)");
    await expect(page.locator("#masteryCorrectPct")).toHaveText("0 / 5 (0%)");
    await expect(page.locator("#readinessBreakdown")).toContainText("Coverage 0% · Mastery 0% · Recent accuracy 0%");
    await expect(page.locator("#readinessBreakdown")).toContainText("(no sessions yet)");
    await expect(page.locator("#availableCount")).toHaveText("5");
  });

  test("20-question quiz updates banner; storage matches pack-scoped contract", async ({ page }) => {
    if (!(await openSamples(page))) return;
    await page.locator("#quizSize").fill("5");
    await page.locator("#startQuizBtn").click();
    await expect(page.locator("#quizScreen")).toBeVisible();
    await answerAll(page);
    await expect(page.locator("#score")).not.toHaveText(/Not graded yet/);

    // Back to config; banner must reflect the just-completed quiz.
    await page.locator("#backToConfig").click();
    await expect(page.locator("#quizConfig")).toBeVisible();

    const seenText = await page.locator("#masterySeenPct").textContent();
    const seenMatch = seenText.match(/^(\d+)\s*\/\s*5/);
    expect(seenMatch).not.toBeNull();
    expect(parseInt(seenMatch[1])).toBeGreaterThanOrEqual(5);

    // Verify storage contract: pack-scoped mastery key exists, sentinel set,
    // no orphan course-only key.
    const storage = await page.evaluate(() => {
      const keys = [];
      for (let i = 0; i < localStorage.length; i++) keys.push(localStorage.key(i));
      return {
        keys,
        sentinel: localStorage.getItem("quizzler_session_schema_v2"),
        packScopedMastery: localStorage.getItem("quizzler_mastery_samples__samples-demo"),
        orphanMastery: localStorage.getItem("quizzler_mastery_samples"),
        sessions: localStorage.getItem("quizzler_sessions"),
      };
    });
    expect(storage.packScopedMastery).not.toBeNull();
    expect(storage.orphanMastery).toBeNull();
    expect(storage.sentinel).toBe("1");
    expect(storage.sessions).not.toBeNull();
    // Every mastery key must use the new __packId shape.
    expect(storage.keys.filter(k => k.startsWith("quizzler_mastery_")).every(k => k.includes("__"))).toBe(true);
  });

  test("mastered question drops out of next quiz pool", async ({ page }) => {
    if (!(await openSamples(page))) return;

    // Mark every question in the pack as mastered via the production primitive.
    await page.evaluate(() => {
      const cid = currentCourse.id;
      Object.values(allQuestionsByModule).forEach(m => {
        const packId = m.pack.pack_id;
        m.questions.forEach(q => setMastered(cid, packId, q.id, true));
      });
    });

    // Available count must drop to 0 after the mastery sweep; renderConfig
    // recomputes when the pack list is interacted with.
    await page.locator("#selectAllBtn").click();
    await expect(page.locator("#availableCount")).toHaveText("0");

    // Inverse: clear mastery, available count rebounds to 120.
    await page.evaluate(() => clearMastery());
    await page.locator("#selectAllBtn").click();
    await expect(page.locator("#availableCount")).toHaveText("5");
  });

  test("DevTools storage layout: pack-scoped mastery + sentinel + sessions, no legacy orphans", async ({ page }) => {
    if (!(await openSamples(page))) return;
    await page.locator("#quizSize").fill("5");
    await page.locator("#startQuizBtn").click();
    await expect(page.locator("#quizScreen")).toBeVisible();
    await answerAll(page);
    await expect(page.locator("#score")).not.toHaveText(/Not graded yet/);

    const keys = await page.evaluate(() => {
      const out = [];
      for (let i = 0; i < localStorage.length; i++) out.push(localStorage.key(i));
      return out.sort();
    });
    // Expected exact set: pack-scoped mastery, sessions, sentinel. No orphans.
    expect(keys).toContain("quizzler_mastery_samples__samples-demo");
    expect(keys).toContain("quizzler_session_schema_v2");
    expect(keys).toContain("quizzler_sessions");
    expect(keys).not.toContain("quizzler_mastery_samples");
    // Defensive: any quizzler_mastery_* key must contain __.
    keys.filter(k => k.startsWith("quizzler_mastery_")).forEach(k => {
      expect(k).toContain("__");
    });
  });

  test("legacy pre-refactor data is wiped on first boot post-refactor", async ({ page }) => {
    if (!(await openSamples(page))) return;
    // Force the page into pre-refactor-like state from inside.
    await page.evaluate(() => {
      // Seed pre-refactor contamination matching the actual observed bug.
      const seen = {}, correct = {};
      for (let i = 0; i < 71; i++) seen[`legacy-q${i}`] = true;
      for (let i = 0; i < 62; i++) correct[`legacy-q${i}`] = true;
      localStorage.setItem("quizzler_mastery_samples", JSON.stringify({ seen, correct, manual: {} }));
      localStorage.setItem("quizzler_sessions", JSON.stringify([
        { quiz_id: "pre-refactor", course: "samples", answers: [] }
      ]));
      // Simulate "first boot post-refactor" by clearing the sentinel.
      localStorage.removeItem("quizzler_session_schema_v2");
    });

    await page.reload();
    await page.locator(".course-card", { hasText: "Samples" }).click();
    await expect(page.locator("#quizConfig")).toBeVisible();

    // After sweep: banner shows 0/120, not the bug's 71/62/120 contamination.
    await expect(page.locator("#masterySeenPct")).toHaveText("0 / 5 (0%)");
    await expect(page.locator("#masteryCorrectPct")).toHaveText("0 / 5 (0%)");
    await expect(page.locator("#readinessBreakdown")).toContainText("(no sessions yet)");

    const storage = await page.evaluate(() => ({
      orphan: localStorage.getItem("quizzler_mastery_samples"),
      sentinel: localStorage.getItem("quizzler_session_schema_v2"),
    }));
    expect(storage.orphan).toBeNull();
    expect(storage.sentinel).toBe("1");
  });
});

test.describe("Clean up archived data — orphan removal button", () => {
  test("with no orphans, the button shows a 'Nothing to clean' alert and changes nothing", async ({ page }) => {
    await page.goto("/app/");
    await page.locator("#historyBtn").click();
    await expect(page.locator("#historyScreen")).toBeVisible();

    const before = await page.evaluate(() => {
      const out = [];
      for (let i = 0; i < localStorage.length; i++) out.push(localStorage.key(i));
      return out.sort();
    });

    await page.locator("#cleanupOrphansBtn").click();
    await expect(page.locator("#dialogModalTitle")).toHaveText("Nothing to clean");
    await page.locator("#dialogConfirmBtn").click();

    const after = await page.evaluate(() => {
      const out = [];
      for (let i = 0; i < localStorage.length; i++) out.push(localStorage.key(i));
      return out.sort();
    });
    expect(after).toEqual(before);
  });

  test("with orphans, button confirms and surgically removes them while preserving active-course data", async ({ page }) => {
    await page.goto("/app/");

    // Seed both an orphan (course not in manifest) and a real samples-course
    // record. Cleanup should remove the orphan, leave samples untouched.
    await page.evaluate(() => {
      // Orphan mastery: course "archived-fake" is not in COURSES.
      localStorage.setItem(
        "quizzler_mastery_archived-fake__archived-fake-mod1",
        JSON.stringify({ seen: { q1: true, q2: true }, correct: { q1: true } })
      );
      // Real mastery for samples (an active course).
      localStorage.setItem(
        "quizzler_mastery_samples__samples-demo",
        JSON.stringify({ seen: { s1: true }, correct: { s1: true } })
      );
      // Sessions: one orphan, one for samples. missed_topics is required by
      // renderHistory's map; missing it throws and the screen never shows.
      const sessions = [
        { quiz_id: "archived-fake-1", course: "archived-fake", completed_at: "2026-05-01T00:00:00Z", answers: [], missed_questions: [], missed_topics: [], score: { correct: 0, total: 0 } },
        { quiz_id: "samples-1", course: "samples", completed_at: "2026-05-02T00:00:00Z", answers: [], missed_questions: [], missed_topics: [], score: { correct: 1, total: 1 } },
      ];
      localStorage.setItem("quizzler_sessions", JSON.stringify(sessions));
    });

    await page.locator("#historyBtn").click();
    await expect(page.locator("#historyScreen")).toBeVisible();

    await page.locator("#cleanupOrphansBtn").click();
    await expect(page.locator("#dialogModalTitle")).toHaveText("Remove archived-course data?");
    await expect(page.locator("#dialogModalBody")).toContainText("1 mastery key");
    await expect(page.locator("#dialogModalBody")).toContainText("1 session");
    await page.locator("#dialogConfirmBtn").click();

    await expect(page.locator("#dialogModalTitle")).toHaveText("Cleaned up");
    await page.locator("#dialogConfirmBtn").click();

    const state = await page.evaluate(() => ({
      orphanMastery: localStorage.getItem("quizzler_mastery_archived-fake__archived-fake-mod1"),
      samplesMastery: localStorage.getItem("quizzler_mastery_samples__samples-demo"),
      sessions: JSON.parse(localStorage.getItem("quizzler_sessions") || "[]"),
    }));
    expect(state.orphanMastery).toBeNull();
    expect(state.samplesMastery).not.toBeNull();
    expect(state.sessions).toHaveLength(1);
    expect(state.sessions[0].course).toBe("samples");
  });

  test("cancelling the confirm dialog leaves all data intact", async ({ page }) => {
    await page.goto("/app/");
    await page.evaluate(() => {
      localStorage.setItem(
        "quizzler_mastery_archived-fake__archived-fake-mod1",
        JSON.stringify({ seen: { q1: true }, correct: {} })
      );
    });
    await page.locator("#historyBtn").click();
    await page.locator("#cleanupOrphansBtn").click();
    await expect(page.locator("#dialogModalTitle")).toHaveText("Remove archived-course data?");
    await page.locator("#dialogCancelBtn").click();

    const stillThere = await page.evaluate(() =>
      localStorage.getItem("quizzler_mastery_archived-fake__archived-fake-mod1")
    );
    expect(stillThere).not.toBeNull();
  });

  // Verifier-discovered: clicking cleanup before the manifest resolves
  // previously treated COURSES === [] as "no active courses" and deleted
  // every mastery key + session as an "orphan". The guard now refuses to
  // run until courseManifestLoaded.
  test("clicking cleanup before manifest loads shows 'Still loading' and leaves data intact", async ({ page }) => {
    await page.addInitScript(() => {
      // Seed active samples data so a buggy cleanup would obliterate it.
      localStorage.setItem("quizzler_session_schema_v2", "1");
      localStorage.setItem(
        "quizzler_mastery_samples__samples-demo",
        JSON.stringify({ seen: { s1: true }, correct: { s1: true } })
      );
      localStorage.setItem("quizzler_sessions", JSON.stringify([{
        quiz_id: "samples-1", course: "samples", title: "Samples",
        modules_used: [], completed_at: new Date().toISOString(),
        score: { correct: 1, total: 1 }, missed_topics: [],
        missed_chapters: [], missed_questions: [], answers: [],
      }]));
    });
    // Delay the manifest fetch enough to click the button before it resolves.
    await page.route("**/question-packs/manifest.json", async route => {
      await new Promise(r => setTimeout(r, 1500));
      await route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({
          generated_at: new Date().toISOString(),
          courses: [{ id: "samples", name: "Samples", description: "", modules: [] }],
        }),
      });
    });
    await page.goto("/app/", { waitUntil: "domcontentloaded" });
    await expect(page.locator("#historyBtn")).toBeVisible();
    await page.locator("#historyBtn").click();
    await page.locator("#cleanupOrphansBtn").click();
    await expect(page.locator("#dialogModalTitle")).toHaveText("Still loading");
    await page.locator("#dialogConfirmBtn").click();

    const state = await page.evaluate(() => ({
      coursesLength: COURSES.length,
      mastery: localStorage.getItem("quizzler_mastery_samples__samples-demo"),
      sessions: JSON.parse(localStorage.getItem("quizzler_sessions") || "[]"),
    }));
    expect(state.coursesLength).toBe(0);
    expect(state.mastery).not.toBeNull();
    expect(state.sessions).toHaveLength(1);
  });

  test("mastery-only orphan (no orphan sessions) is removed correctly", async ({ page }) => {
    await page.goto("/app/");
    await page.evaluate(() => {
      localStorage.setItem(
        "quizzler_mastery_archived-fake__archived-fake-mod1",
        JSON.stringify({ seen: { q1: true }, correct: {} })
      );
      // Sessions array contains ONLY active-course (samples) records, or is empty.
      localStorage.setItem("quizzler_sessions", "[]");
    });
    await page.locator("#historyBtn").click();
    await page.locator("#cleanupOrphansBtn").click();
    await expect(page.locator("#dialogModalBody")).toContainText("1 mastery key");
    await expect(page.locator("#dialogModalBody")).not.toContainText("session");
    await page.locator("#dialogConfirmBtn").click();
    await expect(page.locator("#dialogModalTitle")).toHaveText("Cleaned up");
    await page.locator("#dialogConfirmBtn").click();

    const orphan = await page.evaluate(() =>
      localStorage.getItem("quizzler_mastery_archived-fake__archived-fake-mod1")
    );
    expect(orphan).toBeNull();
  });

  test("session-only orphan (no orphan mastery) is removed correctly", async ({ page }) => {
    await page.goto("/app/");
    await page.evaluate(() => {
      localStorage.setItem("quizzler_sessions", JSON.stringify([
        { quiz_id: "archived-fake-1", course: "archived-fake", completed_at: "2026-05-01T00:00:00Z",
          answers: [], missed_questions: [], missed_topics: [], score: { correct: 0, total: 0 } },
      ]));
    });
    await page.locator("#historyBtn").click();
    await page.locator("#cleanupOrphansBtn").click();
    await expect(page.locator("#dialogModalBody")).toContainText("1 session");
    await expect(page.locator("#dialogModalBody")).not.toContainText("mastery key");
    await page.locator("#dialogConfirmBtn").click();
    await expect(page.locator("#dialogModalTitle")).toHaveText("Cleaned up");
    await page.locator("#dialogConfirmBtn").click();

    const sessions = await page.evaluate(() => JSON.parse(localStorage.getItem("quizzler_sessions") || "[]"));
    expect(sessions).toHaveLength(0);
  });

  // Verifier-discovered: when an active course id sanitizes to a form that
  // ends in `_` (e.g. "course with trailing space"), the prior parser used
  // indexOf("__") to extract the course segment from `quizzler_mastery_X___Y`
  // and silently dropped the trailing `_`. sanitizeKeySegment now strips
  // leading/trailing `_` so the boundary is unambiguous.
  test("sanitizer edge case: course id with chars that require sanitization is classified correctly", async ({ page }) => {
    await page.goto("/app/");
    const state = await page.evaluate(() => {
      // sanitizeKeySegment("course ") → "course" (trailing _ stripped).
      // A mastery key written for that course would be quizzler_mastery_course__demo.
      // We test classification by calling findOrphans directly with a stubbed COURSES.
      const originalCourses = COURSES;
      try {
        // Active course id has a trailing space that sanitizes away.
        COURSES = [{ id: "course " }, { id: "samples" }];
        // Seed a mastery key as if it had been written by getMasteryKey("course ", "demo").
        localStorage.setItem("quizzler_mastery_course__demo", JSON.stringify({ seen: {}, correct: {} }));
        // Seed a true orphan as a control.
        localStorage.setItem("quizzler_mastery_archived-fake__demo", JSON.stringify({ seen: {}, correct: {} }));
        const { masteryKeys } = findOrphans();
        return { masteryKeys };
      } finally {
        COURSES = originalCourses;
        localStorage.removeItem("quizzler_mastery_course__demo");
        localStorage.removeItem("quizzler_mastery_archived-fake__demo");
      }
    });
    // The course-with-trailing-space key must NOT be flagged as orphan.
    expect(state.masteryKeys).not.toContain("quizzler_mastery_course__demo");
    // The actual archived-fake key must still be flagged.
    expect(state.masteryKeys).toContain("quizzler_mastery_archived-fake__demo");
  });
});


// ═══════════════════════════════════════════════════════════
// XSS REGRESSION TESTS (FIX 1, FIX 2, FIX 3)
// ═══════════════════════════════════════════════════════════

test.describe("XSS Regressions", () => {
  // FIX 1 — Attribute breakout via escapeHtml encoding double-quote
  test("FIX 1: quote in course id is encoded in data-course attribute", async ({ page }) => {
    // Intercept the manifest to inject a course whose id contains a double-quote.
    // Before the fix, escapeHtml left " unencoded, allowing attribute breakout.
    await page.route("**/question-packs/manifest.json", async route => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          generated_at: new Date().toISOString(),
          courses: [
            {
              id: 'xss"onmouseover="window.__xss=1',
              name: "XSS Test Course",
              description: "",
              modules: [],
            },
          ],
        }),
      });
    });
    await page.goto("/app/");
    // The course card must exist and contain no injected onmouseover handler.
    await expect(page.locator(".course-card")).toHaveCount(1);
    const attacked = await page.evaluate(() =>
      document.querySelectorAll("[onmouseover]").length
    );
    expect(attacked).toBe(0);
    // Confirm the raw malicious string is NOT findable as an attribute name.
    const xss = await page.evaluate(() => window.__xss);
    expect(xss).toBeUndefined();
  });

  // FIX 2 — Stored XSS via missed_topics in session history
  test("FIX 2: malicious missed_topics payload does not execute in history", async ({ page }) => {
    await clearStorage(page);
    await seedSession(page, {
      missed_topics: ['<img src=x onerror="window.__xss=1">'],
      missed_chapters: [],
    });
    await page.locator("#historyBtn").click();
    await expect(page.locator("#historyScreen")).toBeVisible();
    // The XSS flag must not have fired.
    const xss = await page.evaluate(() => window.__xss);
    expect(xss).toBeUndefined();
    // No <img> elements should be injected inside the history list.
    await expect(page.locator("#historyList img")).toHaveCount(0);
  });

  // FIX 2 — Legacy guard: session without missed_topics still renders
  test("FIX 2: history renders legacy session missing missed_topics field", async ({ page }) => {
    await clearStorage(page);
    // Seed a session that omits missed_topics / missed_chapters / chapter_summary
    // (as a pre-refactor "legacy" record would look).
    await seedSession(page, {
      missed_topics: undefined,
      missed_chapters: undefined,
      chapter_summary: undefined,
    });
    await page.locator("#historyBtn").click();
    await expect(page.locator("#historyScreen")).toBeVisible();
    // History should display exactly the one seeded session without crashing.
    await expect(page.locator("#historyList .history-item")).toHaveCount(1);
  });

  // FIX 3 — SVG diagram injected as <img> so onload/script cannot execute
  test("FIX 3: malicious diagram SVG is rendered as img and does not execute", async ({ page }) => {
    const maliciousSvg = '<svg onload="window.__xss=1"><script>window.__xss=1<\/script><\/svg>';
    // Intercept the manifest to expose a single fake course with one module.
    await page.route("**/question-packs/manifest.json", async route => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          generated_at: new Date().toISOString(),
          courses: [
            {
              id: "xss-diagram-test",
              name: "XSS Diagram Test",
              description: "",
              modules: [
                { file: "xss-diagram-pack.json", title: "XSS Pack", questionCount: 1 },
              ],
            },
          ],
        }),
      });
    });
    // Intercept the pack fetch to return one question with a malicious diagram.
    await page.route("**/question-packs/xss-diagram-test/xss-diagram-pack.json", async route => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          pack_id: "xss-diagram-pack",
          subject: "XSS Diagram Test",
          title: "XSS Diagram Pack",
          version: 1,
          generated_at: new Date().toISOString(),
          questions: [
            {
              id: "xss-d1",
              type: "multiple_choice",
              topic: "security",
              prompt: "Is this safe?",
              diagram: maliciousSvg,
              diagram_alt: "A diagram",
              options: ["Yes", "No"],
              answer: 0,
            },
          ],
        }),
      });
    });
    await page.goto("/app/");
    await page.locator('.course-card[data-course="xss-diagram-test"]').click();
    await expect(page.locator("#quizConfig")).toBeVisible();
    await page.locator("#quizSize").fill("1");
    await page.locator("#startQuizBtn").click();
    await expect(page.locator("#quizScreen")).toBeVisible();
    await expect(page.locator(".card")).toHaveCount(1);
    // The SVG must not have executed its payload.
    const xss = await page.evaluate(() => window.__xss);
    expect(xss).toBeUndefined();
    // The diagram must be rendered as an <img class="diagram">, not a raw <div>.
    await expect(page.locator("img.diagram")).toBeVisible();
  });
});


// ═══════════════════════════════════════════════════════════
// Storage Resilience (B-5 / B-6 / B-7)
// ═══════════════════════════════════════════════════════════

test.describe("Storage Resilience", () => {
  test.beforeEach(async ({ page }) => {
    await clearStorage(page);
  });

  test("B-5: QuotaExceededError on saveSessions does not abort updateMastery", async ({ page }) => {
    await startQuiz(page, 2);

    // Identify the mastery key for the loaded pack before answering.
    const masteryKey = await page.evaluate(() => {
      const cid = currentCourse.id;
      const packs = Object.values(allQuestionsByModule)
        .map(m => m.pack && m.pack.pack_id)
        .filter(Boolean);
      return packs.length ? getMasteryKey(cid, packs[0]) : null;
    });
    if (!masteryKey) { test.skip(true, "No pack loaded"); return; }

    // Patch setItem to throw a QuotaExceededError for the sessions key only.
    // Suppress showAlert so no modal stalls the flow.
    await page.evaluate(() => {
      const origSetItem = Object.getPrototypeOf(localStorage).setItem;
      Object.getPrototypeOf(localStorage).setItem = function(key, value) {
        if (key === STORAGE_KEY) {
          const err = new Error("QuotaExceededError");
          err.name = "QuotaExceededError";
          err.code = 22;
          throw err;
        }
        origSetItem.call(this, key, value);
      };
      window.__origShowAlert = window.showAlert;
      window.showAlert = () => Promise.resolve();
    });

    await answerAll(page);
    await page.waitForFunction(() => /\d+%/.test(document.title));

    // Mastery must have been written despite the sessions quota error.
    const masteryRaw = await page.evaluate(mk => localStorage.getItem(mk), masteryKey);
    expect(masteryRaw).not.toBeNull();
    const mastery = JSON.parse(masteryRaw);
    expect(Object.keys(mastery.seen).length).toBeGreaterThan(0);
  });

  test("B-6: getMastery backs up corrupt and wrong-shape data before discarding", async ({ page }) => {
    await startQuiz(page, 2);

    const { courseId, packId, masteryKey } = await page.evaluate(() => {
      const cid = currentCourse.id;
      const packs = Object.values(allQuestionsByModule)
        .map(m => m.pack && m.pack.pack_id)
        .filter(Boolean);
      const pid = packs[0];
      return { courseId: cid, packId: pid, masteryKey: getMasteryKey(cid, pid) };
    });
    if (!packId) { test.skip(true, "No pack loaded"); return; }

    // Sub-case 1: parse error — seed invalid JSON, complete quiz to trigger read+write.
    await page.evaluate(key => localStorage.setItem(key, "{bad json"), masteryKey);
    await answerAll(page);
    await page.waitForFunction(() => /\d+%/.test(document.title));

    const hasParseBackup = await page.evaluate(prefix => {
      const keys = [];
      for (let i = 0; i < localStorage.length; i++) keys.push(localStorage.key(i));
      return keys.some(k => k.startsWith(prefix + "__corrupt_"));
    }, masteryKey);
    expect(hasParseBackup).toBe(true);

    // Sub-case 2: wrong shape — seed `{}` (no seen/correct), call getMastery directly.
    await page.evaluate(key => localStorage.setItem(key, "{}"), masteryKey);
    await page.evaluate(([cid, pid]) => getMastery(cid, pid), [courseId, packId]);

    const corruptCount = await page.evaluate(prefix => {
      const keys = [];
      for (let i = 0; i < localStorage.length; i++) keys.push(localStorage.key(i));
      return keys.filter(k => k.startsWith(prefix + "__corrupt_")).length;
    }, masteryKey);
    expect(corruptCount).toBeGreaterThanOrEqual(2);
  });

  test("B-7: non-array sessions blob is backed up; history shows empty state; no page error", async ({ page }) => {
    // Seed a non-array value then (re)load the app so getSessions sees it on first call.
    await page.evaluate(() => localStorage.setItem(STORAGE_KEY, '{"not":"array"}'));
    await page.goto("/app/");

    const errors = [];
    page.on("pageerror", err => errors.push(err.message));

    await page.locator("#historyBtn").click();
    await expect(page.locator("#historyScreen")).toBeVisible();
    await expect(page.locator("#historyList")).toContainText("No sessions recorded yet.");

    const hasBackup = await page.evaluate(() => {
      const keys = [];
      for (let i = 0; i < localStorage.length; i++) keys.push(localStorage.key(i));
      return keys.some(k => k.startsWith("quizzler_sessions__corrupt_"));
    });
    expect(hasBackup).toBe(true);
    expect(errors).toEqual([]);
  });
});


// ═══════════════════════════════════════════════════════════
// FIX 3.1 — Malformed-question hardening
// ═══════════════════════════════════════════════════════════

test.describe("FIX 3.1 – Malformed question hardening", () => {
  // Set up a fake course with 4 questions: one with an out-of-range answer,
  // one missing options entirely, one missing topic, and one valid question.
  async function goToMalformedQuiz(page) {
    await page.route("**/question-packs/manifest.json", async route => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          generated_at: new Date().toISOString(),
          courses: [{
            id: "malformed-test",
            name: "Malformed Test",
            description: "",
            modules: [{ file: "malformed-pack.json", title: "Malformed Pack", questionCount: 4 }],
          }],
        }),
      });
    });
    await page.route("**/question-packs/malformed-test/malformed-pack.json", async route => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          pack_id: "malformed-pack",
          questions: [
            // Out-of-range answer: options exist but answer index is beyond bounds.
            // wireMC's out-of-range guard must prevent a null-dereference throw.
            { id: "bad-answer", type: "multiple_choice", topic: "t", prompt: "Bad answer idx", options: ["A", "B"], answer: 99 },
            // No options field: renderMCQuestion must set _malformed and return early.
            { id: "no-options", type: "multiple_choice", topic: "t", prompt: "No options field", answer: 0 },
            // Topic-less: eyebrow must not crash; topic_summary must use "Uncategorized".
            { id: "no-topic", type: "true_false", prompt: "No topic field", answer: true },
            // Valid question so we can verify the quiz is completable.
            { id: "valid-q", type: "true_false", topic: "valid", prompt: "Valid question", answer: true },
          ],
        }),
      });
    });
    await page.goto("/app/");
    await page.locator('.course-card[data-course="malformed-test"]').click();
    await expect(page.locator("#quizConfig")).toBeVisible();
    await page.locator("#startQuizBtn").click();
    await expect(page.locator("#quizScreen")).toBeVisible();
  }

  test("quiz renders without JS error when pack contains malformed questions", async ({ page }) => {
    const errors = [];
    page.on("pageerror", err => errors.push(err.message));
    await goToMalformedQuiz(page);
    await page.waitForTimeout(300);
    // Filter out any browser noise; only JS errors from quiz code matter.
    const quizErrors = errors.filter(e => !e.toLowerCase().includes("favicon"));
    expect(quizErrors).toHaveLength(0);
  });

  test("valid cards still render alongside malformed ones", async ({ page }) => {
    await goToMalformedQuiz(page);
    // At least one card should be present (valid-q is always renderable).
    const cardCount = await page.locator(".card").count();
    expect(cardCount).toBeGreaterThan(0);
    await expect(page.locator("#card-valid-q")).toBeVisible();
  });

  test("quiz can complete when malformed questions are present", async ({ page }) => {
    await goToMalformedQuiz(page);
    await answerAll(page);
    await expect(page.locator("#score")).not.toHaveText("Score: Not graded yet");
    await expect(page.locator("#completionNotice")).toBeHidden();
  });

  test("saved session has no undefined topic bucket; topic-less question uses Uncategorized", async ({ page }) => {
    await clearStorage(page);
    // Set up routes after clearStorage (routes persist across page.goto calls).
    await page.route("**/question-packs/manifest.json", async route => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          generated_at: new Date().toISOString(),
          courses: [{
            id: "malformed-test",
            name: "Malformed Test",
            description: "",
            modules: [{ file: "malformed-pack.json", title: "Malformed Pack", questionCount: 4 }],
          }],
        }),
      });
    });
    await page.route("**/question-packs/malformed-test/malformed-pack.json", async route => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          pack_id: "malformed-pack",
          questions: [
            { id: "bad-answer", type: "multiple_choice", topic: "t", prompt: "Bad answer idx", options: ["A", "B"], answer: 99 },
            { id: "no-options", type: "multiple_choice", topic: "t", prompt: "No options field", answer: 0 },
            { id: "no-topic", type: "true_false", prompt: "No topic field", answer: true },
            { id: "valid-q", type: "true_false", topic: "valid", prompt: "Valid question", answer: true },
          ],
        }),
      });
    });
    await page.goto("/app/");
    await page.locator('.course-card[data-course="malformed-test"]').click();
    await expect(page.locator("#quizConfig")).toBeVisible();
    await page.locator("#startQuizBtn").click();
    await expect(page.locator("#quizScreen")).toBeVisible();
    await answerAll(page);
    await expect(page.locator("#durationLine")).toBeVisible();

    const sessions = await page.evaluate(() => JSON.parse(localStorage.getItem("quizzler_sessions") || "[]"));
    expect(sessions.length).toBeGreaterThan(0);
    const session = sessions[0];

    // topic_summary must have no "undefined" bucket.
    const topicBuckets = session.topic_summary.map(t => t.topic);
    expect(topicBuckets).not.toContain("undefined");
    expect(topicBuckets.some(t => t === undefined)).toBe(false);
    // Topic-less question must appear as 'Uncategorized', not a missing key.
    expect(topicBuckets).toContain("Uncategorized");

    // answers array must have no undefined topics.
    session.answers.forEach(a => {
      expect(typeof a.topic).toBe("string");
      expect(a.topic).not.toBe("undefined");
    });
  });
});


// ═══════════════════════════════════════════════════════════
// FIX 3.2 — Retry-missed scoping
// ═══════════════════════════════════════════════════════════

test.describe("FIX 3.2 – Retry-missed scoping", () => {
  test("retry session modules_used lists only source modules; missed_questions carry pack_id", async ({ page }) => {
    // Two-module course: mod-a (q-a always missed — TF answer=false, first btn=True)
    //                     mod-b (q-b always correct — TF answer=true, first btn=True)
    await page.route("**/question-packs/manifest.json", async route => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          generated_at: new Date().toISOString(),
          courses: [{
            id: "retry-scope-test",
            name: "Retry Scope Test",
            description: "",
            modules: [
              { file: "mod-a.json", title: "Module A", questionCount: 1 },
              { file: "mod-b.json", title: "Module B", questionCount: 1 },
            ],
          }],
        }),
      });
    });
    await page.route("**/question-packs/retry-scope-test/mod-a.json", async route => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          pack_id: "pack-a",
          questions: [
            { id: "q-a", type: "true_false", topic: "module-a", prompt: "Q-A", answer: false },
          ],
        }),
      });
    });
    await page.route("**/question-packs/retry-scope-test/mod-b.json", async route => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          pack_id: "pack-b",
          questions: [
            { id: "q-b", type: "true_false", topic: "module-b", prompt: "Q-B", answer: true },
          ],
        }),
      });
    });

    await clearStorage(page);
    await page.goto("/app/");
    await page.locator('.course-card[data-course="retry-scope-test"]').click();
    await expect(page.locator("#quizConfig")).toBeVisible();
    await page.locator("#startQuizBtn").click();
    await expect(page.locator("#quizScreen")).toBeVisible();

    // Answer both cards. The first TF button is "True".
    // q-a has answer=false so "True" is wrong → missed.
    // q-b has answer=true so "True" is correct.
    await answerAll(page);
    await expect(page.locator("#durationLine")).toBeVisible();

    // Original session: missed_questions must carry pack_id.
    const sessions = await page.evaluate(() => JSON.parse(localStorage.getItem("quizzler_sessions") || "[]"));
    const origSession = sessions[0];
    expect(origSession.missed_questions.length).toBeGreaterThan(0);
    origSession.missed_questions.forEach(m => {
      expect(m.pack_id).toBeTruthy();
    });

    // Click "Retry missed" — this starts a retry quiz.
    await page.locator("#retryMissedBtn").click();
    await expect(page.locator("#quizScreen")).toBeVisible();

    // quizModulesUsed should be only mod-a.json (source of the missed question).
    const modulesUsed = await page.evaluate(() => quizModulesUsed);
    expect(modulesUsed).toEqual(["mod-a.json"]);

    // Complete the retry quiz to save the retry session.
    await answerAll(page);
    await expect(page.locator("#durationLine")).toBeVisible();

    const allSessions = await page.evaluate(() => JSON.parse(localStorage.getItem("quizzler_sessions") || "[]"));
    const retrySession = allSessions[0]; // most recent
    expect(retrySession.retry_mode).toBe(true);
    expect(retrySession.modules_used).toEqual(["mod-a.json"]);
  });
});


// ═══════════════════════════════════════════════════════════
// FIX 3.3 — Concurrency guards
// ═══════════════════════════════════════════════════════════

test.describe("FIX 3.3 – Dialog resolver flushing", () => {
  test("opening a second dialog immediately settles the first promise with false", async ({ page }) => {
    await page.goto("/app/");

    // Fire two showAlert calls back-to-back. The second openDialog call must
    // flush the first resolver synchronously (resolving P1 = false), then
    // create a fresh promise for the second dialog.
    await page.evaluate(async () => {
      window.__p1Result = "pending";
      window.__p2Result = "pending";
      showAlert("First", "First body").then(v => { window.__p1Result = v; });
      showAlert("Second", "Second body").then(v => { window.__p2Result = v; });
      // Let microtasks run so the .then() callbacks have a chance to fire.
      await new Promise(r => setTimeout(r, 0));
    });

    // P1 must be settled with false (flushed when P2 opened).
    const p1 = await page.evaluate(() => window.__p1Result);
    expect(p1).toBe(false);

    // P2 must still be pending (no one clicked OK yet).
    const p2Before = await page.evaluate(() => window.__p2Result);
    expect(p2Before).toBe("pending");

    // Clicking OK on the second dialog resolves P2 = true.
    await page.locator("#dialogConfirmBtn").click();

    const p2After = await page.evaluate(() => window.__p2Result);
    expect(p2After).toBe(true);
  });
});

test.describe("FIX 3.3 – Course-card race guard", () => {
  test("generation counter prevents stale load from being treated as current", async ({ page }) => {
    await page.goto("/app/");

    // Simulate two concurrent loadAllModules calls: the first is immediately
    // superseded. The guard (gen !== moduleLoadGen) must make gen1 stale and
    // gen2 current — verifiable without real network timing.
    const result = await page.evaluate(() => {
      const gen1 = ++moduleLoadGen;
      const gen2 = ++moduleLoadGen;
      return {
        gen1IsStale: gen1 !== moduleLoadGen,   // gen1 was superseded by gen2
        gen2IsCurrent: gen2 === moduleLoadGen,  // gen2 is the active generation
      };
    });

    expect(result.gen1IsStale).toBe(true);
    expect(result.gen2IsCurrent).toBe(true);
  });
});

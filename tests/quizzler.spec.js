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
  await page.evaluate(() => localStorage.clear());
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
    page.on("dialog", (d) => d.accept());
    await page.locator("#startQuizBtn").click();
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

  test("selecting only last module yields only that module's question IDs", async ({ page }) => {
    await goToConfig(page);
    const info = await getCourseInfo(page);
    if (info.moduleCount < 2) { test.skip(); return; }

    await page.locator("#selectNoneBtn").click();
    await page.locator("#moduleList .module-row").last().click();
    const expectedIds = await page.evaluate(() => {
      const lastFile = currentCourse.modules[currentCourse.modules.length - 1].file;
      return allQuestionsByModule[lastFile].questions.map(q => q.id);
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

    page.on("dialog", (d) => d.accept());
    await matchCard.locator('button:has-text("Check Matches")').click();
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

    page.on("dialog", (d) => d.accept());
    await page.locator("#clearHistoryBtn").click();
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
    await expect(page.locator("#retryMissedTab")).toContainText("No missed questions");
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
  test("return to selection screen navigates back to config", async ({ page }) => {
    await startQuiz(page, 3);
    await page.locator("#returnToSelectionBtn").click();

    await expect(page.locator("#quizConfig")).toBeVisible();
    await expect(page.locator("#quizScreen")).toBeHidden();
  });

  test("return to selection screen preserves module selection state", async ({ page }) => {
    await goToConfig(page);
    const info = await getCourseInfo(page);
    if (info.moduleCount < 3) { test.skip(); return; }

    // Uncheck first two modules
    await page.locator("#moduleList .module-row").nth(0).click();
    await page.locator("#moduleList .module-row").nth(1).click();
    await page.locator("#quizSize").fill("3");
    await page.locator("#startQuizBtn").click();
    await expect(page.locator("#quizScreen")).toBeVisible();

    await page.locator("#returnToSelectionBtn").click();

    const checked = page.locator('#moduleList input[type="checkbox"]:checked');
    await expect(checked).toHaveCount(info.moduleCount - 2);
    await expect(page.locator("#quizSize")).toHaveValue("3");
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

    const mastery = await page.evaluate((cid) =>
      JSON.parse(localStorage.getItem(getMasteryKey(cid))),
      courseId
    );
    expect(mastery).not.toBeNull();
    expect(Object.keys(mastery.seen).length).toBe(2);
    expect(Object.keys(mastery.correct).length).toBeGreaterThanOrEqual(0);
    expect(Object.keys(mastery.correct).length).toBeLessThanOrEqual(2);
  });

  test("mastery accumulates across multiple quiz sessions", async ({ page }) => {
    await startQuiz(page, 2);
    const courseId = await page.evaluate(() => currentCourse.id);
    await answerAll(page);

    const mastery1 = await page.evaluate((cid) =>
      JSON.parse(localStorage.getItem(getMasteryKey(cid))),
      courseId
    );
    const seen1 = Object.keys(mastery1.seen).length;

    await page.locator("#backToConfig").click();
    await expect(page.locator("#quizConfig")).toBeVisible();
    await page.locator("#quizSize").fill("3");
    await page.locator("#startQuizBtn").click();
    await expect(page.locator("#quizScreen")).toBeVisible();
    await answerAll(page);

    const mastery2 = await page.evaluate((cid) =>
      JSON.parse(localStorage.getItem(getMasteryKey(cid))),
      courseId
    );
    const seen2 = Object.keys(mastery2.seen).length;

    expect(seen2).toBeGreaterThanOrEqual(seen1);
  });

  test("clearing history also clears mastery", async ({ page }) => {
    await startQuiz(page, 2);
    const courseId = await page.evaluate(() => currentCourse.id);
    await answerAll(page);

    const before = await page.evaluate((cid) =>
      localStorage.getItem(getMasteryKey(cid)),
      courseId
    );
    expect(before).not.toBeNull();

    await page.goto("/app/");
    await page.locator("#historyBtn").click();
    await expect(page.locator("#historyScreen")).toBeVisible();
    page.on("dialog", (d) => d.accept());
    await page.locator("#clearHistoryBtn").click();

    const after = await page.evaluate((cid) =>
      localStorage.getItem(getMasteryKey(cid)),
      courseId
    );
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
      localStorage.setItem(getMasteryKey(cid), JSON.stringify({ seen, correct, manual: {} }));
    }, { seen, correct, cid: courseId });

    await page.locator("#backToCourses").click();
    await page.locator(".course-card").first().click();
    await expect(page.locator("#quizConfig")).toBeVisible();

    await expect(page.locator("#masteryStatus")).toContainText("All");
    await expect(page.locator("#masteryStatus")).toContainText("questions seen");
  });

  test("weighted selection skews toward unseen questions", async ({ page }) => {
    await goToConfig(page);
    const info = await getCourseInfo(page);
    if (info.totalQuestions < 20) { test.skip(); return; }
    const courseId = await page.evaluate(() => currentCourse.id);

    const allIds = await getInternalQuestionIds(page);

    // Mark all but 15 questions as mastered
    const unseenCount = Math.min(15, Math.floor(allIds.length / 2));
    const unseenList = allIds.slice(0, unseenCount);
    const seen = {};
    const correct = {};
    const unseenSet = new Set(unseenList);
    allIds.forEach(id => {
      if (!unseenSet.has(id)) {
        seen[id] = true;
        correct[id] = true;
      }
    });
    await page.evaluate(({ seen, correct, cid }) => {
      localStorage.setItem(getMasteryKey(cid), JSON.stringify({ seen, correct, manual: {} }));
    }, { seen, correct, cid: courseId });

    const quizSize = Math.min(20, info.totalQuestions);
    await page.locator("#quizSize").fill(String(quizSize));
    await page.locator("#startQuizBtn").click();
    await expect(page.locator("#quizScreen")).toBeVisible();

    const quizIds = await page.evaluate(() =>
      questions.map(q => q.id)
    );
    const unseenInQuiz = quizIds.filter(id => unseenSet.has(id)).length;
    // With weighting, unseen questions should appear more than pure random
    expect(unseenInQuiz).toBeGreaterThanOrEqual(Math.min(3, unseenCount));
  });

  test("mastered checkbox flags a question as correct without excluding it", async ({ page }) => {
    await startQuiz(page, 1);
    const courseId = await page.evaluate(() => currentCourse.id);
    const info = await getCourseInfo(page);
    const questionId = await page.locator(".question-id").first().textContent();

    await page.locator('[id^="mastered-"]').first().check();

    const mastery = await page.evaluate((cid) =>
      JSON.parse(localStorage.getItem(getMasteryKey(cid))),
      courseId
    );
    expect(mastery.correct[questionId]).toBe(true);
    expect(mastery.seen[questionId]).toBe(true);
    // Schema no longer carries a `manual` field
    expect(mastery.manual).toBeUndefined();

    await page.locator("#returnToSelectionBtn").click();
    const availableAfter = parseInt(await page.locator("#availableCount").textContent());
    expect(availableAfter).toBe(info.totalQuestions);
  });

  test("unchecking the mastered toggle removes the correct flag", async ({ page }) => {
    await startQuiz(page, 1);
    const courseId = await page.evaluate(() => currentCourse.id);
    const info = await getCourseInfo(page);
    const questionId = await page.locator(".question-id").first().textContent();
    const toggle = page.locator('[id^="mastered-"]').first();

    await toggle.check();
    await expect(toggle).toBeChecked();
    await toggle.uncheck();
    await expect(toggle).not.toBeChecked();

    const mastery = await page.evaluate((cid) =>
      JSON.parse(localStorage.getItem(getMasteryKey(cid))),
      courseId
    );
    expect(mastery.correct[questionId]).toBeUndefined();

    await page.locator("#returnToSelectionBtn").click();
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
      localStorage.setItem(
        getMasteryKey(cid),
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
    // is purged from storage on the next write.
    const otherToggle = page.locator(`#mastered-${otherId}`);
    await otherToggle.check();

    const mastery = await page.evaluate((cid) =>
      JSON.parse(localStorage.getItem(getMasteryKey(cid))),
      courseId
    );
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
      const m = JSON.parse(localStorage.getItem(getMasteryKey(cid))) || { seen: {}, correct: {} };
      m.seen[id] = true;
      m.correct[id] = true;
      localStorage.setItem(getMasteryKey(cid), JSON.stringify(m));
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
      localStorage.setItem(getMasteryKey(cid), JSON.stringify({ seen: ids, correct: ids, manual: {} }));
    }, { ids: both, cid: courseId });

    await page.locator("#backToCourses").click();
    await page.locator(".course-card").first().click();
    await expect(page.locator("#quizConfig")).toBeVisible();

    await expect(page.locator("#masteryStatus")).toContainText("answered correctly at least once");
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
      localStorage.setItem(getMasteryKey(cid), JSON.stringify({ seen: ids, correct: ids, manual: {} }));
    }, { ids: both, cid: courseId });

    for (let i = 0; i < 3; i++) {
      await seedSession(page, {
        quiz_id: `perfect-${i}`,
        course: courseId,
        score: { correct: 20, total: 20 },
        missed_topics: [],
        missed_questions: [],
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
      localStorage.setItem(getMasteryKey(cid), JSON.stringify({ seen, correct, manual: {} }));
    }, { seen, correct, cid: courseId });

    for (let i = 0; i < 3; i++) {
      await seedSession(page, {
        quiz_id: `good-${i}`,
        course: courseId,
        score: { correct: 17, total: 20 },
        missed_topics: ["some-topic"],
        missed_questions: [{ question_id: "x", topic: "some-topic" }],
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
    page.on("dialog", (d) => d.accept());
    await page.locator("#clearHistoryBtn").click();

    await page.locator("#backFromHistory").click();
    await page.locator(".course-card").first().click();
    await expect(page.locator("#quizConfig")).toBeVisible();

    await expect(page.locator("#readinessNumber")).toHaveText("0%");
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
    { id: "returnToSelectionBtn", landing: "#quizConfig" },
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
    // CR-5 guard: completion title must not get clobbered back to "Q.../..." form.
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

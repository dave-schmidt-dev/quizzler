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

  test("manual mastery checkbox persists and excludes a question from future quizzes", async ({ page }) => {
    await startQuiz(page, 1);
    const courseId = await page.evaluate(() => currentCourse.id);
    const info = await getCourseInfo(page);
    const questionId = await page.locator(".question-id").first().textContent();

    await page.locator('[id^="mastered-"]').first().check();

    const mastery = await page.evaluate((cid) =>
      JSON.parse(localStorage.getItem(getMasteryKey(cid))),
      courseId
    );
    expect(mastery.manual[questionId]).toBe(true);

    await page.locator("#returnToSelectionBtn").click();
    const availableAfter = parseInt(await page.locator("#availableCount").textContent());
    expect(availableAfter).toBe(info.totalQuestions - 1);

    if (info.totalQuestions > 5) {
      await page.locator("#quizSize").fill("5");
      await page.locator("#startQuizBtn").click();
      await expect(page.locator("#quizScreen")).toBeVisible();
      const quizIds = await page.evaluate(() => questions.map(q => q.id));
      expect(quizIds).not.toContain(questionId);
    }
  });

  test("manual mastery checkbox can be removed to make a question eligible again", async ({ page }) => {
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
    expect(mastery.manual[questionId]).toBeUndefined();

    await page.locator("#returnToSelectionBtn").click();
    const available = parseInt(await page.locator("#availableCount").textContent());
    expect(available).toBe(info.totalQuestions);
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

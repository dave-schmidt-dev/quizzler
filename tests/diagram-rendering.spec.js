// @ts-check
//
// Diagram rendering coverage for question packs that ship inline SVG
// diagrams. The main spec file (quizzler.spec.js) is course-agnostic and
// does not exercise the q.diagram code path because the demo sample pack
// contains zero diagrams. The ITD 256 final-wrap pack contributes 12
// inline-SVG diagrams that are injected via innerHTML inside `.diagram`.
//
// These tests assert the engine actually renders inline SVG when present,
// and that diagram-bearing questions remain answerable. They run only if
// the loaded course actually has at least one diagram-bearing question, so
// they remain safe even after the ITD pack is rotated out.
const { test, expect } = require("@playwright/test");

// Load every pack under every course and return only diagram-bearing
// questions, preserving course/module identity for navigation.
async function findDiagramQuestion(page) {
  await page.goto("/app/");
  return page.evaluate(async () => {
    const manifest = await fetch("/question-packs/manifest.json").then(r => r.json());
    for (const course of manifest.courses) {
      for (const mod of course.modules) {
        const url = `/question-packs/${course.id}/${mod.file}`;
        const pack = await fetch(url).then(r => r.json());
        const qs = (pack.questions || []).filter(q => q.diagram && q.diagram.trim());
        if (qs.length > 0) {
          return {
            courseId: course.id,
            moduleFile: mod.file,
            sample: qs[0],
            totalDiagrams: qs.length,
          };
        }
      }
    }
    return null;
  });
}

test.describe("Diagram rendering (inline SVG)", () => {
  test("at least one diagram-bearing question renders its <svg> when its module is selected", async ({ page }) => {
    const found = await findDiagramQuestion(page);
    test.skip(!found, "No diagram-bearing questions in any installed pack.");

    // Pick the course that has the diagram and start a quiz on just that
    // module, sized to the full module so we are very likely to pull in
    // the diagram-bearing question. We then verify at least one .diagram
    // node renders a parsed inline <svg> element (not just an HTML string
    // fragment escaped into the DOM).
    await page.locator(`.course-card[data-course="${found.courseId}"]`).click();
    await expect(page.locator("#quizConfig")).toBeVisible();

    // Make sure the right module is checked (single-module packs already are).
    await page.locator("#selectAllBtn").click();

    // Set quiz size to "all" so every diagram-bearing question is included.
    await page.locator('#quickPickChips .quick-pick-chip[data-size="all"]').click();
    await page.locator("#startQuizBtn").click();
    await expect(page.locator("#quizScreen")).toBeVisible();

    // The .diagram wrapper must contain a real <svg> element parsed from
    // the injected innerHTML — not an escaped string.
    const diagramCount = await page.locator(".diagram svg").count();
    expect(diagramCount).toBeGreaterThan(0);

    // Sanity-check the SVG actually has child shape nodes (rect/line/text/etc.).
    // If the engine ever HTML-escaped the diagram, querySelectorAll would
    // return zero children inside the wrapper.
    const hasShapes = await page.evaluate(() => {
      const svg = document.querySelector(".diagram svg");
      if (!svg) return false;
      return svg.children.length > 0;
    });
    expect(hasShapes).toBe(true);
  });

  test("a diagram-bearing question is still answerable end-to-end", async ({ page }) => {
    const found = await findDiagramQuestion(page);
    test.skip(!found, "No diagram-bearing questions in any installed pack.");

    await page.locator(`.course-card[data-course="${found.courseId}"]`).click();
    await expect(page.locator("#quizConfig")).toBeVisible();
    await page.locator("#selectAllBtn").click();
    await page.locator('#quickPickChips .quick-pick-chip[data-size="all"]').click();
    await page.locator("#startQuizBtn").click();
    await expect(page.locator("#quizScreen")).toBeVisible();

    // Pick the first card that has a diagram and a multiple-choice body
    // (the most common diagram-bearing shape in real packs). We don't
    // require correctness — only that clicking a choice locks the card.
    const diagramCard = page.locator(".card:has(.diagram svg):has(label.choice)").first();
    if ((await diagramCard.count()) === 0) {
      // Fall back to any diagram-bearing card with any answerable body.
      const anyDiagramCard = page.locator(".card:has(.diagram svg)").first();
      expect(await anyDiagramCard.count()).toBeGreaterThan(0);
      // True/false fallback path
      const tfBtn = anyDiagramCard.locator(".tf-btn").first();
      if (await tfBtn.count()) {
        await tfBtn.click();
        await expect(anyDiagramCard.locator(".tf-btn.is-correct, .tf-btn.is-incorrect")).toHaveCount(2);
        return;
      }
      test.skip(true, "Diagram-bearing question is matching-only; covered elsewhere.");
      return;
    }
    await diagramCard.locator("label.choice").first().click();
    await expect(diagramCard.locator("label.choice.is-correct, label.choice.is-incorrect")).not.toHaveCount(0);
  });
});

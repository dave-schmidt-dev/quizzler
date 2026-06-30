// @ts-check
//
// Layer-A pack-quality linter ratchet test.
//
// Strategy: lint_baseline.json captures the count of critical violations per
// legacy pack. This test asserts no NEW criticals beyond the baseline per
// pack, and zero criticals in any pack not listed in the baseline. Cleanup
// ratchets the baseline counts down over time.
//
// The test is NOT a "zero criticals" check because retroactively applying
// Layer A's rules to legacy packs would brick the project. New packs and
// future edits are held to a zero-criticals standard via the unlisted-pack
// rule.

const { test, expect } = require("@playwright/test");
const { execFileSync } = require("node:child_process");
const path = require("node:path");
const fs = require("node:fs");

const ROOT = path.resolve(__dirname, "..");
const LINTER = path.join(ROOT, "scripts", "lint_packs.py");
const BASELINE_FILE = path.join(ROOT, "lint_baseline.json");

function runLinter() {
  let stdout;
  try {
    stdout = execFileSync("python3", [LINTER, "--all", "--json"], {
      cwd: ROOT,
      encoding: "utf8",
    });
  } catch (err) {
    stdout = err.stdout || "";
  }
  return JSON.parse(stdout);
}

function loadBaseline() {
  // lint_baseline.json is gitignored (it references private course pack paths
  // that don't ship to the public repo). On a fresh clone there's no baseline
  // → enforce zero criticals on every pack (the public samples pack is clean).
  // Local dev with the baseline file gets the ratchet for legacy packs.
  if (!fs.existsSync(BASELINE_FILE)) return {};
  const raw = JSON.parse(fs.readFileSync(BASELINE_FILE, "utf8"));
  return raw.packs || {};
}

test.describe("Pack quality — Layer-A linter ratchet", () => {
  test("no NEW critical violations beyond lint_baseline.json", () => {
    const { results } = runLinter();
    const baseline = loadBaseline();

    /** @type {string[]} */
    const regressions = [];
    /** @type {string[]} */
    const ratchetable = [];

    for (const result of results) {
      const pack = result.pack;
      const crits = (result.violations || []).filter(
        (v) => v.severity === "critical"
      );
      const allowed = baseline[pack] || 0;

      if (crits.length > allowed) {
        const extra = crits.length - allowed;
        const lines = crits
          .map((v) => `      [${v.rule}] @ ${v.qid}: ${v.detail}`)
          .join("\n");
        regressions.push(
          `  ${pack}: ${crits.length} critical (baseline ${allowed}, +${extra} new)\n${lines}`
        );
      } else if (crits.length < allowed) {
        ratchetable.push(
          `  ${pack}: ${crits.length} critical (baseline ${allowed}, can ratchet down by ${
            allowed - crits.length
          })`
        );
      }
    }

    // Soft signal: log ratchetable packs so the dev notices and updates the baseline.
    if (ratchetable.length) {
      console.log(
        `\nlint_baseline.json can be ratcheted down:\n${ratchetable.join("\n")}\n`
      );
    }

    expect(
      regressions,
      `New critical Layer-A violations detected (raise baseline only with deliberate justification):\n${regressions.join(
        "\n"
      )}`
    ).toHaveLength(0);
  });

  test("linter runs and produces structured output", () => {
    const { results, exit_code } = runLinter();
    expect(results).toBeInstanceOf(Array);
    expect(results.length).toBeGreaterThan(0);
    expect([0, 1, 2]).toContain(exit_code);
  });
});

// ── L10 distractor-coverage rule — focused unit cases ────────────────────────
//
// Each case writes a one-question pack to a gitignored scratch file and lints it
// directly. Fixtures live under test-results/ and are passed as RELATIVE paths
// (cwd = ROOT) so the linter's pack_path.relative_to(PROJECT_ROOT) stays valid.
const FIXTURE_DIR = path.join(ROOT, "test-results", "l10-fixtures");

/** Lint an ad-hoc pack of questions; return violations matching `ruleFilter`. */
function lintQuestions(questions, fileLabel, ruleFilter = "L10") {
  fs.mkdirSync(FIXTURE_DIR, { recursive: true });
  const abs = path.join(FIXTURE_DIR, `${fileLabel}.json`);
  fs.writeFileSync(abs, JSON.stringify({ pack_id: fileLabel, questions }));
  const rel = path.relative(ROOT, abs);
  let stdout;
  try {
    stdout = execFileSync("python3", [LINTER, rel, "--json"], { cwd: ROOT, encoding: "utf8" });
  } catch (err) {
    stdout = err.stdout || ""; // non-zero exit on critical/warning still prints JSON
  }
  const { results } = JSON.parse(stdout);
  return (results[0].violations || []).filter((v) => v.rule === ruleFilter);
}

/**
 * Lint a single ad-hoc question; return only its violations for `ruleFilter`
 * (default "L10" so the existing L10 cases keep working unchanged).
 */
function lintQuestion(question, fileLabel, ruleFilter = "L10") {
  return lintQuestions([question], fileLabel, ruleFilter);
}

const MC = {
  id: "q1",
  type: "multiple_choice",
  topic: "t",
  difficulty: "easy",
  prompt: "Which control acts after an incident to repair damage?",
  options: ["Preventive", "Detective", "Corrective", "Compensating"],
  answer: 2,
};

test.describe("Pack quality — L10 distractor coverage", () => {
  test("CRITICAL when the explanation addresses no distractor and has no contrast cue", () => {
    const v = lintQuestion(
      { ...MC, explanation: "A corrective control repairs damage after an incident occurs." },
      "l10-critical"
    );
    expect(v).toHaveLength(1);
    expect(v[0].severity).toBe("critical");
  });

  test("RESCUED (no violation) when zero token coverage but a contrast cue is present", () => {
    // Paraphrase coverage: never names Preventive/Detective/Compensating, but the
    // phrase-level contrast cue "unlike" signals the explanation is distinguishing
    // options. (The over-broad generic cues "the other" / "rather" were dropped in
    // the Task-19 tightening, so the cue here is a kept phrase-level one.)
    const v = lintQuestion(
      {
        ...MC,
        explanation:
          "A corrective control repairs damage after an incident; unlike the other control types, which act before or merely watch for an event without fixing it.",
      },
      "l10-rescue"
    );
    expect(v).toHaveLength(0);
  });

  test("WARNING when some but not all distractors are addressed", () => {
    const v = lintQuestion(
      {
        ...MC,
        explanation:
          "A corrective control repairs damage after the fact. Preventive controls act before an attack and detective controls only identify one in progress.",
      },
      "l10-warning"
    );
    expect(v).toHaveLength(1);
    expect(v[0].severity).toBe("warning");
    expect(v[0].detail).toContain("Compensating");
  });

  test("CLEAN when every distractor is named in the explanation", () => {
    const v = lintQuestion(
      {
        ...MC,
        explanation:
          "A corrective control repairs damage after the fact. Preventive controls act before an attack, detective controls identify one in progress, and a compensating control is a stand-in when the primary control is unavailable.",
      },
      "l10-clean"
    );
    expect(v).toHaveLength(0);
  });

  test("checkable-guard: numeric distractors with no usable tokens are skipped, not flagged", () => {
    const v = lintQuestion(
      {
        id: "q1",
        type: "multiple_choice",
        topic: "t",
        difficulty: "easy",
        prompt: "What is 6 times 7?",
        options: ["42", "36", "48", "49"],
        answer: 0,
        explanation: "Six groups of seven total forty-two.",
      },
      "l10-numeric"
    );
    expect(v).toHaveLength(0);
  });

  test("non-MC types (true_false, matching) are out of scope", () => {
    const tf = lintQuestion(
      {
        id: "q1",
        type: "true_false",
        topic: "t",
        difficulty: "easy",
        prompt: "The sky is green.",
        answer: false,
        explanation: "The daytime sky appears blue due to Rayleigh scattering.",
      },
      "l10-tf"
    );
    expect(tf).toHaveLength(0);
  });

  test("missing explanation is out of scope for L10 (L12 owns the defect)", () => {
    const v = lintQuestion({ ...MC, explanation: "" }, "l10-noexpl");
    expect(v).toHaveLength(0);
    // L10 stays silent, but L12 now owns the empty-explanation defect: the same
    // question must produce an L12 critical.
    const l12 = lintQuestion({ ...MC, explanation: "" }, "l10-noexpl", "L12");
    expect(l12).toHaveLength(1);
    expect(l12[0].severity).toBe("critical");
  });
});

// ── L12 explanation presence + topic/difficulty hygiene ──────────────────────
test.describe("Pack quality — L12 explanation + metadata hygiene", () => {
  test("CLEAN when explanation, topic, and difficulty are all present and valid", () => {
    const v = lintQuestion(
      { ...MC, explanation: "A corrective control repairs damage after an incident." },
      "l12-clean",
      "L12"
    );
    expect(v).toHaveLength(0);
  });

  test("CRITICAL when the explanation is blank (whitespace only) on an explained type", () => {
    const v = lintQuestion({ ...MC, explanation: "   " }, "l12-blank-expl", "L12");
    expect(v).toHaveLength(1);
    expect(v[0].severity).toBe("critical");
  });

  test("WARNING when difficulty is outside {easy, medium, hard}", () => {
    const v = lintQuestion(
      { ...MC, explanation: "A corrective control repairs damage.", difficulty: "trivial" },
      "l12-bad-difficulty",
      "L12"
    );
    expect(v).toHaveLength(1);
    expect(v[0].severity).toBe("warning");
    expect(v[0].detail).toContain("difficulty");
  });

  test("matching question with no explanation is an L12 critical", () => {
    const v = lintQuestion(
      {
        id: "m0",
        type: "matching",
        topic: "t",
        difficulty: "easy",
        prompt: "Match each item to its category.",
        leftItems: ["Apple", "Carrot"],
        rightItems: ["Fruit", "Vegetable"],
        correctPairs: [0, 1],
      },
      "l12-matching-noexpl",
      "L12"
    );
    expect(v).toHaveLength(1);
    expect(v[0].severity).toBe("critical");
  });
});

// ── L13 duplicate question ids within a pack ─────────────────────────────────
test.describe("Pack quality — L13 duplicate question ids", () => {
  test("CRITICAL when two questions in a pack share an id", () => {
    const q1 = { ...MC, id: "dup", explanation: "x explains it." };
    const q2 = {
      ...MC,
      id: "dup",
      prompt: "Which framework component handles authorization decisions?",
      explanation: "y explains it.",
    };
    const v = lintQuestions([q1, q2], "l13-dup", "L13");
    expect(v).toHaveLength(1);
    expect(v[0].severity).toBe("critical");
    expect(v[0].qid).toBe("dup");
  });

  test("no L13 violation when ids are distinct", () => {
    const q1 = { ...MC, id: "a", explanation: "x explains it." };
    const q2 = {
      ...MC,
      id: "b",
      prompt: "Which framework component handles authorization decisions?",
      explanation: "y explains it.",
    };
    const v = lintQuestions([q1, q2], "l13-distinct", "L13");
    expect(v).toHaveLength(0);
  });
});

// ── L7 matching schema: shorter-rightItems allowed; duplicate rightItems fail ─
test.describe("Pack quality — L7 matching schema", () => {
  test("no critical when rightItems is shorter than leftItems (shared targets)", () => {
    // Regression guard: this configuration formerly tripped a false
    // "matching pairs unbalanced" critical. Several left items legitimately
    // share one right answer by reusing its index in correctPairs.
    const q = {
      id: "m1",
      type: "matching",
      topic: "t",
      difficulty: "easy",
      prompt: "Sort each item into its category.",
      leftItems: ["Apple", "Carrot", "Banana", "Pea"],
      rightItems: ["Edible plant", "Root storage organ"],
      correctPairs: [0, 1, 0, 1],
      explanation: "Apples and bananas key to the first category; carrots and peas to the second.",
    };
    const v = lintQuestion(q, "l7-shared-targets", "L7");
    expect(v).toHaveLength(0);
  });

  test("CRITICAL when rightItems contains duplicate entries", () => {
    const q = {
      id: "m2",
      type: "matching",
      topic: "t",
      difficulty: "easy",
      prompt: "Sort each item into its category.",
      leftItems: ["Apple", "Carrot"],
      rightItems: ["Category one", "category one "],
      correctPairs: [0, 1],
      explanation: "Both items would key to the same category — a duplicate right entry.",
    };
    const v = lintQuestion(q, "l7-dup-right", "L7");
    expect(v).toHaveLength(1);
    expect(v[0].severity).toBe("critical");
    expect(v[0].detail).toContain("rightItems");
  });
});

// ── lint_waivers — author-declared suppression with an audit trail ────────────
//
// A pack may carry a top-level `lint_waivers` array. Matched findings move from
// `violations` (blocking) to `waived` (non-blocking) with the justification.
// Stale/unjustified waivers are reported back as WAIVER warnings so the list
// can't rot. Lints a full pack object and returns the whole result.
function lintFullPack(pack, fileLabel) {
  fs.mkdirSync(FIXTURE_DIR, { recursive: true });
  const abs = path.join(FIXTURE_DIR, `${fileLabel}.json`);
  fs.writeFileSync(abs, JSON.stringify(pack));
  const rel = path.relative(ROOT, abs);
  let stdout;
  try {
    stdout = execFileSync("python3", [LINTER, rel, "--json"], { cwd: ROOT, encoding: "utf8" });
  } catch (err) {
    stdout = err.stdout || "";
  }
  return JSON.parse(stdout).results[0];
}

// A matching question with identity-ordered correctPairs → one L1 warning.
const IDENTITY_MATCH = {
  id: "m1",
  type: "matching",
  topic: "t",
  difficulty: "easy",
  prompt: "Match each unit to what it measures.",
  leftItems: ["Kelvin", "Pascal"],
  rightItems: ["Temperature", "Pressure"],
  correctPairs: [0, 1],
  explanation: "Kelvin measures temperature; Pascal measures pressure.",
};

test.describe("Pack quality — lint_waivers", () => {
  test("a matching waiver moves the L1 finding to waived (non-blocking)", () => {
    const r = lintFullPack(
      {
        pack_id: "waiver-hit",
        lint_waivers: [
          { rule: "L1", qid: "m1", reason: "identity order is intentional for this demo" },
        ],
        questions: [IDENTITY_MATCH],
      },
      "waiver-hit"
    );
    expect(r.violations.filter((v) => v.rule === "L1")).toHaveLength(0);
    const waived = (r.waived || []).filter((v) => v.rule === "L1");
    expect(waived).toHaveLength(1);
    expect(waived[0].waived_reason).toContain("intentional");
  });

  test("a pack-wide waiver (no qid) suppresses the rule across questions", () => {
    const r = lintFullPack(
      {
        pack_id: "waiver-packwide",
        lint_waivers: [{ rule: "L1", reason: "demo pack, runtime shuffler covers it" }],
        questions: [IDENTITY_MATCH, { ...IDENTITY_MATCH, id: "m2" }],
      },
      "waiver-packwide"
    );
    expect(r.violations.filter((v) => v.rule === "L1")).toHaveLength(0);
    expect((r.waived || []).filter((v) => v.rule === "L1")).toHaveLength(2);
  });

  test("a stale waiver (matches nothing) is reported as a blocking WAIVER warning", () => {
    const r = lintFullPack(
      {
        pack_id: "waiver-stale",
        lint_waivers: [{ rule: "L3", qid: "m1", reason: "no length tell here" }],
        questions: [IDENTITY_MATCH],
      },
      "waiver-stale"
    );
    const hygiene = r.violations.filter((v) => v.rule === "WAIVER");
    expect(hygiene).toHaveLength(1);
    expect(hygiene[0].severity).toBe("warning");
    expect(hygiene[0].detail).toContain("stale");
    // The real L1 finding is untouched (its waiver didn't match it).
    expect(r.violations.filter((v) => v.rule === "L1")).toHaveLength(1);
  });

  test("a waiver without a reason is flagged even though it suppresses the finding", () => {
    const r = lintFullPack(
      {
        pack_id: "waiver-noreason",
        lint_waivers: [{ rule: "L1", qid: "m1" }],
        questions: [IDENTITY_MATCH],
      },
      "waiver-noreason"
    );
    expect(r.violations.filter((v) => v.rule === "L1")).toHaveLength(0); // suppressed
    const hygiene = r.violations.filter((v) => v.rule === "WAIVER");
    expect(hygiene).toHaveLength(1);
    expect(hygiene[0].detail).toContain("reason");
  });

  test("a malformed (non-object) waiver entry is flagged and suppresses nothing", () => {
    // The common mistake: a bare string instead of {rule, reason}. It must NOT
    // silently vanish — emit a WAIVER warning AND leave the real finding live.
    const r = lintFullPack(
      {
        pack_id: "waiver-malformed",
        lint_waivers: ["L1"],
        questions: [IDENTITY_MATCH],
      },
      "waiver-malformed"
    );
    const hygiene = r.violations.filter((v) => v.rule === "WAIVER");
    expect(hygiene).toHaveLength(1);
    expect(hygiene[0].detail).toContain("not an object");
    expect(r.violations.filter((v) => v.rule === "L1")).toHaveLength(1); // not suppressed
    expect(r.waived || []).toHaveLength(0);
  });
});

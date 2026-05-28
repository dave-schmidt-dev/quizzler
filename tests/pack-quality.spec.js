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

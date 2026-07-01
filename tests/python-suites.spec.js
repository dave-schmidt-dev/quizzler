// @ts-check
//
// Bridges the standalone Python unittest suites into the main `npm test`
// (Playwright) gate so they can't silently rot. test_build_manifest.py is not
// otherwise run by CI; when the Layer-A lint gate was wired into build_manifest
// it crashed every fixture build and nobody noticed because this suite lived
// outside the runner. Running it here closes that gap.

const { test, expect } = require("@playwright/test");
const { spawnSync } = require("node:child_process");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..");

/** Run a Python unittest module from the project root; return {code, output}.
 *  spawnSync (no shell, arg array — no injection surface) returns both streams
 *  on success and failure; unittest writes its "OK"/"FAILED" summary to stderr,
 *  so combine them. */
function runUnittest(moduleName) {
  const r = spawnSync("python3", ["-m", "unittest", moduleName, "-v"], {
    cwd: ROOT,
    encoding: "utf8",
  });
  return { code: r.status ?? 1, output: `${r.stdout || ""}${r.stderr || ""}` };
}

test.describe("Python unittest suites (run under the main gate)", () => {
  for (const mod of [
    "tests.test_build_manifest",
    "tests.test_factcheck_pack",
    "tests.test_lint_hook",
    "tests.test_lint_packs",
    "tests.test_verify_pack",
    "tests.test_start_sh",
  ]) {
    test(`${mod.replace(".", "/")}.py passes`, () => {
      const { code, output } = runUnittest(mod);
      expect(output, output).toContain("OK");
      expect(code, output).toBe(0);
    });
  }
});

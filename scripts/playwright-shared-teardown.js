// @ts-check
var fs = require("fs");
var os = require("os");
var path = require("path");

var STATE_FILE = path.join(os.tmpdir(), "quizzler-shared-state.json");

async function globalTeardown() {
  var state = null;
  try {
    state = JSON.parse(fs.readFileSync(STATE_FILE, "utf-8"));
  } catch (_) {}

  if (state && state.serverPid) {
    try { process.kill(state.serverPid, "SIGTERM"); } catch (_) {}
  }

  var tmpDir = state && state.tmpDir ? state.tmpDir : process.env.QUIZZLER_TMP_DIR;
  if (tmpDir) {
    try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch (_) {}
  }

  try { fs.unlinkSync(STATE_FILE); } catch (_) {}
}

module.exports = globalTeardown;

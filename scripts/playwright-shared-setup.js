// @ts-check
var { execSync, spawn } = require("child_process");
var fs = require("fs");
var http = require("http");
var os = require("os");
var path = require("path");

var STATE_FILE = path.join(os.tmpdir(), "quizzler-shared-state.json");

function getFreePortSync() {
  var result = execSync(
    'python3 -c "import socket; s=socket.socket(); s.bind((\'127.0.0.1\',0)); print(s.getsockname()[1]); s.close()"'
  )
    .toString()
    .trim();
  return parseInt(result, 10);
}

function waitForHealthz(baseURL, timeoutMs) {
  var deadline = Date.now() + timeoutMs;
  return new Promise(function (resolve, reject) {
    function poll() {
      if (Date.now() > deadline) {
        return reject(new Error("Server did not become healthy within " + timeoutMs + "ms"));
      }
      var url = baseURL.replace(/\/$/, "") + "/healthz";
      var req = http.get(url, function (res) {
        res.resume();
        if (res.statusCode === 200) return resolve();
        setTimeout(poll, 300);
      });
      req.on("error", function () { setTimeout(poll, 300); });
      req.setTimeout(2000, function () { req.destroy(); });
    }
    poll();
  });
}

async function globalSetup() {
  try { fs.unlinkSync(STATE_FILE); } catch (_) {}

  var root = path.resolve(__dirname, "..");
  var port = getFreePortSync();
  var tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "quizzler-shared-"));
  var dataDir = path.join(tmpDir, "data");
  var logDir = path.join(tmpDir, "logs");
  fs.mkdirSync(dataDir, { recursive: true });
  fs.mkdirSync(logDir, { recursive: true });

  var baseURL = "http://127.0.0.1:" + port;

  var serveScript = path.join(root, "scripts", "serve.py");
  var args = [
    serveScript,
    String(port),
    ".",
    "--shared-progress",
    "--data-dir", dataDir,
    "--log-dir", logDir,
    "--app-root", "app",
    "--packs-root", "question-packs",
  ];

  var server = spawn("python3", args, {
    cwd: root,
    stdio: "pipe",
    detached: false,
  });

  server.stderr.on("data", function (chunk) {
    process.stderr.write(chunk);
  });
  server.stdout.on("data", function () {});

  server.on("error", function (err) {
    console.error("[quizzler-shared-setup] server spawn error:", err.message);
  });

  await waitForHealthz(baseURL, 15000);

  var state = {
    port: port,
    baseURL: baseURL,
    tmpDir: tmpDir,
    dataDir: dataDir,
    logDir: logDir,
    serverPid: server.pid,
  };
  fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2));

  process.env.QUIZZLER_REAL_SERVER = "1";
  process.env.QUIZZLER_TMP_DIR = tmpDir;

  console.log("[quizzler-shared-setup] server ready on " + baseURL);
}

module.exports = globalSetup;

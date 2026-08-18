// Single source of truth for the browser gate's fixed port.
//
// The port stays fixed (not `reuseExistingServer`) so a stale server can never
// serve the suite, but it lives here rather than being repeated across the
// config and the spec fallbacks: a host-level collision should be a one-line
// change, not a hunt through five files. 8787 was moved off on 2026-08-18
// because a supervised daemon on the development host holds it.
const PORT = 8799;

module.exports = {
  PORT,
  baseURL: `http://localhost:${PORT}`,
  loopbackURL: `http://127.0.0.1:${PORT}`,
};

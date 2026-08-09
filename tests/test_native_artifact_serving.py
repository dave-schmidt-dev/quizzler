"""Negative LAN-serving contract for native and release artifacts."""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("serve", ROOT / "scripts/serve.py")
serve = importlib.util.module_from_spec(spec)
spec.loader.exec_module(serve)


class NativeArtifactServingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        (base / "app").mkdir()
        (base / "packs").mkdir()
        for name in ("index.html", "progress-store.js", "shared-progress.js", "Native.swift", "project.yml", "release-config.toml", "APP_SETUP_CHECKLIST.md"):
            (base / "app" / name).write_text(name, encoding="utf-8")
        (base / "packs" / "manifest.json").write_text("{}", encoding="utf-8")
        (base / "packs" / "pack.json").write_text("{}", encoding="utf-8")
        self.routes = {"/app/": str(base / "app"), "/question-packs/": str(base / "packs")}

    def tearDown(self):
        self.temp.cleanup()

    def test_browser_assets_are_reachable(self):
        self.assertIsNotNone(serve.resolve_static_path("/app/index.html", self.routes))
        self.assertIsNotNone(serve.resolve_static_path("/app/shared-progress.js", self.routes))
        self.assertIsNotNone(serve.resolve_static_path("/question-packs/manifest.json", self.routes))

    def test_native_and_release_artifacts_are_unreachable(self):
        for name in ("Native.swift", "project.yml", "release-config.toml", "APP_SETUP_CHECKLIST.md"):
            self.assertIsNone(serve.resolve_static_path(f"/app/{name}", self.routes))

    def test_non_json_pack_artifacts_are_unreachable(self):
        base = Path(self.temp.name) / "packs"
        (base / "evidence.md").write_text("private", encoding="utf-8")
        self.assertIsNone(serve.resolve_static_path("/question-packs/evidence.md", self.routes))

    def test_allowlist_is_explicit(self):
        self.assertEqual(serve.BROWSER_APP_ASSETS, {"index.html", "progress-store.js", "shared-progress.js"})


if __name__ == "__main__":
    unittest.main()

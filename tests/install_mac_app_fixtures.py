"""Shared test fixtures and base test case for install_mac_app tests."""

from __future__ import annotations

import importlib.util
import io
import os
import plistlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Callable

# Load install_mac_app from its file path using importlib
SCRIPT_PATH = Path(__file__).resolve().parents[1] / "app" / "scripts" / "install_mac_app.py"
_spec = importlib.util.spec_from_file_location("install_mac_app", SCRIPT_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load module spec from {SCRIPT_PATH}")
install_mac_app = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(install_mac_app)

BUNDLE_ID = install_mac_app.BUNDLE_ID
ICLOUD_CONTAINER = install_mac_app.ICLOUD_CONTAINER


def plant_bundle(
    path: Path,
    identifier: str | None = None,
    version: str = "1.0",
    build: str = "1",
) -> Path:
    """Plant a synthetic app bundle with Info.plist."""
    contents = path / "Contents"
    macos = contents / "MacOS"
    macos.mkdir(parents=True, exist_ok=True)
    if identifier is not None:
        plist_data = {
            "CFBundleIdentifier": identifier,
            "CFBundleShortVersionString": version,
            "CFBundleVersion": build,
        }
        with open(contents / "Info.plist", "wb") as fp:
            plistlib.dump(plist_data, fp)
    return path


class FakeRunner:
    """Fake command runner for hermetic test execution."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.timeouts: list[float | None] = []
        self.build_returncode: int = 0
        self.build_output: str = "Build succeeded\n** BUILD SUCCEEDED **\n"
        self.verify_product_returncode: int = 0
        self.verify_target_returncode: int = 0
        self.entitlements_returncode: int = 0
        self.entitlements_xml: str = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0">\n'
            '<dict>\n'
            '    <key>com.apple.developer.icloud-container-identifiers</key>\n'
            '    <array>\n'
            f'        <string>{ICLOUD_CONTAINER}</string>\n'
            '    </array>\n'
            '</dict>\n'
            '</plist>'
        )
        self.ps_outputs: list[str] = []
        self.default_ps_output: str = ""
        self.lsregister_dump_outputs: list[str] = []
        self.default_lsregister_dump_output: str = ""
        self.git_describe_output: str = "v1.0.0-1-gabcdef-dirty\n"
        self.ditto_callback: Callable[[list[str]], None] | None = None
        self.ditto_returncode: int = 0
        self.xcodebuild_callback: Callable[[list[str]], None] | None = None
        # Raise subprocess.TimeoutExpired for any call this predicate accepts.
        self.timeout_when: Callable[[list[str]], bool] | None = None
        # When set, the streaming build reports one heartbeat at this elapsed time.
        self.heartbeat_at: float | None = None

    def _fake_build(self, argv: list[str], cwd: Path | str | None) -> None:
        if self.xcodebuild_callback:
            self.xcodebuild_callback(argv)
        elif self.build_returncode == 0 and cwd:
            product_dir = (
                Path(cwd)
                / ".build"
                / "DerivedData"
                / "Build"
                / "Products"
                / "Debug-maccatalyst"
                / "QuizzleriOS.app"
            )
            plant_bundle(product_dir, identifier=BUNDLE_ID, version="2.0.0", build="100")

    def run_streaming(
        self,
        argv: list[str],
        *,
        log_path: Path,
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        heartbeat: Callable[[float], None] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Synchronous stand-in for the streaming build: no process, no waiting."""
        self.commands.append(argv)
        self.timeouts.append(timeout)
        if self.timeout_when is not None and self.timeout_when(argv):
            raise subprocess.TimeoutExpired(argv, timeout or 0)
        if self.heartbeat_at is not None and heartbeat is not None:
            heartbeat(self.heartbeat_at)
        self._fake_build(argv, cwd)
        Path(log_path).write_text(self.build_output, encoding="utf-8")
        return subprocess.CompletedProcess(argv, returncode=self.build_returncode)

    def run(
        self,
        argv: list[str],
        *,
        check: bool = False,
        capture_output: bool = True,
        text: bool = True,
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(argv)
        self.timeouts.append(timeout)
        if self.timeout_when is not None and self.timeout_when(argv):
            raise subprocess.TimeoutExpired(argv, timeout or 0)
        tool = argv[0]

        if tool.endswith("/xcb"):
            self._fake_build(argv, cwd)
            return subprocess.CompletedProcess(
                argv,
                returncode=self.build_returncode,
                stdout=self.build_output,
                stderr="",
            )

        if tool == "codesign":
            if "--entitlements" in argv:
                return subprocess.CompletedProcess(
                    argv,
                    returncode=self.entitlements_returncode,
                    stdout=self.entitlements_xml,
                    stderr="",
                )
            # verify
            target_path = argv[-1]
            if "QuizzleriOS.app" in target_path:
                rc = self.verify_product_returncode
            else:
                rc = self.verify_target_returncode
            return subprocess.CompletedProcess(argv, returncode=rc, stdout="", stderr="")

        if tool == "osascript":
            return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")

        if tool == "ps":
            out = self.ps_outputs.pop(0) if self.ps_outputs else self.default_ps_output
            return subprocess.CompletedProcess(argv, returncode=0, stdout=out, stderr="")

        if tool == "ditto":
            if self.ditto_returncode != 0:
                # A failed copy leaves nothing behind in this fake.
                return subprocess.CompletedProcess(
                    argv, returncode=self.ditto_returncode, stdout="", stderr="ditto: failed"
                )
            if self.ditto_callback:
                self.ditto_callback(argv)
            else:
                src = Path(argv[1])
                dst = Path(argv[2])
                if dst.exists():
                    shutil.rmtree(dst)
                if src.is_dir():
                    shutil.copytree(src, dst)
            return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")

        if tool.endswith("lsregister"):
            if "-dump" in argv:
                out = (
                    self.lsregister_dump_outputs.pop(0)
                    if self.lsregister_dump_outputs
                    else self.default_lsregister_dump_output
                )
                return subprocess.CompletedProcess(argv, returncode=0, stdout=out, stderr="")
            # -u or -f
            return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")

        if tool == "git":
            return subprocess.CompletedProcess(
                argv,
                returncode=0,
                stdout=self.git_describe_output,
                stderr="",
            )

        return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")


class InstallMacAppTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="quizzler-install-test-")).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _replace_fixture(self, old_version: str | None = "1.0") -> tuple[Path, Path, Path, FakeRunner]:
        repo = self.tmp_dir / "repo"
        dest = self.tmp_dir / "dest"
        dest.mkdir()
        plant_bundle(
            repo / ".build" / "DerivedData" / "Build" / "Products" / "Debug-maccatalyst" / "QuizzleriOS.app",
            identifier=BUNDLE_ID,
            version="2.0.0",
            build="100",
        )
        target = dest / "Quizzler.app"
        if old_version is not None:
            plant_bundle(target, identifier=BUNDLE_ID, version=old_version, build="1")
        runner = FakeRunner()
        runner.default_lsregister_dump_output = f"""--------------------------------------------------------------------------------
path:                 {target}
identifier:           {BUNDLE_ID}
--------------------------------------------------------------------------------"""
        return repo, dest, target, runner

    def _install(self, repo: Path, dest: Path, runner: FakeRunner) -> tuple[int, str, str]:
        stderr_buf = io.StringIO()
        stdout_buf = io.StringIO()
        with redirect_stdout(stdout_buf):
            ret = install_mac_app.install(
                destination=dest,
                repo_root=repo,
                runner=runner,
                skip_build=True,
                sleep_fn=lambda _: None,
                kill_fn=lambda _pid, _sig: None,
                stderr=stderr_buf,
            )
        return ret, stdout_buf.getvalue(), stderr_buf.getvalue()

    @staticmethod
    def _version(app: Path) -> str:
        with open(app / "Contents" / "Info.plist", "rb") as fp:
            return str(plistlib.load(fp)["CFBundleShortVersionString"])
